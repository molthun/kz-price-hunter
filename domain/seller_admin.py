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
from domain.seller_identity import IdentityType, normalize_bin, normalize_domain, normalize_phone
from domain.seller_onboarding import _clean_social_handle, marketplace_channel
from repositories.channel_repo import ChannelRepository
from repositories.seller_identity_repo import SellerIdentityRepository
from repositories.seller_repo import SellerRepository
from repositories.social_source_repo import SocialSourceRepository

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


def _sync_channels(
    channels: ChannelRepository,
    seller: Seller,
    channel_type: str,
    wanted: List[Dict[str, str]],
    social_repo: Optional[SocialSourceRepository] = None,
) -> None:
    """Приводит каналы одного типа к списку: новые создаёт, убранные отключает, возвращённые включает."""
    kept = set()
    for item in wanted:
        channel = channels.get_or_create(
            seller_id=seller.id, channel_type=channel_type, name=item["name"],
            external_store_id=item["handle"], base_url=item["url"],
        )
        channels.update_channel(channel.id, {"name": item["name"], "base_url": item["url"], "is_active": True})
        kept.add(channel.id)
        if social_repo and channel_type in (ChannelType.INSTAGRAM, ChannelType.TELEGRAM):
            platform = "instagram" if channel_type == ChannelType.INSTAGRAM else "telegram"
            raw_h = (item.get("handle") or "").lstrip("@")
            if raw_h:
                social_repo.add_source(
                    platform=platform,
                    handle=raw_h,
                    url=item.get("url") or "",
                    seller_id=seller.id,
                    channel_id=channel.id,
                    enabled=seller.is_active,
                )
    for channel in channels.list_by_seller(seller.id, active_only=True):
        if channel.channel_type == channel_type and channel.id not in kept:
            channels.delete_channel(channel.id)
            if social_repo and channel_type in (ChannelType.INSTAGRAM, ChannelType.TELEGRAM):
                soc = social_repo.get_by_channel_id(channel.id)
                if soc:
                    social_repo.set_enabled(soc.id, False)


