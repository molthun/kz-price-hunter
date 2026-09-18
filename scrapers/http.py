"""Общий исходящий HTTP для всех магазинов: один бюджет запросов на домен.

Модуль повторяет интерфейс `curl_cffi.requests` (get/post/request/Session), поэтому
адаптеры подключают его как `from scrapers import http as requests`. Каталожный обход,
live-поиск и загрузка описаний проходят через один лимитер и общий cooldown по 429.
"""
import datetime
import random
import threading
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlsplit

import certifi
from curl_cffi import requests as _curl
from curl_cffi.requests import Response, exceptions  # noqa: F401 — совместимость с `curl_cffi.requests`
from curl_cffi.requests.exceptions import *  # noqa: F401,F403

# Минимальные ограничения (решение владельца 18.09.2026): скорость обхода как до лимитера —
# паузы между страницами задают сами адаптеры, здесь только общий потолок одновременных
# запросов на домен (4 = потоки карточек iSpace) и общая пауза по 429/Retry-After.
# Не лимит, согласованный с магазинами; ужесточается этими константами.
MAX_CONCURRENCY_PER_HOST = 4
MIN_INTERVAL_SECONDS = 0.0
JITTER_SECONDS = 0.0
DEFAULT_COOLDOWN_SECONDS = 60
MAX_COOLDOWN_SECONDS = 900
# Короткий cooldown выжидаем; длинный — быстрый отказ, обход завершается как partial
MAX_COOLDOWN_WAIT_SECONDS = 30

_clock = time.monotonic
_sleep = time.sleep
_random = random.uniform


class HostCooldown(RuntimeError):
    """Магазин попросил паузу (429/Retry-After); запрос не отправлялся."""

    def __init__(self, host: str, seconds: float):
        super().__init__(f"{host}: пауза по Retry-After ещё {int(seconds)} с")
        self.host = host
        self.retry_after = seconds


class _HostState:
    def __init__(self):
        self.slots = threading.BoundedSemaphore(MAX_CONCURRENCY_PER_HOST)
        self.lock = threading.Lock()
        self.next_start = 0.0
        self.cooldown_until = 0.0


_hosts: Dict[str, _HostState] = {}
_hosts_lock = threading.Lock()


def host_key(url: str) -> str:
    return (urlsplit(str(url)).hostname or "").lower()


def _state(host: str) -> _HostState:
    with _hosts_lock:
        state = _hosts.get(host)
        if state is None:
            state = _hosts[host] = _HostState()
        return state


def reset_limiter() -> None:
    """Для тестов: забыть накопленные интервалы и cooldown."""
    with _hosts_lock:
        _hosts.clear()


def parse_retry_after(value: Optional[str], now: Optional[datetime.datetime] = None) -> Optional[float]:
    """Retry-After в секундах: число секунд либо HTTP-date (UTC). Некорректное значение — None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(int(text))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return max(0.0, (when - now).total_seconds())


def _acquire(host: str) -> _HostState:
    state = _state(host)
    wait = state.cooldown_until - _clock()
    if wait > MAX_COOLDOWN_WAIT_SECONDS:
        raise HostCooldown(host, wait)
    if wait > 0:
        _sleep(wait)
    state.slots.acquire()
    try:
        with state.lock:
            now = _clock()
            start = max(now, state.next_start)
            state.next_start = start + MIN_INTERVAL_SECONDS + (_random(0, JITTER_SECONDS) if JITTER_SECONDS else 0.0)
        if start > now:
            _sleep(start - now)
    except BaseException:
        state.slots.release()
        raise
    return state


def _observe(state: _HostState, response) -> None:
    status = getattr(response, "status_code", 0)
    if status not in (429, 503):
        return
    retry_after = parse_retry_after((getattr(response, "headers", None) or {}).get("Retry-After"))
    if status == 503 and retry_after is None:
        return
    pause = min(MAX_COOLDOWN_SECONDS, retry_after if retry_after is not None else DEFAULT_COOLDOWN_SECONDS)
    with state.lock:
        state.cooldown_until = max(state.cooldown_until, _clock() + pause)


def _limited(method: str, url: str, send):
    host = host_key(url)
    if not host:
        return send()
    state = _acquire(host)
    try:
        response = send()
    finally:
        state.slots.release()
    _observe(state, response)
    return response


def request(method: str, url: str, **kwargs):
    return _limited(method, url, lambda: _curl.request(method, url, **kwargs))


def get(url: str, **kwargs):
    return _limited("GET", url, lambda: _curl.get(url, **kwargs))


def post(url: str, **kwargs):
    return _limited("POST", url, lambda: _curl.post(url, **kwargs))


class Session(_curl.Session):
    """curl_cffi Session: get/post вызывают request, поэтому лимит действует на все методы."""

    def request(self, method, url, *args, **kwargs):
        return _limited(method, url, lambda: super(Session, self).request(method, url, *args, **kwargs))


_CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"
_bundles: Dict[str, str] = {}
_bundles_lock = threading.Lock()


def ca_bundle_with(*cert_names: str) -> str:
    """certifi + недостающие промежуточные сертификаты магазина. Проверка TLS остаётся включённой."""
    key = "|".join(cert_names)
    with _bundles_lock:
        path = _bundles.get(key)
        if path and Path(path).exists():
            return path
        import tempfile
        parts = [Path(certifi.where()).read_text()]
        parts += [(_CERTS_DIR / name).read_text() for name in cert_names]
        handle = tempfile.NamedTemporaryFile("w", prefix="kzph-ca-", suffix=".pem", delete=False)
        with handle:
            handle.write("\n".join(parts))
        _bundles[key] = handle.name
        return handle.name
