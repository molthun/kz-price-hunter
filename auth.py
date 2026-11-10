"""Авторизация через Telegram Login Widget, сессии, права доступа и ограничение частоты запросов."""
import asyncio
import concurrent.futures
import hmac
import time
import hashlib
import ipaddress
from collections import defaultdict, deque
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from aiohttp import web

from config import ADMIN_TELEGRAM_IDS, ALLOW_DEV_LOGIN, DEV_ADMIN_ID, get_bot_token, merge_user_settings, TRUSTED_PROXIES, PUBLIC_ORIGIN, MAINTENANCE_DEV_MODE
from database import get_session_user, SESSION_TTL_DAYS

SESSION_COOKIE = "kzph_session"
TELEGRAM_AUTH_MAX_AGE_SECONDS = 5 * 60
TELEGRAM_AUTH_CLOCK_SKEW_SECONDS = 30

def verify_telegram_auth(data: Dict[str, Any], bot_token: str) -> Dict[str, Any]:
    """Проверяет подпись данных Telegram Login Widget.

    https://core.telegram.org/widgets/login-legacy#checking-authorization
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
    now = time.time()
    if auth_date > now + TELEGRAM_AUTH_CLOCK_SKEW_SECONDS:
        raise ValueError("Время авторизации Telegram находится в будущем")
    if now - auth_date > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        raise ValueError("Данные авторизации устарели, войдите еще раз")

    if not str(fields["id"]).isdigit() or int(fields["id"]) <= 0:
        raise ValueError("Некорректный ID Telegram")

    return fields

def verify_telegram_webapp_data(init_data_str: str, bot_token: str) -> Dict[str, Any]:
    """Проверяет криптографическую подпись данных Telegram WebApp / Mini App (initData).

    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    Возвращает проверенные данные пользователя или бросает ValueError.
    """
    if not bot_token:
        raise ValueError("Вход через Telegram не настроен на сервере")
    if not init_data_str or not isinstance(init_data_str, str):
        raise ValueError("Отсутствуют данные Telegram WebApp")

    import json
    from urllib.parse import parse_qsl

    parsed = dict(parse_qsl(init_data_str, keep_blank_values=True))
    if "hash" not in parsed:
        raise ValueError("В данных Telegram WebApp отсутствует подпись hash")

    received_hash = parsed["hash"]
    fields = {k: v for k, v in parsed.items() if k != "hash"}
    check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))

    # Секретный ключ Telegram WebApp: HMAC_SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise ValueError("Подпись Telegram WebApp не прошла проверку")

    try:
        auth_date = int(fields.get("auth_date", 0))
    except (TypeError, ValueError):
        auth_date = 0
    now = time.time()
    if auth_date > now + TELEGRAM_AUTH_CLOCK_SKEW_SECONDS:
        raise ValueError("Время авторизации Telegram WebApp находится в будущем")
    if now - auth_date > 86400:  # 24 часа для сессии WebApp
        raise ValueError("Данные Telegram WebApp устарели")

    user_raw = fields.get("user")
    if not user_raw:
        raise ValueError("В данных Telegram WebApp отсутствует объект user")

    try:
        user_obj = json.loads(user_raw)
    except Exception:
        raise ValueError("Некорректный формат JSON пользователя в Telegram WebApp")

    if not isinstance(user_obj, dict) or "id" not in user_obj:
        raise ValueError("Некорректные данные пользователя в Telegram WebApp")

    return {
        "id": int(user_obj["id"]),
        "first_name": str(user_obj.get("first_name") or ""),
        "last_name": str(user_obj.get("last_name") or ""),
        "username": str(user_obj.get("username") or ""),
        "photo_url": str(user_obj.get("photo_url") or ""),
        "auth_date": auth_date,
    }

def is_admin(user: Optional[Dict[str, Any]]) -> bool:
    if not user:
        return False
    if int(user["id"]) in ADMIN_TELEGRAM_IDS:
        return True
    # Локальный запуск без ADMIN_TELEGRAM_IDS: «Разработчик» — администратор
    return ALLOW_DEV_LOGIN and not ADMIN_TELEGRAM_IDS and int(user["id"]) == DEV_ADMIN_ID

def is_confirmed_user(user: Optional[Dict[str, Any]]) -> bool:
    """Проверяет, подтверждён ли пользователь владельцем (или является ли он администратором)."""
    if not user:
        return False
    if is_admin(user):
        return True
    settings = user.get("settings") or {}
    return bool(settings.get("is_approved") or settings.get("is_confirmed"))

def user_settings_for(request: web.Request) -> Dict[str, Any]:
    """Личные настройки текущего пользователя, у гостей — значения по умолчанию."""
    user = request.get("user")
    return user["settings"] if user else merge_user_settings({})

def normalized_origin(value: str) -> Optional[str]:
    """Compare full browser origins (scheme, host, effective port), never netloc alone."""
    try:
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port == 0:
            return None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        return f"{parsed.scheme}://{host}:{port}"
    except ValueError:
        return None


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def trusted_proxy(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer)
        return any(address in network for network in _TRUSTED_PROXY_NETWORKS)
    except ValueError:
        return False


# Invalid configuration must fail at startup rather than silently trusting a subnet.
_TRUSTED_PROXY_NETWORKS = tuple(ipaddress.ip_network(value, strict=False) for value in TRUSTED_PROXIES)
if PUBLIC_ORIGIN and normalized_origin(PUBLIC_ORIGIN) is None:
    raise ValueError("PUBLIC_ORIGIN должен содержать только http(s)://host[:port]")


def expected_origin(request: web.Request) -> Optional[str]:
    if PUBLIC_ORIGIN:
        return normalized_origin(PUBLIC_ORIGIN)
    # Unconfigured local mode: no arbitrary Host or proxy headers (DNS rebinding).
    origin = normalized_origin(f"{request.scheme}://{request.host}")
    if origin and _is_loopback_host(urlparse(origin).hostname) and _is_loopback_host(request.remote or "") and not trusted_proxy(request.remote or ""):
        return origin
    return None


def is_secure_request(request: web.Request) -> bool:
    if PUBLIC_ORIGIN and urlparse(PUBLIC_ORIGIN).scheme == "https":
        return True
    return request.secure or (trusted_proxy(request.remote or "") and request.headers.get("X-Forwarded-Proto", "").lower() == "https")

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
    peer = request.remote or ""
    if not trusted_proxy(peer):
        return peer
    forwarded = request.headers.get("X-Forwarded-For", "")
    if not forwarded or len(forwarded) > 2048:
        return peer
    try:
        chain = [str(ipaddress.ip_address(x.strip())) for x in forwarded.split(",")]
    except ValueError:
        return peer
    # Walk from the immediate peer outward; a client-controlled left prefix is ignored.
    for address in reversed(chain + [peer]):
        if not trusted_proxy(address):
            return address
    return chain[0]


def browser_read_action_allowed(request: web.Request) -> bool:
    """GET live/AI spends quota and writes cache: require a same-origin-only header."""
    expected = expected_origin(request)
    origin = request.headers.get("Origin")
    return bool(expected and request.headers.get("X-KZPH-Request") == "1"
                and (origin is None or normalized_origin(origin) == expected)
                and request.headers.get("Sec-Fetch-Site") != "cross-site")


def dev_login_allowed(request: web.Request) -> bool:
    """Explicit, direct loopback development only; never a production admin identity."""
    if not ALLOW_DEV_LOGIN or ADMIN_TELEGRAM_IDS or PUBLIC_ORIGIN or trusted_proxy(request.remote or ""):
        return False
    if any(h.lower() == "forwarded" or h.lower().startswith("x-forwarded-") for h in request.headers):
        return False
    return _is_loopback_host(request.remote or "") and expected_origin(request) is not None


# Пробы и статика одинаково не трогают базу сессий, в том числе /api/readiness — alias /api/health
_NO_SESSION_PATHS = frozenset(("/api/live", "/api/health", "/api/readiness", "/favicon.ico", "/api/version"))
_session_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="kzph-session")


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # C06: Быстрый bypass без обращения к БД сессий для health/readiness/liveness и статики
    if request.path in _NO_SESSION_PATHS or request.path.startswith("/static/"):
        request["user"] = None
        response = await handler(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = normalized_origin(request.headers.get("Origin", ""))
        expected = expected_origin(request)
        if not origin or not expected or origin != expected:
            return web.json_response({"status": "error", "message": "Запрос отклонён: требуется Origin настроенного сайта"}, status=403, headers={"Cache-Control": "no-store"})
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return web.json_response({"status": "error", "message": "Запрос с чужого сайта отклонен"}, status=403, headers={"Cache-Control": "no-store"})

    session_cookie = request.cookies.get(SESSION_COOKIE)
    if session_cookie:
        # Свой пул: занятый общий пул потоков (to_thread сканеров) не задерживает проверку сессии (C06)
        request["user"] = await asyncio.get_running_loop().run_in_executor(
            _session_executor, get_session_user, session_cookie)
    else:
        request["user"] = None
    # A developer session is only meaningful at the same direct local boundary.
    if request["user"] and int(request["user"]["id"]) == DEV_ADMIN_ID and not dev_login_allowed(request):
        request["user"] = None

    # Server-side gate: never deliver the application shell to guests in closed mode.
    restricted = MAINTENANCE_DEV_MODE and not is_confirmed_user(request["user"])
    if restricted and not request.path.startswith("/api/"):
        if request.method not in ("GET", "HEAD"):
            return web.Response(status=403, headers={"Cache-Control":"no-store"})
        return web.FileResponse(Path(__file__).parent / "web/templates/closed_login.html", headers={
            "Cache-Control":"no-store", "Vary":"Cookie", "X-Content-Type-Options":"nosniff",
            "X-Frame-Options":"DENY", "Referrer-Policy":"strict-origin-when-cross-origin"})

    # Закрытый режим разработки: доступ к функциональным API только подтверждённым пользователям
    if MAINTENANCE_DEV_MODE and request.path.startswith("/api/"):
        _allowed_api = {"/api/me", "/api/auth/telegram", "/api/auth/telegram-webapp",
                        "/api/auth/dev-login", "/api/auth/logout", "/api/diag",
                        "/api/version", "/api/telegram/deep-link"}
        if request.path not in _allowed_api:
            if not is_confirmed_user(request["user"]):
                return web.json_response({
                    "status": "error",
                    "message": "Сайт находится в режиме закрытой разработки. Доступ открыт только подтверждённым пользователям.",
                    "maintenance": True,
                    "authenticated": bool(request["user"]),
                    "confirmed": False
                }, status=403, headers={"Cache-Control": "no-store"})
    response = await handler(request)
    if MAINTENANCE_DEV_MODE:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = ", ".join(dict.fromkeys([v.strip() for v in response.headers.get("Vary", "").split(",") if v.strip()] + ["Cookie"]))
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response

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

    SWEEP_EVERY = 1024

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._calls = defaultdict(deque)
        self._ops = 0

    def _sweep(self, now: float) -> None:
        """Удаляет ключи без вызовов в текущем окне: IP/пользователи не копятся бесконечно (M03)."""
        for key in [k for k, calls in self._calls.items() if not calls or now - calls[-1] > self.period]:
            del self._calls[key]

    def retry_after(self, key: str) -> float:
        """0 — запрос разрешен (и учтен), иначе — сколько секунд подождать."""
        now = time.monotonic()
        self._ops += 1
        if self._ops % self.SWEEP_EVERY == 0:
            self._sweep(now)
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
        print(f"[Auth] getMe вернул {res.status_code}")
    except Exception as e:
        print(f"[Auth] Не удалось получить имя бота: {type(e).__name__}")
    return None