def save_seller(conn, seller_id: Optional[str], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Создаёт магазин (seller_id=None) или обновляет. None — такого магазина нет."""
    sellers = SellerRepository(conn)
    channels = ChannelRepository(conn)
    social_repo = SocialSourceRepository(conn)
    existing = sellers.get_by_id(seller_id) if seller_id else None
    if seller_id and not existing:
        return None
    form = clean_form(body, is_new=existing is None)

    if existing:
        seller = existing
    else:
        new_id, slug = _new_seller_id(sellers, form["name"])
        seller = Seller(id=new_id, slug=slug, name=form["name"])
    old_identity = {IdentityType.DOMAIN: seller.domain, IdentityType.PHONE: seller.phone,
                    IdentityType.BIN: seller.bin, IdentityType.LEGAL_NAME: seller.legal_name}
    seller.name = form["name"]
    seller.legal_name = form["legal_name"]
    seller.bin = form["bin"]
    seller.phone = form["phone"]
    seller.is_active = form["is_active"]
    seller.domain = normalize_domain(form["website_url"]) if form["website_url"] else None
    metadata = dict(seller.metadata or {})
    for field in ("city", "website_url", "whatsapp", "contact_person", "comment"):
        metadata[field] = form[field]
    seller.metadata = metadata
    sellers.save_or_update(seller)
    sellers.set_identity_fields(seller)
    identities = SellerIdentityRepository(conn)
    new_identity = {IdentityType.DOMAIN: seller.domain, IdentityType.PHONE: seller.phone,
                    IdentityType.BIN: seller.bin, IdentityType.LEGAL_NAME: seller.legal_name}
    for identity_type, old_value in old_identity.items():
        if old_value and old_value != new_identity[identity_type]:
            identities.remove_identity_value(seller.id, identity_type, old_value)
    identities.sync_identities_from_seller(seller, source="admin_editor")

    _sync_channels(channels, seller, ChannelType.MARKETPLACE,
                   [marketplace_channel(url, seller.name) for url in form["marketplace_links"]])
    for channel_type, handle, site in ((ChannelType.INSTAGRAM, form["instagram"], "instagram.com"),
                                       (ChannelType.TELEGRAM, form["telegram"], "t.me")):
        _sync_channels(channels, seller, channel_type, [{
            "name": f"{seller.name} ({channel_type})", "handle": f"@{handle}", "url": f"https://{site}/{handle}",
        }] if handle else [], social_repo=social_repo)
    if form["website_url"] and not existing:
        channels.get_or_create(seller_id=seller.id, channel_type=ChannelType.WEBSITE,
                               name=f"{seller.name} (сайт)", base_url=form["website_url"])

    # Синхронизация активности источников соцсетей со статусом продавца
    if not seller.is_active:
        for s_src in social_repo.list_by_seller_id(seller.id):
            social_repo.set_enabled(s_src.id, False)
    elif existing and not existing.is_active and seller.is_active:
        for s_src in social_repo.list_by_seller_id(seller.id):
            social_repo.set_enabled(s_src.id, True)

    conn.commit()
    return seller_details(conn, seller.id)


def seller_details(conn, seller_id: str) -> Optional[Dict[str, Any]]:
    """Данные для формы редактора: те же поля, что принимает save_seller."""
    seller = SellerRepository(conn).get_by_id(seller_id)
    if not seller:
        return None
    meta = seller.metadata or {}
    all_channels = ChannelRepository(conn).list_by_seller(seller_id, active_only=False)
    active_channels = [c for c in all_channels if c.is_active]
    social_repo = SocialSourceRepository(conn)

    def handle_of(channel_type: str) -> str:
        found = next((c for c in active_channels if c.channel_type == channel_type), None)
        return (found.external_store_id or "").lstrip("@") if found else ""

    def site() -> str:
        website = next((c for c in active_channels if c.channel_type == ChannelType.WEBSITE and c.base_url), None)
        if "website_url" in meta:  # после очистки поля адрес не возвращается из домена
            return meta["website_url"] or ""
        if seller.domain:
            return f"https://{seller.domain}"
        return website.base_url if website else ""

    channel_items = []
    for c in all_channels:
        status_label = "Активен"
        last_fetch = None
        last_status = None
        last_error = None
        if c.channel_type in (ChannelType.INSTAGRAM, ChannelType.TELEGRAM):
            soc = social_repo.get_by_channel_id(c.id)
            if soc:
                last_fetch = soc.last_fetch_at
                last_status = soc.last_status
                last_error = soc.last_error
                status_label = "В сборе" if (soc.enabled and c.is_active) else "Отключён"
            else:
                status_label = "В сборе" if c.is_active else "Отключён"
        elif c.channel_type == ChannelType.MARKETPLACE:
            status_label = "Нужен сканер витрины" if c.is_active else "Отключён"
        elif c.channel_type == ChannelType.WEBSITE:
            url_l = (c.base_url or "").lower()
            if "xml" in url_l or "yml" in url_l or "feed" in url_l:
                status_label = "Нужен сканер фидов" if c.is_active else "Отключён"
            else:
                status_label = "Нужен сканер" if c.is_active else "Отключён"
        elif c.channel_type in (ChannelType.WHATSAPP, ChannelType.DIRECT):
            status_label = "Контакт"

        channel_items.append({
            "id": c.id,
            "channel_type": c.channel_type,
            "name": c.name,
            "base_url": c.base_url,
            "external_store_id": c.external_store_id,
            "is_active": c.is_active,
            "status_label": status_label,
            "last_fetch_at": last_fetch,
            "last_status": last_status,
            "last_error": last_error,
        })

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
        "marketplace_links": [c.base_url for c in active_channels if c.channel_type == ChannelType.MARKETPLACE and c.base_url],
        "collection_channels": [c.name for c in active_channels if c.channel_type not in _EDITED_CHANNEL_TYPES],
        "channels": channel_items,
        "is_active": seller.is_active,
    }
