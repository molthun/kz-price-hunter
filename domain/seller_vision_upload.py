"""Загрузка прайс-листов и скриншотов продавца через Vision ИИ (v6.43.0).

Предназначен для магазинов, которые публикуют цены картинками (Instagram сторис/посты,
WhatsApp-прайсы и т. п.) и принимают заказы в мессенджерах.

Поток состоит из двух явных шагов:
  1. `extract_seller_price_image`: проверяет файл (размер, magic bytes JPEG/PNG/WEBP),
     отправляет в `VisionExtractionEngine.extract_from_image` (ai_router: локальная
     `qwen3-vl:8b-instruct` или настроенный fallback), делит позиции на готовые к
     публикации (`publishable`) и отклонённые (`rejected`). В БД ничего не пишет.
  2. `publish_seller_vision_items`: повторно валидирует подтверждённые администратором
     позиции на сервере через `VisionExtractionEngine.extract_from_structured`, выбирает
     канал продавца и ссылку заказа (приоритет: явная ссылка -> WhatsApp `wa.me` ->
     канал продавца) и сохраняет предложения через `SocialOfferPipeline`.
"""

from __future__ import annotations

import datetime
import re
import sqlite3
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from domain.models import Channel, ChannelType, Condition, Seller, utc_now_iso
from domain.seller_identity import normalize_phone
from domain.social_content import (
    ContentObservation,
    ContentType,
    SocialContentExtractor,
)
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import (
    MIN_VISION_PUBLISH_CONFIDENCE,
    VisionCandidate,
    VisionExtractionEngine,
)
from model_matching import extract_canonical_key
from repositories.channel_repo import ChannelRepository
from repositories.seller_repo import SellerRepository

MAX_VISION_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 МБ
MIN_VISION_UPLOAD_BYTES = 16
MAX_CAPTION_TEXT_CHARS = 12000
MAX_PUBLISH_ITEMS = 200
DEFAULT_EXPIRES_DAYS = 7
MAX_EXPIRES_DAYS = 90

_CHANNEL_PRIORITY = (
    ChannelType.WHATSAPP,
    ChannelType.INSTAGRAM,
    ChannelType.TELEGRAM,
    ChannelType.DIRECT,
    ChannelType.WEBSITE,
    ChannelType.MARKETPLACE,
)

_REJECTION_LABELS = {
    "occluded_price": "Цена закрыта или нечитаема",
    "price_not_determined": "Цена не определена",
    "non_kzt_currency": "Валюта не в тенге (KZT)",
    "low_confidence": "Низкая уверенность распознавания",
    "missing_title": "Не распознано название товара",
    "invalid_price": "Некорректная цена",
    "price_equals_memory_size": "Цена совпадает с объёмом памяти/размером",
}


class VisionUploadError(ValueError):
    """Ошибка валидации входных данных при загрузке прайса через Vision."""


def detect_image_mime(image_bytes: bytes) -> Optional[str]:
    """Определяет MIME-тип изображения по magic bytes (JPEG, PNG, WEBP)."""
    if not image_bytes or len(image_bytes) < 12:
        return None
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_image_payload(image_bytes: bytes) -> str:
    """Проверяет размер и сигнатуру файла изображения; возвращает доверенный MIME."""
    if not isinstance(image_bytes, (bytes, bytearray)) or len(image_bytes) < MIN_VISION_UPLOAD_BYTES:
        raise VisionUploadError("Файл изображения пустой или повреждён.")
    raw = bytes(image_bytes)
    if len(raw) > MAX_VISION_UPLOAD_BYTES:
        raise VisionUploadError("Размер изображения превышает 10 МБ.")
    mime = detect_image_mime(raw)
    if not mime:
        raise VisionUploadError("Поддерживаются только изображения JPEG, PNG или WEBP.")
    return mime


def _rejection_label(reason: Optional[str]) -> str:
    if not reason:
        return "Позиция не прошла проверку"
    if reason in _REJECTION_LABELS:
        return _REJECTION_LABELS[reason]
    if reason.startswith("non_kzt_currency"):
        return "Валюта не в тенге (KZT)"
    if reason.startswith("low_confidence"):
        return "Низкая уверенность распознавания"
    return reason


