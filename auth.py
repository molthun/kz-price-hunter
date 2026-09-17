"""Авторизация через Telegram Login Widget, сессии, права доступа и ограничение частоты запросов."""
import hmac
import time
import hashlib
import ipaddress
from collections import defaultdict, deque
from functools import wraps
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from aiohttp import web

from config import ADMIN_TELEGRAM_IDS, ALLOW_DEV_LOGIN, get_bot_token, merge_user_settings
from database import get_session_user, SESSION_TTL_DAYS

SESSION_COOKIE = "kzph_session"
TELEGRAM_AUTH_MAX_AGE_SECONDS = 24 * 3600

def verify_telegram_auth(data: Dict[str, Any], bot_token: str) -> Dict[str, Any]:
    """Проверяет подпись данных Telegram Login Widget.

    https://core.telegram.org/widgets/login#checking-authorization
    Возвращает проверенные данные пользователя или бросает ValueError.
    """
    if not bot_token:
        raise ValueError("Вход через Telegram не настроен на сервере")
    if not isinstance(data, dict) or "hash" not in data or "id" not in data:
        raise ValueError("Некорректные данные авторизации")

    received_hash = str(data["hash"])
    fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hashlib.sha256(bot_token.encode("utf-8")).digest()
    expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Подпись Telegram не прошла проверку")

    try:
        auth_date = int(fields.get("auth_date", 0))
    except (TypeError, ValueError):
        auth_date = 0
    if time.time() - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise ValueError("Данные авторизации устарели, войдите еще раз")

    return fields

def is_admin(user: Optional[Dict[str, Any]]) -> bool:
    return bool(user) and int(user["id"]) in ADMIN_TELEGRAM_IDS

def user_settings_for(request: web.Request) -> Dict[str, Any]:
    """Личные настройки текущего пользователя, у гостей — значения по умолчанию."""
    user = request.get("user")
    return user["settings"] if user else merge_user_settings({})

def is_secure_request(request: web.Request) -> bool:
    return request.secure or request.headers.get("X-Forwarded-Proto", "").lower() == "https"

def set_session_cookie(response: web.StreamResponse, request: web.Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        secure=is_secure_request(request),
        samesite="Lax",
        path="/",
    )

def clear_session_cookie(response: web.StreamResponse) -> None:
    response.del_cookie(SESSION_COOKIE, path="/")

def client_ip(request: web.Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or ""

def dev_login_allowed(request: web.Request) -> bool:
    """Вход разработчика: только при ALLOW_DEV_LOGIN=1, без reverse proxy и из локальной сети."""
    if not ALLOW_DEV_LOGIN or "X-Forwarded-For" in request.headers:
        return False
    try:
        ip = ipaddress.ip_address(request.remote or "")
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private

@web.middleware
async def auth_middleware(request: web.Request, handler):
    # Защита от CSRF: изменяющие запросы принимаются только со своей страницы
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("Origin")
        if origin:
            host = request.headers.get("X-Forwarded-Host") or request.host
            if urlparse(origin).netloc != host:
                return web.json_response({"status": "error", "message": "Запрос с чужого сайта отклонен"}, status=403)

    request["user"] = get_session_user(request.cookies.get(SESSION_COOKIE))
    return await handler(request)

def require_login(handler):
    @wraps(handler)
    async def wrapper(request: web.Request):
        if not request.get("user"):
            return web.json_response({"status": "error", "message": "Войдите через Telegram"}, status=401)
        return await handler(request)
    return wrapper

def require_admin(handler):
    @wraps(handler)
    async def wrapper(request: web.Request):
        user = request.get("user")
        if not user:
            return web.json_response({"status": "error", "message": "Войдите через Telegram"}, status=401)
        if not is_admin(user):
            return web.json_response({"status": "error", "message": "Доступно только администратору"}, status=403)
        return await handler(request)
    return wrapper

class RateLimiter:
    """Простое скользящее окно в памяти процесса: не больше max_calls за period секунд на ключ."""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls = defaultdict(deque)

    def retry_after(self, key: str) -> float:
        """0 — запрос разрешен (и учтен), иначе — сколько секунд подождать."""
        now = time.monotonic()
        calls = self._calls[key]
        while calls and now - calls[0] > self.period:
            calls.popleft()
        if len(calls) >= self.max_calls:
            return self.period - (now - calls[0])
        calls.append(now)
        return 0.0

_bot_username_cache: Dict[str, Any] = {"token": None, "username": None}

def get_bot_username() -> Optional[str]:
    """Имя бота для виджета входа (getMe), кэшируется до смены токена."""
    token = get_bot_token()
    if not token:
        return None
    if _bot_username_cache["token"] == token and _bot_username_cache["username"]:
        return _bot_username_cache["username"]
    from notifier import telegram_api
    try:
        res = telegram_api("getMe", {})
        if res.status_code == 200:
            username = res.json().get("result", {}).get("username")
            _bot_username_cache.update(token=token, username=username)
            return username
        print(f"[Auth] getMe вернул {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[Auth] Не удалось получить имя бота: {e}")
    return None
