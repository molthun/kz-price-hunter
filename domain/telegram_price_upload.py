"""Telegram-команда администратора `/price <магазин>` для загрузки прайсов и скриншотов (v6.47.0).

Позволяет администратору отправить в Telegram-бота:
  - фото/скриншот прайса (или файл-изображение) с подписью `/price Мобильник`
    (и опциональным текстом поста/комментариев со следующей строки);
  - либо текстовое сообщение `/price Мобильник\\n<строки прайса>`.

Поток строго двухшаговый, как в веб-админке (`domain/seller_vision_upload.py`):
  1. Распознавание (`extract_seller_price_payload`, без записи в БД) + сохранение
     временного черновика в `PriceUploadDraftStore` с привязкой к `admin_user_id`.
  2. Подтверждение кнопкой «✅ Опубликовать» (`pricepub:<draft_id>`) — атомарное
     извлечение черновика, повторная серверная валидация и запись через
     `publish_seller_vision_items`.
"""

from __future__ import annotations

import html
import sqlite3
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from domain.models import Seller
from domain.seller_vision_upload import (
    MAX_VISION_UPLOAD_BYTES,
    VisionUploadError,
    validate_image_payload,
)
from repositories.seller_repo import SellerRepository

DRAFT_TTL_SECONDS = 1800  # 30 минут
MAX_DRAFTS = 64
MAX_PREVIEW_ITEMS = 15


def parse_price_command_args(raw_text: str) -> Tuple[str, str]:
    """Разбирает `/price <магазин>[\\n<текст прайса>]` или `/price <магазин> | <текст>`.

    Возвращает `(seller_query, caption_text)`.
    """
    text = str(raw_text or "").strip()
    if not text:
        return "", ""

    lines = text.splitlines()
    first_line = lines[0].strip()
    rest_lines = "\n".join(lines[1:]).strip()

    parts = first_line.split(maxsplit=1)
    first_arg = parts[1].strip() if len(parts) > 1 else ""

    if "|" in first_arg:
        seller_part, inline_text = first_arg.split("|", 1)
        seller_query = seller_part.strip()
        extra_chunks = [c for c in (inline_text.strip(), rest_lines) if c]
        return seller_query, "\n".join(extra_chunks)

    return first_arg, rest_lines


def extract_photo_file_id(msg: Dict[str, Any]) -> Optional[str]:
    """Извлекает `file_id` крупнейшего фото или документа-изображения из сообщения Telegram."""
    if not isinstance(msg, dict):
        return None
    photos = msg.get("photo")
    if isinstance(photos, list) and photos:
        best = max(
            (p for p in photos if isinstance(p, dict) and p.get("file_id")),
            key=lambda p: int(p.get("file_size") or 0) or (int(p.get("width") or 0) * int(p.get("height") or 0)),
            default=None,
        )
        if best:
            return str(best["file_id"])
    doc = msg.get("document")
    if isinstance(doc, dict) and doc.get("file_id"):
        mime = str(doc.get("mime_type") or "").lower()
        if mime in ("image/jpeg", "image/png", "image/webp"):
            return str(doc["file_id"])
    return None


def find_seller_for_price_upload(
    conn: sqlite3.Connection,
    query: str,
) -> Tuple[Optional[Seller], List[Seller]]:
    """Находит активного продавца по id, slug или названию.

    Возвращает `(matched_seller, candidates)`:
      - `(seller, [seller])` при однозначном совпадении;
      - `(None, [s1, s2, ...])` при неоднозначном совпадении;
      - `(None, [])` если ничего не найдено.
    """
    needle = str(query or "").strip()
    if not needle:
        return None, []

    repo = SellerRepository(conn)
    direct = repo.get_by_id_or_slug(needle)
    if direct and direct.is_active:
        return direct, [direct]

    needle_cf = needle.casefold()
    sellers = repo.list_sellers(active_only=True)

    exact = [
        s for s in sellers
        if (s.slug or "").casefold() == needle_cf
        or (s.name or "").casefold() == needle_cf
        or (s.legal_name or "").casefold() == needle_cf
    ]
    if len(exact) == 1:
        return exact[0], exact
    if len(exact) > 1:
        return None, exact[:8]

    partial = [
        s for s in sellers
        if needle_cf in (s.name or "").casefold()
        or needle_cf in (s.slug or "").casefold()
        or needle_cf in (s.legal_name or "").casefold()
    ]
    if len(partial) == 1:
        return partial[0], partial
    return None, partial[:8]