def _serialize_candidate(cand: VisionCandidate, *, source: str = "image") -> Dict[str, Any]:
    data = cand.to_dict()
    data["source"] = source
    data["source_label"] = "Текст" if source == "text" else "Фото"
    data["rejection_label"] = None if cand.publishable else _rejection_label(cand.rejection_reason)
    return data


def _clean_http_url(raw: Optional[str]) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    url = text if "://" in text else f"https://{text}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in (parsed.hostname or ""):
        raise VisionUploadError(f"Некорректная ссылка для заказа: {text}")
    return url


def _whatsapp_url_for_seller(seller: Seller, active_channels: List[Channel]) -> Optional[str]:
    wa_channel = next(
        (c for c in active_channels if c.channel_type == ChannelType.WHATSAPP and c.base_url),
        None,
    )
    if wa_channel and wa_channel.base_url:
        return wa_channel.base_url
    meta = seller.metadata if isinstance(seller.metadata, dict) else {}
    phone_raw = meta.get("whatsapp") or seller.phone
    norm = normalize_phone(phone_raw) if phone_raw else None
    if norm:
        digits = re.sub(r"\D+", "", norm)
        if len(digits) >= 10:
            return f"https://wa.me/{digits}"
    return None


def resolve_seller_upload_channel(
    conn: sqlite3.Connection,
    seller_id: str,
    *,
    channel_id: Optional[str] = None,
    source_url: Optional[str] = None,
) -> Tuple[Seller, Channel, str]:
    """Подбирает продавца, канал публикации и кликабельную ссылку заказа."""
    sellers = SellerRepository(conn)
    channels = ChannelRepository(conn)
    seller = sellers.get_by_id(seller_id)
    if not seller:
        raise LookupError("Магазин не найден")

    explicit_url = _clean_http_url(source_url) if source_url else None
    all_channels = channels.list_by_seller(seller_id, active_only=False)
    active_channels = [c for c in all_channels if c.is_active]

    selected: Optional[Channel] = None
    if channel_id:
        selected = channels.get_by_id(channel_id)
        if not selected or selected.seller_id != seller_id:
            raise VisionUploadError("Выбранный канал не принадлежит этому магазину.")
    else:
        for ch_type in _CHANNEL_PRIORITY:
            selected = next((c for c in active_channels if c.channel_type == ch_type), None)
            if selected:
                break
        if not selected and all_channels:
            selected = all_channels[0]

    wa_url = _whatsapp_url_for_seller(seller, active_channels)
    if not selected:
        if wa_url:
            selected = channels.get_or_create(
                seller_id=seller.id,
                channel_type=ChannelType.WHATSAPP,
                name=f"{seller.name} (WhatsApp)",
                base_url=wa_url,
            )
        else:
            selected = channels.get_or_create(
                seller_id=seller.id,
                channel_type=ChannelType.DIRECT,
                name=f"{seller.name} (прайс-лист)",
                base_url=explicit_url,
            )

    order_url = explicit_url or ""
    if not order_url:
        if selected.channel_type == ChannelType.WHATSAPP and (selected.base_url or wa_url):
            order_url = selected.base_url or wa_url or ""
        elif not channel_id and wa_url and selected.channel_type in (
            ChannelType.INSTAGRAM,
            ChannelType.TELEGRAM,
            ChannelType.DIRECT,
        ):
            order_url = wa_url
        elif selected.base_url:
            order_url = selected.base_url
        elif wa_url:
            order_url = wa_url

    return seller, selected, order_url


def _candidate_dedup_key(item: Dict[str, Any]) -> Tuple[str, Optional[float]]:
    title = str(item.get("raw_title") or "").strip()
    canon = item.get("canonical_key") or extract_canonical_key(title) or title.lower()
    price = item.get("price")
    price_key = round(float(price), 2) if price is not None else None
    return (str(canon).lower(), price_key)


