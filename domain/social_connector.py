"""Коннекторы Telegram и Instagram (S2-E06, KZ Price Hunter 2.0).

Слой отвечает только за получение и детерминированный разбор публичного контента:
Telegram — публичная веб-превью канала (t.me/s/<handle>), Instagram — Graph API
с бизнес-токеном. Сохранение наблюдений и извлечение предложений — отдельный сервис
social_ingest.py; здесь нет обращений к БД.

Правила этапа:
- контент соцсетей — последний по предпочтению источник каталога (после feed/YML/
  структурированного сайта), поэтому ошибка источника не должна ронять остальные;
- доступ только публичный: никаких логинов, паролей и обхода авторизации;
- токен Instagram берётся из окружения и никогда не попадает в тексты ошибок/логи;
- один пост = один наблюдение с детерминированным id: повторный сбор обновляет
  запись, а не плодит дубликаты.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from html_soup import make_soup
from domain.models import utc_now_iso
from domain.social_content import ContentObservation, ContentType


class SocialPlatform:
    TELEGRAM = "telegram"
    INSTAGRAM = "instagram"
    ALL = {TELEGRAM, INSTAGRAM}


class ConnectorError(Exception):
    """Базовая ошибка коннектора: источник недоступен или ответ не разобран."""


class ConnectorUnavailable(ConnectorError):
    """Источник закрыт/не найден/нет доступа: повторять немедленно бессмысленно."""


class ConnectorRateLimited(ConnectorError):
    """Источник ответил 429/cooldown: нужно подождать retry_after секунд."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class FetchedPost:
    """Один пост/сторис, полученный коннектором до сохранения в БД."""

    post_id: str
    text: str = ""
    media_urls: List[str] = field(default_factory=list)
    published_at: Optional[str] = None
    content_type: str = ContentType.POST
    platform: str = ""
    channel_handle: str = ""
    post_url: Optional[str] = None


@dataclass
class SocialSource:
    """Настроенный источник контента: канал Telegram или профиль Instagram."""

    id: str
    platform: str
    handle: str
    url: str = ""
    seller_id: str = ""
    channel_id: str = ""
    enabled: bool = True
    last_fetch_at: Optional[str] = None
    last_status: str = "PENDING"
    last_error: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "platform": self.platform,
            "handle": self.handle,
            "url": self.url,
            "seller_id": self.seller_id,
            "channel_id": self.channel_id,
            "enabled": self.enabled,
            "last_fetch_at": self.last_fetch_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> "SocialSource":
        return cls(
            id=row["id"],
            platform=row["platform"],
            handle=row["handle"],
            url=row.get("url") or "",
            seller_id=row.get("seller_id") or "",
            channel_id=row.get("channel_id") or "",
            enabled=bool(row.get("enabled", 1)),
            last_fetch_at=row.get("last_fetch_at"),
            last_status=row.get("last_status") or "PENDING",
            last_error=row.get("last_error") or "",
            created_at=row.get("created_at") or utc_now_iso(),
            updated_at=row.get("updated_at") or utc_now_iso(),
        )


def source_id(platform: str, handle: str) -> str:
    """Детерминированный id источника: повторное добавление того же канала не создаёт дубль."""
    digest = hashlib.sha1(f"{platform}|{handle}".encode("utf-8")).hexdigest()[:12]
    return f"src_{platform[:2]}_{digest}"


def observation_id(platform: str, handle: str, post_id: str) -> str:
    """Детерминированный id наблюдения: повторный сбор обновляет пост, а не дублирует его."""
    digest = hashlib.sha1(f"{platform}|{handle}|{post_id}".encode("utf-8")).hexdigest()[:16]
    return f"cobs_{platform[:2]}_{digest}"


def normalize_handle(platform: str, raw: str) -> str:
    """Приводит @handle, URL или числовой Instagram ID к стабильному виду."""
    value = (raw or "").strip()
    if not value:
        raise ValueError("пустой handle источника")
    if "://" in value or value.startswith("t.me/") or value.startswith("www."):
        parsed = urlparse(value if "://" in value else f"https://{value}")
        path = [part for part in parsed.path.split("/") if part and part != "s"]
        value = path[-1] if path else parsed.netloc.split(".")[0]
    value = value.lstrip("@").strip().lower()
    if platform == SocialPlatform.TELEGRAM:
        if not re.fullmatch(r"[a-z0-9_]{2,64}", value):
            raise ValueError(f"некорректный Telegram handle: {raw!r}")
        return value
    if platform == SocialPlatform.INSTAGRAM:
        if not re.fullmatch(r"[a-z0-9._]{2,64}", value):
            raise ValueError(f"некорректный Instagram handle: {raw!r}")
        return value
    raise ValueError(f"неизвестная платформа: {platform!r}")