class PriceUploadDraftStore:
    """Ограниченное in-memory хранилище черновиков загрузки прайса с TTL и проверкой владельца."""

    def __init__(self, *, max_items: int = MAX_DRAFTS, ttl_seconds: int = DRAFT_TTL_SECONDS):
        self._max_items = max(1, int(max_items))
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._drafts: Dict[str, Dict[str, Any]] = {}

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl_seconds
        expired = [k for k, v in self._drafts.items() if float(v.get("created_at") or 0) < cutoff]
        for k in expired:
            self._drafts.pop(k, None)

    def create(
        self,
        *,
        admin_user_id: int,
        seller_id: str,
        seller_name: str,
        city: str,
        publishable: List[Dict[str, Any]],
        rejected_count: int = 0,
        now: Optional[float] = None,
    ) -> str:
        ts = time.monotonic() if now is None else float(now)
        self._evict_expired(ts)
        while len(self._drafts) >= self._max_items:
            oldest_key = min(self._drafts, key=lambda k: float(self._drafts[k].get("created_at") or 0))
            self._drafts.pop(oldest_key, None)

        draft_id = uuid.uuid4().hex[:12]
        self._drafts[draft_id] = {
            "draft_id": draft_id,
            "admin_user_id": int(admin_user_id),
            "seller_id": str(seller_id),
            "seller_name": str(seller_name),
            "city": str(city),
            "publishable": [dict(item) for item in publishable],
            "rejected_count": int(rejected_count),
            "created_at": ts,
        }
        return draft_id

    def pop_for_admin(
        self,
        draft_id: str,
        admin_user_id: int,
        *,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Атомарно извлекает черновик, только если он не истёк и принадлежит этому админу."""
        ts = time.monotonic() if now is None else float(now)
        self._evict_expired(ts)
        draft = self._drafts.get(str(draft_id or "").strip())
        if not draft:
            return None
        if int(draft.get("admin_user_id") or 0) != int(admin_user_id):
            return None
        return self._drafts.pop(draft["draft_id"], None)

    def clear(self) -> None:
        self._drafts.clear()


PRICE_UPLOAD_DRAFTS = PriceUploadDraftStore()


async def download_telegram_image(
    session: aiohttp.ClientSession,
    token: str,
    file_id: str,
    *,
    max_bytes: int = MAX_VISION_UPLOAD_BYTES,
) -> bytes:
    """Скачивает изображение из Telegram Bot API с проверкой размера и magic bytes."""
    if not token or not file_id:
        raise VisionUploadError("Не удалось получить файл изображения из Telegram.")

    get_file_url = f"https://api.telegram.org/bot{token}/getFile"
    async with session.get(
        get_file_url,
        params={"file_id": str(file_id)},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status != 200:
            raise VisionUploadError(f"Telegram getFile вернул HTTP {resp.status}.")
        meta = await resp.json()

    if not isinstance(meta, dict) or not meta.get("ok"):
        raise VisionUploadError("Telegram не вернул метаданные файла.")

    result = meta.get("result") or {}
    file_path = str(result.get("file_path") or "").strip().lstrip("/")
    if not file_path or ".." in file_path:
        raise VisionUploadError("Некорректный путь файла в ответе Telegram.")

    declared_size = int(result.get("file_size") or 0)
    if declared_size > max_bytes:
        raise VisionUploadError("Размер изображения превышает 10 МБ.")

    download_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    chunks: List[bytes] = []
    total = 0
    async with session.get(download_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
        if resp.status != 200:
            raise VisionUploadError(f"Не удалось скачать изображение из Telegram (HTTP {resp.status}).")
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > max_bytes:
                raise VisionUploadError("Размер изображения превышает 10 МБ.")
            chunks.append(chunk)

    raw = b"".join(chunks)
    validate_image_payload(raw)
    return raw


def _fmt_kzt(value: Any) -> str:
    try:
        return f"{int(round(float(value))):,}".replace(",", " ") + " ₸"
    except (TypeError, ValueError):
        return str(value or "—")


def format_price_preview_html(
    *,
    seller_name: str,
    city: str,
    publishable: List[Dict[str, Any]],
    rejected: List[Dict[str, Any]],
    max_shown: int = MAX_PREVIEW_ITEMS,
) -> str:
    """Формирует безопасное HTML-превью распознанного прайса для Telegram."""
    safe_seller = html.escape(str(seller_name or "Магазин"))
    safe_city = html.escape(str(city or "Алматы"))
    pub_count = len(publishable)
    rej_count = len(rejected)

    lines = [
        f"🧾 <b>Прайс для «{safe_seller}»</b> ({safe_city})",
        f"Готово к публикации: <b>{pub_count}</b> · Отклонено: <b>{rej_count}</b>",
    ]

    if publishable:
        lines.append("")
        for item in publishable[:max_shown]:
            title = html.escape(str(item.get("raw_title") or item.get("title") or "Без названия")[:90])
            price_str = _fmt_kzt(item.get("price"))
            old_price = item.get("old_price")
            if old_price and float(old_price or 0) > float(item.get("price") or 0):
                price_part = f"<b>{price_str}</b> <s>{_fmt_kzt(old_price)}</s>"
            else:
                price_part = f"<b>{price_str}</b>"
            lines.append(f"• {title} — {price_part}")
        if pub_count > max_shown:
            lines.append(f"<i>… и ещё {pub_count - max_shown} поз.</i>")
    else:
        lines.append("")
        lines.append("⚠️ Не удалось распознать ни одной позиции с ценой в KZT.")
        if rejected:
            reasons = []
            for item in rejected[:3]:
                lbl = html.escape(str(item.get("rejection_label") or item.get("rejection_reason") or "проверка"))
                reasons.append(lbl)
            lines.append("Причины: " + "; ".join(reasons))

    return "\n".join(lines)


def format_publish_result_html(
    *,
    seller_name: str,
    city: str,
    published_count: int,
    skipped_count: int,
) -> str:
    safe_seller = html.escape(str(seller_name or "Магазин"))
    safe_city = html.escape(str(city or "Алматы"))
    text = (
        f"✅ <b>Прайс опубликован: «{safe_seller}»</b> ({safe_city})\n"
        f"Сохранено предложений: <b>{int(published_count)}</b>"
    )
    if skipped_count:
        text += f"\nПропущено при повторной проверке: <b>{int(skipped_count)}</b>"
    return text