def _extract_text_candidates(
    caption_text: str,
    *,
    default_city: str,
    min_confidence: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Извлекает позиции из текста описания поста и/или комментариев продавца."""
    clean_text = str(caption_text or "").strip()[:MAX_CAPTION_TEXT_CHARS]
    if not clean_text:
        return [], []

    publishable: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen_pub: set = set()

    # 1. Детерминированный разбор соцсетевого текста (маскирует телефоны и память, определяет condition и city)
    obs = ContentObservation(
        content_type=ContentType.POST,
        text=clean_text,
    )
    for oc in SocialContentExtractor.extract_candidates(obs):
        cand = VisionCandidate(
            raw_title=oc.raw_title,
            price=float(oc.price),
            old_price=float(oc.old_price) if oc.old_price and oc.old_price > oc.price else None,
            currency=getattr(oc, "currency", None) or "KZT",
            condition=oc.condition or Condition.NEW,
            city=oc.city if oc.city and oc.city != "Казахстан" else default_city,
            confidence=0.92,
            price_determined=True,
            publishable=True,
            is_discount=bool(oc.old_price and oc.old_price > oc.price),
            is_unit_price=bool(getattr(oc, "is_unit_price", False)),
            quantity=getattr(oc, "quantity", None),
            unit=getattr(oc, "unit", None),
            size_or_volume=getattr(oc, "size_or_volume", None),
            canonical_key=extract_canonical_key(oc.raw_title),
            raw_snippet=oc.raw_title[:200],
        )
        validated = VisionExtractionEngine.validate_candidate(cand, min_confidence=min_confidence)
        ser = _serialize_candidate(validated, source="text")
        if validated.publishable:
            key = _candidate_dedup_key(ser)
            if key not in seen_pub:
                seen_pub.add(key)
                publishable.append(ser)
        else:
            rejected.append(ser)

    # 2. OCR/текстовый разбор VisionExtractionEngine (дополнительно ловит зачёркнутые цены и отклонённые валюты RUB/USD)
    for vc in VisionExtractionEngine.extract_from_ocr_text(
        clean_text,
        default_city=default_city,
        min_confidence=min_confidence,
    ):
        ser = _serialize_candidate(vc, source="text")
        if vc.publishable:
            key = _candidate_dedup_key(ser)
            if key not in seen_pub:
                seen_pub.add(key)
                publishable.append(ser)
        else:
            rejected.append(ser)

    return publishable, rejected


async def extract_seller_price_payload(
    image_bytes: Optional[bytes] = None,
    caption_text: str = "",
    *,
    default_city: str = "Алматы",
    min_confidence: float = MIN_VISION_PUBLISH_CONFIDENCE,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Распознаёт позиции прайс-листа из изображения и/или текста описания/комментариев без записи в БД."""
    has_image = bool(image_bytes)
    clean_text = str(caption_text or "").strip()[:MAX_CAPTION_TEXT_CHARS]
    if not has_image and not clean_text:
        raise VisionUploadError("Загрузите изображение прайса или вставьте текст описания/комментариев.")

    city = str(default_city or "Алматы").strip()[:80] or "Алматы"
    mime: Optional[str] = None
    publishable: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen_pub: set = set()

    if has_image:
        mime = validate_image_payload(image_bytes or b"")
        img_candidates = await VisionExtractionEngine.extract_from_image(
            bytes(image_bytes or b""),
            mime_type=mime,
            default_city=city,
            min_confidence=min_confidence,
            config=config,
        )
        for cand in img_candidates:
            ser = _serialize_candidate(cand, source="image")
            if cand.publishable:
                key = _candidate_dedup_key(ser)
                if key not in seen_pub:
                    seen_pub.add(key)
                    publishable.append(ser)
            else:
                rejected.append(ser)

    if clean_text:
        txt_pub, txt_rej = _extract_text_candidates(
            clean_text,
            default_city=city,
            min_confidence=min_confidence,
        )
        for item in txt_pub:
            key = _candidate_dedup_key(item)
            if key not in seen_pub:
                seen_pub.add(key)
                publishable.append(item)
        rejected.extend(txt_rej)

    return {
        "mime_type": mime,
        "has_image": has_image,
        "has_text": bool(clean_text),
        "city": city,
        "publishable": publishable,
        "rejected": rejected,
        "total": len(publishable) + len(rejected),
    }


async def extract_seller_price_image(
    image_bytes: bytes,
    *,
    default_city: str = "Алматы",
    min_confidence: float = MIN_VISION_PUBLISH_CONFIDENCE,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Распознаёт позиции прайс-листа на изображении без записи в БД (обёртка совместимости)."""
    validate_image_payload(image_bytes)
    return await extract_seller_price_payload(
        image_bytes=image_bytes,
        caption_text="",
        default_city=default_city,
        min_confidence=min_confidence,
        config=config,
    )


def publish_seller_vision_items(
    conn: sqlite3.Connection,
    seller_id: str,
    items: Iterable[Dict[str, Any]],
    *,
    channel_id: Optional[str] = None,
    city: Optional[str] = None,
    source_url: Optional[str] = None,
    expires_days: int = DEFAULT_EXPIRES_DAYS,
    min_confidence: float = MIN_VISION_PUBLISH_CONFIDENCE,
) -> Dict[str, Any]:
    """Повторно валидирует подтверждённые позиции и сохраняет их как предложения продавца."""
    raw_items = list(items or [])
    if not raw_items:
        raise VisionUploadError("Нет выбранных позиций для публикации.")
    if len(raw_items) > MAX_PUBLISH_ITEMS:
        raise VisionUploadError(f"За один раз можно опубликовать не более {MAX_PUBLISH_ITEMS} позиций.")

    try:
        ttl_days = int(expires_days)
    except (TypeError, ValueError):
        ttl_days = DEFAULT_EXPIRES_DAYS
    ttl_days = max(1, min(ttl_days, MAX_EXPIRES_DAYS))

    seller, channel, order_url = resolve_seller_upload_channel(
        conn,
        seller_id,
        channel_id=channel_id,
        source_url=source_url,
    )
    meta = seller.metadata if isinstance(seller.metadata, dict) else {}
    resolved_city = str(city or meta.get("city") or "Алматы").strip()[:80] or "Алматы"

    # Серверная повторная валидация: клиент не может обойти порог уверенности, валюту или проверку цены
    normalized_input: List[Dict[str, Any]] = []
    conditions_by_idx: List[str] = []
    allowed_conditions = {Condition.NEW, Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX}
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("raw_title") or item.get("title") or "").strip()
        cond = str(item.get("condition") or Condition.NEW)
        if cond not in allowed_conditions:
            cond = Condition.NEW
        conditions_by_idx.append(cond)
        normalized_input.append({
            "title": title,
            "price": item.get("price"),
            "old_price": item.get("old_price"),
            "currency": item.get("currency") or "KZT",
            "city": str(item.get("city") or resolved_city).strip()[:80] or resolved_city,
            "confidence": item.get("confidence", 0.9),
            "is_price_occluded": bool(item.get("is_price_occluded", False)),
            "is_unit_price": bool(item.get("is_unit_price", False)),
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "size_or_volume": item.get("size_or_volume"),
            "raw_snippet": str(item.get("raw_snippet") or "")[:300],
        })

    validated = VisionExtractionEngine.extract_from_structured(
        normalized_input,
        default_city=resolved_city,
        min_confidence=min_confidence,
    )
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    published_at = utc_now_iso()
    expires_at = (now_dt + datetime.timedelta(days=ttl_days)).isoformat()

    offer_candidates = []
    rejected_count = 0
    for idx, vc in enumerate(validated):
        if idx < len(conditions_by_idx):
            vc.condition = conditions_by_idx[idx]
        oc = vc.to_offer_candidate(expires_at=expires_at)
        if oc is not None:
            offer_candidates.append(oc)
        else:
            rejected_count += 1

    if not offer_candidates:
        raise VisionUploadError("Ни одна из выбранных позиций не прошла проверку цены и валюты (KZT).")

    observation = ContentObservation(
        seller_id=seller.id,
        channel_id=channel.id,
        content_type=ContentType.POST,
        post_id=f"vision_upload_{uuid.uuid4().hex[:12]}",
        text="",
        published_at=published_at,
        observed_at=published_at,
        expires_at=expires_at,
        raw_metadata={
            "source": "admin_vision_upload",
            "post_url": order_url,
            "platform": channel.channel_type,
        },
    )

    pipeline = SocialOfferPipeline(conn)
    saved_offers = pipeline.process_candidates(observation, offer_candidates)
    if conn.in_transaction:
        conn.commit()
    return {
        "seller_id": seller.id,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "order_url": order_url,
        "city": resolved_city,
        "published_count": len(saved_offers),
        "skipped_count": rejected_count,
        "offers": [
            {
                "id": o.id,
                "product_id": o.product_id,
                "price": o.price,
                "old_price": o.old_price,
                "city": o.city,
                "url": o.url,
            }
            for o in saved_offers
        ],
    }