def telegram_url(handle: str, before: Optional[int] = None) -> str:
    """Публичная веб-превью канала: работает без авторизации и показывает последние посты."""
    suffix = f"?before={int(before)}" if before else ""
    return f"https://t.me/s/{handle}{suffix}"


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}


def _response_status(response: Any) -> int:
    status = getattr(response, "status_code", None)
    if status is None:
        status = getattr(response, "status", 200)
    return int(status)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", "")
    if callable(text):
        text = text()
    return text or ""


def _retry_after(response: Any) -> Optional[float]:
    headers = getattr(response, "headers", None) or {}
    try:
        return float(headers.get("Retry-After") or headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def _normalize_timestamp(value: Any) -> Optional[str]:
    """ISO-время из Graph API («+0000», «Z») — к виду, который понимает fromisoformat."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})([+-])(\d{2})(\d{2})", text)
    if match:
        return f"{match.group(1)}{match.group(2)}{match.group(3)}:{match.group(4)}"
    return text


class TelegramChannelConnector:
    """Публичный веб-превью канала Telegram: посты, фото, время публикации."""

    platform = SocialPlatform.TELEGRAM

    def __init__(self, http_get: Optional[Callable[..., Any]] = None):
        self._http_get = http_get

    def _get(self, url: str, **kwargs: Any) -> Any:
        if self._http_get is None:
            from scrapers import http as requests
            return requests.get(url, **kwargs)
        return self._http_get(url, **kwargs)

    def fetch(self, handle: str, before: Optional[int] = None) -> List[FetchedPost]:
        handle = normalize_handle(self.platform, handle)
        response = self._get(telegram_url(handle, before), headers=DEFAULT_HEADERS, timeout=20)
        status = _response_status(response)
        if status == 429:
            raise ConnectorRateLimited("Telegram: слишком много запросов", _retry_after(response))
        if status in (401, 403, 404):
            raise ConnectorUnavailable(f"Telegram: канал недоступен (HTTP {status})")
        if status >= 400:
            raise ConnectorUnavailable(f"Telegram: ошибка источника (HTTP {status})")
        return self.parse_posts(_response_text(response), handle)

    def parse_posts(self, html: str, handle: str) -> List[FetchedPost]:
        """Разбор страницы t.me/s/<handle>. Пустая страница — пустой список, не ошибка."""
        soup = make_soup(html or "")
        posts: List[FetchedPost] = []
        seen = set()
        for node in soup.select("div.tgme_widget_message"):
            data_post = node.get("data-post") or ""
            if "/" not in data_post:
                continue
            post_id = data_post.split("/", 1)[1].strip()
            if not post_id or post_id in seen:
                continue
            text_node = node.select_one(".tgme_widget_message_text")
            text = text_node.get_text("\n").strip() if text_node else ""
            media_urls: List[str] = []
            for photo in node.select(".tgme_widget_message_photo_wrap"):
                match = re.search(r"url\((?:'|\")?(.*?)(?:'|\")?\)", photo.get("style") or "")
                if match:
                    media_urls.append(match.group(1).strip())
            for video in node.select(".tgme_widget_message_video_player video"):
                if video.get("src"):
                    media_urls.append(video["src"])
            if not text and not media_urls:
                continue  # служебное сообщение (подписка, закрепление, без контента)
            time_node = node.select_one("time[datetime]")
            seen.add(post_id)
            posts.append(FetchedPost(
                post_id=post_id,
                text=text,
                media_urls=media_urls,
                published_at=_normalize_timestamp(time_node.get("datetime")) if time_node else None,
                content_type=ContentType.POST,
                platform=self.platform,
                channel_handle=handle,
                post_url=f"https://t.me/{handle}/{post_id}",
            ))
        return posts


class InstagramConnector:
    """Instagram Graph API: медиа и сторис бизнес-аккаунта по числовому ID.

    Публичного способа читать Instagram без токена нет, поэтому:
    - нужен INSTAGRAM_ACCESS_TOKEN (или переданный токен) и числовой Business ID;
    - без токена коннектор явно сообщает «нет доступа», а не изображает пустой каталог.
    """

    platform = SocialPlatform.INSTAGRAM

    def __init__(self, http_get: Optional[Callable[..., Any]] = None,
                 access_token: Optional[str] = None,
                 base_url: str = "https://graph.facebook.com/v21.0"):
        self._http_get = http_get
        self._access_token = access_token
        self.base_url = base_url

    @property
    def access_token(self) -> str:
        return self._access_token if self._access_token is not None else os.environ.get(
            "INSTAGRAM_ACCESS_TOKEN", "")

    def _get(self, url: str, **kwargs: Any) -> Any:
        if self._http_get is None:
            from scrapers import http as requests
            return requests.get(url, **kwargs)
        return self._http_get(url, **kwargs)

    def fetch_media(self, handle: str, limit: int = 25) -> List[FetchedPost]:
        return self._fetch_edge(handle, "media", ContentType.POST, limit)

    def fetch_stories(self, handle: str, limit: int = 25) -> List[FetchedPost]:
        return self._fetch_edge(handle, "stories", ContentType.STORY, limit)

    def _fetch_edge(self, handle: str, edge: str, content_type: str, limit: int) -> List[FetchedPost]:
        if not self.access_token:
            raise ConnectorUnavailable("Instagram: не задан INSTAGRAM_ACCESS_TOKEN")
        user_id = normalize_handle(self.platform, handle)
        if not user_id.isdigit():
            raise ConnectorUnavailable(
                "Instagram: нужен числовой Business ID аккаунта (Graph API не ищет по @handle)")
        fields = "id,caption,media_url,media_type,timestamp,permalink"
        url = (f"{self.base_url}/{user_id}/{edge}"
               f"?fields={fields}&limit={int(limit)}&access_token={self.access_token}")
        response = self._get(url, headers=DEFAULT_HEADERS, timeout=20)
        status = _response_status(response)
        if status == 429:
            raise ConnectorRateLimited("Instagram: лимит Graph API", _retry_after(response))
        if status in (400, 401, 403, 404):
            raise ConnectorUnavailable(f"Instagram: нет доступа к {edge} (HTTP {status})")
        if status >= 400:
            raise ConnectorUnavailable(f"Instagram: ошибка источника (HTTP {status})")
        payload_text = _response_text(response)
        try:
            payload = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError as exc:
            raise ConnectorUnavailable("Instagram: ответ не является JSON") from exc
        return self.parse_media(payload, user_id, content_type=content_type)

    def parse_media(self, payload: Any, handle: str,
                    content_type: str = ContentType.POST) -> List[FetchedPost]:
        """Разбор ответа Graph API (data: []), в том числе карусели и сторис."""
        items = payload.get("data") if isinstance(payload, dict) else payload
        posts: List[FetchedPost] = []
        seen = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            post_id = str(item.get("id") or "").strip()
            if not post_id or post_id in seen:
                continue
            text = (item.get("caption") or "").strip()
            media_urls: List[str] = []
            if item.get("media_url"):
                media_urls.append(item["media_url"])
            children = item.get("children")
            if isinstance(children, dict):
                for child in children.get("data") or []:
                    if isinstance(child, dict) and child.get("media_url"):
                        media_urls.append(child["media_url"])
            if not text and not media_urls:
                continue
            seen.add(post_id)
            posts.append(FetchedPost(
                post_id=post_id,
                text=text,
                media_urls=media_urls,
                published_at=_normalize_timestamp(item.get("timestamp")),
                content_type=content_type,
                platform=self.platform,
                channel_handle=handle,
                post_url=item.get("permalink"),
            ))
        return posts


def build_connector(platform: str, http_get: Optional[Callable[..., Any]] = None):
    """Создаёт коннектор по платформе источника."""
    if platform == SocialPlatform.TELEGRAM:
        return TelegramChannelConnector(http_get=http_get)
    if platform == SocialPlatform.INSTAGRAM:
        return InstagramConnector(http_get=http_get)
    raise ValueError(f"неизвестная платформа: {platform!r}")


def posts_to_observations(posts: List[FetchedPost], source: SocialSource) -> List[ContentObservation]:
    """Превращает посты коннектора в ContentObservation с детерминированными id.

    observed_at — момент сбора: для сторис от него отсчитывается TTL 24 часа
    (ContentObservation.__post_init__), поэтому повторный сбор продлевает жизнь сторис,
    а старые записи удаляет retention.
    """
    now = utc_now_iso()
    observations: List[ContentObservation] = []
    for post in posts:
        platform = post.platform or source.platform
        handle = post.channel_handle or source.handle
        observations.append(ContentObservation(
            id=observation_id(platform, handle, post.post_id),
            seller_id=source.seller_id,
            channel_id=source.channel_id,
            content_type=post.content_type,
            post_id=post.post_id,
            text=post.text,
            media_urls=list(post.media_urls),
            published_at=post.published_at,
            observed_at=now,
            raw_metadata={
                "platform": platform,
                "handle": handle,
                "post_url": post.post_url or (f"https://t.me/{handle}/{post.post_id}"
                                              if platform == SocialPlatform.TELEGRAM else ""),
                "source_id": source.id,
            },
        ))
    return observations
