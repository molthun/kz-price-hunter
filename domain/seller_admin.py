"""Редактор магазинов для администратора: создание и правка карточки продавца и его каналов.

Сохранение меняет только данные магазина. Сканирование и склейка здесь не запускаются, предложения
и товары не трогаются. Каналы сбора (сайт, Kaspi, Halyk и т. п.) принадлежат парсерам и не отключаются.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from domain.models import Channel, ChannelType, Seller
from domain.seller_identity import normalize_bin, normalize_domain, normalize_phone
from domain.seller_onboarding import _clean_social_handle, marketplace_channel
from repositories.channel_repo import ChannelRepository
from repositories.seller_identity_repo import SellerIdentityRepository
from repositories.seller_repo import SellerRepository

_EDITED_CHANNEL_TYPES = (ChannelType.MARKETPLACE, ChannelType.INSTAGRAM, ChannelType.TELEGRAM)
MAX_MARKETPLACE_LINKS = 50


class SellerFormError(ValueError):
    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _text(value: Any, limit: int = 300) -> Optional[str]:
    return str(value).strip()[:limit] or None if value is not None else None


def _web_url(raw: str) -> Optional[str]:
    """Ссылка http(s) с адресом сайта или None: в карточке она станет кликабельной."""
    url = raw if "://" in raw else f"https://{raw}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in (parsed.hostname or ""):
        return None
    return url


def _marketplace_url(url: str) -> str:
    """Страница продавца Kaspi открывается только по адресу /shop/m/<номер>/products/."""
    found = re.search(r"kaspi\.kz/shop/(?:info/merchant|m)/([^/?#]+)", url, re.IGNORECASE)
    return f"https://kaspi.kz/shop/m/{found.group(1)}/products/" if found else url


def clean_form(body: Dict[str, Any], is_new: bool) -> Dict[str, Any]:
    errors: List[str] = []
    name = _text(body.get("name")) or ""
    if len(name) < 2:
        errors.append("Укажите название магазина (не менее 2 символов).")

    bin_raw = _text(body.get("bin"))
    bin_value = normalize_bin(bin_raw) if bin_raw else None
    if bin_raw and not bin_value:
        errors.append("БИН/ИИН должен содержать ровно 12 цифр.")

    website = None
    website_raw = _text(body.get("website_url"), 500)
    if website_raw:
        website = _web_url(website_raw)
        if not website:
            errors.append(f"Некорректный адрес сайта: {website_raw}")

    raw_links = body.get("marketplace_links") or []
    if not isinstance(raw_links, list):
        raw_links = [raw_links]
    links: List[str] = []
    for raw in raw_links:
        value = _text(raw, 500)
        if not value:
            continue
        url = _web_url(value)
        if not url:
            errors.append(f"Некорректная ссылка на маркетплейс: {value}")
        elif _marketplace_url(url) not in links:
            links.append(_marketplace_url(url))
    if len(links) > MAX_MARKETPLACE_LINKS:
        errors.append(f"Не больше {MAX_MARKETPLACE_LINKS} ссылок на маркетплейсы.")

    phone_raw = _text(body.get("phone"), 40)
    instagram = _clean_social_handle(_text(body.get("instagram")), "instagram")
    telegram = _clean_social_handle(_text(body.get("telegram")), "telegram")
    if is_new and not any((website, links, phone_raw, instagram, telegram)):
        errors.append("Укажите хотя бы один канал или контакт магазина.")
    if errors:
        raise SellerFormError(errors)
    return {
        "name": name,
        "legal_name": _text(body.get("legal_name")),
        "bin": bin_value,
        "phone": normalize_phone(phone_raw) or phone_raw,
        "city": _text(body.get("city"), 80) or "Алматы",
        "website_url": website,
        "marketplace_links": links,
        "instagram": instagram,
        "telegram": telegram,
        "whatsapp": _text(body.get("whatsapp"), 40),
        "contact_person": _text(body.get("contact_person")),
        "comment": _text(body.get("comment"), 1000),
        "is_active": bool(body.get("is_active", True)),
    }


def _new_seller_id(repo: SellerRepository, name: str) -> tuple:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    slug = base if len(base) >= 3 else f"shop-{uuid.uuid4().hex[:8]}"
    while repo.get_by_slug(slug) or repo.get_by_id(f"seller_{slug}"):
        slug = f"{base or 'shop'}-{uuid.uuid4().hex[:6]}"
    return f"seller_{slug}", slug


def _sync_channels(channels: ChannelRepository, seller: Seller, channel_type: str, wanted: List[Dict[str, str]]) -> None:
    """Приводит каналы одного типа к списку: новые создаёт, убранные отключает, возвращённые включает."""
    kept = set()
    for item in wanted:
        channel = channels.get_or_create(
            seller_id=seller.id, channel_type=channel_type, name=item["name"],
            external_store_id=item["handle"], base_url=item["url"],
        )
        channels.update_channel(channel.id, {"name": item["name"], "base_url": item["url"], "is_active": True})
        kept.add(channel.id)
    for channel in channels.list_by_seller(seller.id, active_only=True):
        if channel.channel_type == channel_type and channel.id not in kept:
            channels.delete_channel(channel.id)


def save_seller(conn, seller_id: Optional[str], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Создаёт магазин (seller_id=None) или обновляет. None — такого магазина нет."""
    sellers = SellerRepository(conn)
    channels = ChannelRepository(conn)
    existing = sellers.get_by_id(seller_id) if seller_id else None
    if seller_id and not existing:
        return None
    form = clean_form(body, is_new=existing is None)

    if existing:
        seller = existing
    else:
        new_id, slug = _new_seller_id(sellers, form["name"])
        seller = Seller(id=new_id, slug=slug, name=form["name"])
    seller.name = form["name"]
    seller.legal_name = form["legal_name"]
    seller.bin = form["bin"]
    seller.phone = form["phone"]
    seller.is_active = form["is_active"]
    if form["website_url"]:
        seller.domain = normalize_domain(form["website_url"]) or seller.domain
    metadata = dict(seller.metadata or {})
    for field in ("city", "website_url", "whatsapp", "contact_person", "comment"):
        metadata[field] = form[field]
    seller.metadata = metadata
    sellers.save_or_update(seller)
    SellerIdentityRepository(conn).sync_identities_from_seller(seller, source="admin_editor")

    _sync_channels(channels, seller, ChannelType.MARKETPLACE,
                   [marketplace_channel(url, seller.name) for url in form["marketplace_links"]])
    for channel_type, handle, site in ((ChannelType.INSTAGRAM, form["instagram"], "instagram.com"),
                                       (ChannelType.TELEGRAM, form["telegram"], "t.me")):
        _sync_channels(channels, seller, channel_type, [{
            "name": f"{seller.name} ({channel_type})", "handle": f"@{handle}", "url": f"https://{site}/{handle}",
        }] if handle else [])
    if form["website_url"] and not existing:
        channels.get_or_create(seller_id=seller.id, channel_type=ChannelType.WEBSITE,
                               name=f"{seller.name} (сайт)", base_url=form["website_url"])
    conn.commit()
    return seller_details(conn, seller.id)


