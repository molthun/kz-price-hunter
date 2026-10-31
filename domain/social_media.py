"""Контролируемая загрузка фотографий постов перед Vision (FA69-02).

Адрес приходит из чужого контента, поэтому каждый шаг проверяется: схема и порт, публичность адреса
в момент соединения (DNS-ответ проверяет сам резолвер, повторный DNS-запрос его не обойдёт), каждое
перенаправление, тип и размер ответа, настоящее содержимое файла. Перед моделью картинка уменьшается.

В журнал и отчёт попадает только код ошибки: ссылки CDN содержат подписи доступа.
"""

from __future__ import annotations

import asyncio
import io
import ipaddress
import socket
import ssl
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp
import certifi
from aiohttp.abc import AbstractResolver
from aiohttp.resolver import DefaultResolver

MAX_IMAGES_PER_POST = 3
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_SIDE = 1600
MAX_REDIRECTS = 3
FETCH_TIMEOUT_SEC = 15
ALLOWED_SCHEMES = {"https"}
ALLOWED_PORTS = {443}
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}


class MediaError(Exception):
    """Фото не получено; code — короткий код без адреса."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class FetchedImage:
    data: bytes
    mime_type: str = "image/jpeg"


@dataclass
class MediaReport:
    """Итог обработки фото за проход; отделён от статуса текстового сбора."""

    attempted: int = 0
    fetched: int = 0
    fetch_failed: int = 0
    model_errors: int = 0
    codes: Counter = field(default_factory=Counter)

    def failed(self, code: str) -> None:
        self.fetch_failed += 1
        self.codes[code] += 1

    def model_failed(self, code: str) -> None:
        self.model_errors += 1
        self.codes[f"model:{code}"] += 1

    def summary(self) -> str:
        if not self.codes:
            return ""
        details = ", ".join(f"{code}×{count}" for code, count in sorted(self.codes.items()))
        return f"MEDIA: фото {self.fetched}/{self.attempted}, ошибки: {details}"


def ip_allowed(host: str) -> bool:
    """Только публичные адреса: внутренняя сеть, localhost и метаданные облака недоступны по ссылке из поста."""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return address.is_global and not address.is_multicast


class PublicOnlyResolver(AbstractResolver):
    """Проверяет адреса в момент соединения: один непубличный адрес в ответе отменяет всё соединение."""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> List[Dict[str, Any]]:
        answers = await self._resolver.resolve(host, port, family)
        if not answers or not all(ip_allowed(a["host"]) for a in answers):
            raise OSError("media_private_address")
        return answers

    async def close(self) -> None:
        await self._resolver.close()


def check_url(url: str) -> None:
    try:
        parts = urlsplit(str(url))
        port = parts.port
    except ValueError:
        raise MediaError("bad_url") from None
    if parts.scheme.lower() not in ALLOWED_SCHEMES or not parts.hostname:
        raise MediaError("bad_url")
    if parts.username or parts.password:
        raise MediaError("bad_url")
    if port is not None and port not in ALLOWED_PORTS:
        raise MediaError("bad_port")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return                                      # имя проверит резолвер при соединении
    if not ip_allowed(parts.hostname):
        raise MediaError("private_address")


def prepare_image(raw: bytes) -> FetchedImage:
    """Проверяет настоящий формат и размер изображения, уменьшает его и пересохраняет в JPEG."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ALLOWED_FORMATS:
                raise MediaError("unsupported_format")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise MediaError("too_many_pixels")
            image.load()
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            out = io.BytesIO()
            image.convert("RGB").save(out, format="JPEG", quality=85)
            return FetchedImage(out.getvalue())
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise MediaError("bad_image") from None


async def _download(url: str) -> bytes:
    connector = aiohttp.TCPConnector(resolver=PublicOnlyResolver(),
                                     ssl=ssl.create_default_context(cafile=certifi.where()))
    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SEC)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            check_url(current)
            async with session.get(current, allow_redirects=False) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise MediaError("bad_redirect")
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise MediaError(f"http_{response.status}")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise MediaError("unsupported_format")
                if (response.content_length or 0) > MAX_IMAGE_BYTES:
                    raise MediaError("too_large")
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_IMAGE_BYTES:
                        raise MediaError("too_large")
                return bytes(body)
        raise MediaError("too_many_redirects")


async def fetch_image(url: str) -> FetchedImage:
    """Скачивает и готовит фото для модели. Любой сбой — MediaError с кодом, без адреса."""
    try:
        raw = await _download(url)
    except MediaError:
        raise
    except asyncio.TimeoutError:
        raise MediaError("timeout") from None
    except (aiohttp.ClientError, OSError):
        raise MediaError("network") from None
    return await asyncio.to_thread(prepare_image, raw)