def seller_details(conn, seller_id: str) -> Optional[Dict[str, Any]]:
    """Данные для формы редактора: те же поля, что принимает save_seller."""
    seller = SellerRepository(conn).get_by_id(seller_id)
    if not seller:
        return None
    meta = seller.metadata or {}
    channels = ChannelRepository(conn).list_by_seller(seller_id, active_only=True)

    def handle_of(channel_type: str) -> str:
        found = next((c for c in channels if c.channel_type == channel_type), None)
        return (found.external_store_id or "").lstrip("@") if found else ""

    def site() -> str:
        website = next((c for c in channels if c.channel_type == ChannelType.WEBSITE and c.base_url), None)
        if "website_url" in meta:  # после очистки поля адрес не возвращается из домена
            return meta["website_url"] or ""
        if seller.domain:
            return f"https://{seller.domain}"
        return website.base_url if website else ""

    return {
        "id": seller.id,
        "slug": seller.slug,
        "name": seller.name,
        "legal_name": seller.legal_name or "",
        "bin": seller.bin or "",
        "phone": seller.phone or "",
        "city": meta.get("city") or "Алматы",
        "website_url": site(),
        "whatsapp": meta.get("whatsapp") or "",
        "contact_person": meta.get("contact_person") or "",
        "comment": meta.get("comment") or "",
        "instagram": handle_of(ChannelType.INSTAGRAM),
        "telegram": handle_of(ChannelType.TELEGRAM),
        "marketplace_links": [c.base_url for c in channels if c.channel_type == ChannelType.MARKETPLACE and c.base_url],
        "collection_channels": [c.name for c in channels if c.channel_type not in _EDITED_CHANNEL_TYPES],
        "is_active": seller.is_active,
    }
