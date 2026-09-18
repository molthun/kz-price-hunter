import asyncio
from typing import Dict
import datetime
import time
from pathlib import Path
from aiohttp import web

from config import (
    load_settings,
    save_settings,
    get_scan_interval_seconds,
    get_bot_token,
    get_candidate_settings,
    legacy_user_settings,
    validate_user_settings,
    ADMIN_TELEGRAM_IDS,
    DEV_ADMIN_ID,
    ALLOW_DEV_LOGIN,
    SHOP_KEYS,
    CITIES_KZ,
    DNS_CATEGORIES,
    SHOPKZ_CATEGORIES,
    TECHNODOM_CATEGORIES,
    FORCECOM_CATEGORIES,
    SULPAK_CATEGORIES,
    MECHTA_CATEGORIES,
    ALSER_CATEGORIES,
    EVRIKA_CATEGORIES,
    MOON_CATEGORIES,
    KASPI_CATEGORIES,
    FOURMOBILE_CATEGORIES,
    FLIP_CATEGORIES,
    HALYK_CATEGORIES,
    TGRAD_CATEGORIES,
    ANTS_CATEGORIES,
    ITMAG_CATEGORIES,
    ISPACE_CATEGORIES
)
from version import get_version_info
from database import (
    init_db,
    reconcile_source,
    notification_stats,
    get_stats,
    get_alerts,
    get_products_list,
    get_products_count,
    was_alert_sent_recently,
    record_alert,
    get_db_freshness,
    record_shop_scan_start,
    record_shop_scan_result,
    get_stale_shops,
    get_shops_scan_report,
    upsert_telegram_user,
    save_user_settings,
    list_users,
    get_user,
    set_user_blocked,
    create_session,
    delete_session
)
from detector import check_anomaly, check_market_arbitrage
from scrapers.flip import FlipScraper
from scrapers.halyk import HalykScraper
from scrapers.tgrad import TgradScraper
from scrapers.ants import AntsScraper
from scrapers.itmag import ItmagScraper
from scrapers.ispace import ISpaceScraper
from scrapers.dns import DNSScraper
from scrapers.shopkz import ShopKzScraper
from scrapers.technodom import TechnodomScraper
from scrapers.forcecom import ForcecomScraper
from scrapers.sulpak import SulpakScraper
from scrapers.mechta import MechtaScraper
from scrapers.alser import AlserScraper
from scrapers.evrika import EvrikaScraper
from scrapers.moon import MoonScraper
from scrapers.kaspi import KaspiScraper
from scrapers.fourmobile import FourMobileScraper
from search_engine import get_best_price_summary
import ai_service
from config import get_ai_config
from notifier import prepare_deliveries, notification_worker, telegram_api
from log_manager import install_log_interceptor, log_buffer
from auth import (
    SESSION_COOKIE,
    auth_middleware,
    require_login,
    require_admin,
    is_admin,
    user_settings_for,
    verify_telegram_auth,
    set_session_cookie,
    clear_session_cookie,
    dev_login_allowed,
    client_ip,
    get_bot_username,
    RateLimiter
)

# Инициализируем перехватчик консольного вывода для веб-логов
install_log_interceptor()

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"

# Глобальное состояние сканирования
scan_state = {
    "is_running": False,
    "current_shop": "",
    "current_category": "",
    "current_step": 0,
    "total_steps": 0,
    "progress_pct": 0,
    "total_scanned": 0,
    "anomalies_found": 0,
    "last_completed": None,
    "error": None
}

# Прямой опрос магазинов создает нагрузку на их сайты — ограничиваем частоту
live_search_limiter = RateLimiter(max_calls=10, period=600)   # на пользователя
search_limiter = RateLimiter(max_calls=60, period=60)         # на IP
consultant_limiter = RateLimiter(max_calls=10, period=60)
auth_limiter = RateLimiter(max_calls=20, period=600)          # на IP

routes = web.RouteTableDef()

def _int_param(request, name, default, minimum=0, maximum=None):
    try:
        value = int(request.query.get(name, default))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value

def _public_user(user):
    if not user:
        return None
    return {
        "id": user["id"],
        "username": user.get("username"),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "photo_url": user.get("photo_url"),
        "is_admin": is_admin(user),
    }

@routes.get("/")
async def index_handler(request):
    html_file = TEMPLATES_DIR / "index.html"
    return web.FileResponse(html_file)

@routes.get("/api/version")
async def version_handler(request):
    return web.json_response(get_version_info())

@routes.get("/api/cities")
async def cities_handler(request):
    return web.json_response(CITIES_KZ)

POPULAR_CATEGORIES = [
    {"id": "Смартфоны", "name": "Смартфоны", "icon": "📱"},
    {"id": "Ноутбуки", "name": "Ноутбуки", "icon": "💻"},
    {"id": "Видеокарты", "name": "Видеокарты", "icon": "🎮"},
    {"id": "Мониторы", "name": "Мониторы", "icon": "🖥"},
    {"id": "Наушники", "name": "Наушники", "icon": "🎧"},
    {"id": "Планшеты", "name": "Планшеты", "icon": "📲"},
    {"id": "Телевизоры", "name": "Телевизоры", "icon": "📺"},
    {"id": "Игровые приставки", "name": "Игровые приставки", "icon": "🕹"},
    {"id": "Процессоры", "name": "Процессоры", "icon": "⚙️"},
    {"id": "Материнские платы", "name": "Материнские платы", "icon": "🔌"},
    {"id": "SSD", "name": "SSD накопители", "icon": "💾"},
    {"id": "Корпуса", "name": "Корпуса ПК", "icon": "🗄"},
    {"id": "Блоки питания", "name": "Блоки питания", "icon": "🔋"},
    {"id": "Клавиатуры", "name": "Клавиатуры", "icon": "⌨️"},
    {"id": "Мыши", "name": "Мыши", "icon": "🖱"},
    {"id": "Смарт-часы", "name": "Смарт-часы", "icon": "⌚️"},
    {"id": "Пылесосы", "name": "Пылесосы", "icon": "🧹"},
    {"id": "Стиральные машины", "name": "Стиральные машины", "icon": "🧺"},
    {"id": "Холодильники", "name": "Холодильники", "icon": "❄️"},
    {"id": "Кондиционеры", "name": "Кондиционеры", "icon": "🌬"},
]

@routes.get("/api/categories")
async def categories_handler(request):
    return web.json_response(POPULAR_CATEGORIES)

@routes.get("/api/stats")
async def stats_handler(request):
    stats = get_stats(user_settings_for(request))
    stats["scan_state"] = scan_state
    return web.json_response(stats)

@routes.get("/api/alerts")
async def alerts_handler(request):
    city = request.query.get("city", None)
    alert_type = request.query.get("type", None)
    limit = _int_param(request, "limit", 150, minimum=1, maximum=500)
    alerts = get_alerts(limit=limit, city=city, alert_type=alert_type, user_settings=user_settings_for(request))
    return web.json_response(alerts)

@routes.get("/api/products")
async def products_handler(request):
    shop = request.query.get("shop", None)
    city = request.query.get("city", None)
    search = request.query.get("search", None)
    limit = _int_param(request, "limit", 50, minimum=1, maximum=200)
    offset = _int_param(request, "offset", 0)

    products = get_products_list(shop=shop, city=city, search=search, limit=limit, offset=offset)
    total = get_products_count(shop=shop, city=city, search=search)

    return web.json_response({
        "products": products,
        "total": total,
        "limit": limit,
        "offset": offset
    })

@routes.get("/api/ai/status")
async def ai_status_handler(request):
    creds = get_ai_config()
    provider = "gemini" if creds["gemini_api_key"] else ("openai" if creds["openai_api_key"] else None)
    return web.json_response({
        "status": "ok",
        "configured": bool(creds.get("has_ai")),
        "available": bool(provider and creds["ai_search_enabled"]),
        "provider": provider,
        "enabled": creds["ai_search_enabled"],
        "has_gemini": bool(creds["gemini_api_key"]),
        "has_openai": bool(creds["openai_api_key"])
    })

@routes.post("/api/ai/consultant")
@require_login
async def ai_consultant_handler(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Некорректный JSON в теле запроса"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"error": "Ожидается объект JSON"}, status=400)
    message = str(data.get("message") or "").strip()
    history = data.get("history") or []
    city = str(data.get("city") or "Все").strip()

    if (len(message) > 4000 or not isinstance(history, list) or len(history) > 8
            or any(not isinstance(t, dict) or t.get("role") not in ("user", "model", "assistant")
                   or not isinstance(t.get("content"), str) for t in history)):
        return web.json_response({"error": "Слишком длинное сообщение или некорректная история"}, status=400)
    # Длинные прошлые ответы не ломают диалог: реплики обрезаются, а не отклоняют запрос
    history = ai_service.normalize_history(history)
    wait = consultant_limiter.retry_after(str(request["user"]["id"]))
    if wait:
        return web.json_response({"error": "Слишком много запросов. Попробуйте позже."}, status=429,
                                 headers={"Retry-After": str(int(wait) + 1)})
    if not message:
        return web.json_response({"error": "Поле message обязательно"}, status=400)

    try:
        response = await ai_service.ask_ai_consultant(message=message, history=history, city=city)
        if isinstance(response, dict) and "status" not in response:
            response["status"] = "ok"
        return web.json_response(response)
    except Exception as e:
        print(f"[AI] Ошибка консультанта: {type(e).__name__}")
        return web.json_response({
            "error": "Ошибка обработки запроса AI-консультанта",
            "answer": "Произошла ошибка при обращении к AI-консультанту. Пожалуйста, попробуйте еще раз через несколько секунд.",
            "products": []
        }, status=500)

@routes.get("/api/models/compare")
async def compare_model_offers_handler(request):
    """Сравнение цен на каноническую модель между всеми подключенными магазинами."""
    key = request.query.get("canonical_key", "").strip().lower()
    title = request.query.get("title", "").strip()
    city = request.query.get("city", "Все").strip()

    if not key and title:
        from model_matching import extract_canonical_key
        key = extract_canonical_key(title) or ""

    if not key:
        return web.json_response({"status": "error", "message": "Параметр canonical_key или title обязателен"}, status=400)

    from database import get_connection, active_product_clause
    with get_connection() as conn:
        city_clause = "AND (city = ? OR city = 'Все' OR city IS NULL)" if city != "Все" else ""
        params = [key, city] if city != "Все" else [key]
        rows = conn.execute(f"""
            SELECT id, shop, title, current_price, old_price_on_site, url, image_url, city, canonical_key
            FROM products
            WHERE canonical_key = ? {city_clause} AND current_price > 0 AND """ + active_product_clause() + """
            ORDER BY current_price ASC
        """, params).fetchall()

    from model_matching import same_model
    reference = title or (rows[0]["title"] if rows else "")
    items = [dict(r) for r in rows if same_model(reference, r["title"])]
    min_p = items[0]["current_price"] if items else 0
    max_p = items[-1]["current_price"] if items else 0
    diff = max_p - min_p if len(items) > 1 else 0
    diff_pct = round((diff / max_p) * 100, 1) if max_p > 0 else 0

    return web.json_response({
        "status": "ok",
        "canonical_key": key,
        "total_offers": len(items),
        "min_price": min_p,
        "max_price": max_p,
        "arbitrage_savings": diff,
        "arbitrage_pct": diff_pct,
        "offers": items
    })

@routes.get("/api/best-price")
async def best_price_handler(request):
    query = request.query.get("q", "").strip()
    live = request.query.get("live", "false").lower() in ("true", "1", "yes")
    shop = request.query.get("shop", None)
    city = request.query.get("city", None)
    category = request.query.get("category", None)
    user = request.get("user")

    # AI-поиск
    ai_param = request.query.get("ai", "auto").lower()
    # AI-разбор расходует квоту владельца, поэтому доступен только вошедшим пользователям
    use_ai = bool(user) and (ai_param in ("1", "true", "yes") or (ai_param == "auto" and ai_service.should_use_ai_parsing(query)))

    # Расширенные гибкие фильтры
    min_price_param = request.query.get("min_price", None)
    min_price = int(min_price_param) if min_price_param and min_price_param.isdigit() else None

    max_price_param = request.query.get("max_price", None)
    max_price = int(max_price_param) if max_price_param and max_price_param.isdigit() else None

    exclude_acc_param = request.query.get("exclude_acc", "1")
    exclude_accessories = exclude_acc_param in ("1", "true", "yes")

    only_discount_param = request.query.get("only_discount", "0")
    only_discount = only_discount_param in ("1", "true", "yes")

    match_mode = request.query.get("mode", "AND").upper()
    sort_by = request.query.get("sort", "price_asc")

    neg_param = request.query.get("neg", "").strip()
    negative_keywords = [k.strip() for k in neg_param.split(",") if k.strip()] if neg_param else None

    if not query:
        return web.json_response({
            "query": "",
            "total_found": 0,
            "best_deal": None,
            "price_stats": None,
            "store_comparison": [],
            "items": []
        })

    wait = search_limiter.retry_after(f"ip:{client_ip(request)}")
    if wait:
        return web.json_response({"error": f"Слишком много запросов, повторите через {int(wait) + 1} сек"}, status=429)

    if live:
        if not user:
            return web.json_response({"error": "Прямой опрос магазинов доступен после входа через Telegram"}, status=401)
        wait = live_search_limiter.retry_after(f"user:{user['id']}")
        if wait:
            return web.json_response({"error": f"Лимит прямого опроса магазинов исчерпан, повторите через {int(wait // 60) + 1} мин"}, status=429)

    # Интеллектуальный разбор запроса через AI
    ai_meta = None
    search_query = query
    if use_ai and query:
        try:
            ai_meta = await ai_service.parse_natural_query(query, current_city=city or "Все")
            if ai_meta:
                if ai_meta.get("clean_query"):
                    search_query = ai_meta["clean_query"]
                if (not category or category == "Все") and ai_meta.get("category"):
                    category = ai_meta["category"]
                if min_price is None and ai_meta.get("min_price"):
                    min_price = ai_meta["min_price"]
                if max_price is None and ai_meta.get("max_price"):
                    max_price = ai_meta["max_price"]
                if not only_discount and ai_meta.get("only_discount"):
                    only_discount = True
                if not negative_keywords and ai_meta.get("negative_keywords"):
                    negative_keywords = ai_meta["negative_keywords"]
                if sort_by == "price_asc" and ai_meta.get("sort"):
                    sort_by = ai_meta["sort"]
        except Exception as e:
            print(f"[AI BestPrice] Ошибка парсинга запроса: {e}")

    # Разбор по правилам без AI: гости, AI выключен или не ответил. Фраза «ноутбук для игр до 400к»
    # иначе ищется целиком и ничего не находит. При ai=0 (явно выключено) запрос не трогаем.
    prefer_keywords = product_nouns = None
    if ai_meta is None and query and ai_param != "0":
        from query_parser import parse_query_rules
        rules_meta = parse_query_rules(query)
        if rules_meta:
            ai_meta = rules_meta
            search_query = rules_meta["clean_query"]
            if min_price is None and rules_meta.get("min_price"):
                min_price = rules_meta["min_price"]
            if max_price is None and rules_meta.get("max_price"):
                max_price = rules_meta["max_price"]
            if not only_discount and rules_meta.get("only_discount"):
                only_discount = True
            prefer_keywords = rules_meta.get("prefer_keywords") or None
            product_nouns = rules_meta.get("product_nouns") or None

    try:
        data = await get_best_price_summary(
            query=search_query,
            live=live,
            shop=shop,
            city=city,
            category=category,
            only_discount=only_discount,
            min_price=min_price,
            max_price=max_price,
            exclude_accessories=exclude_accessories,
            match_mode=match_mode,
            sort_by=sort_by,
            negative_keywords=negative_keywords,
            junk_keywords=user_settings_for(request).get("junk_keywords", []),
            prefer_keywords=prefer_keywords,
            product_nouns=product_nouns
        )
        data["original_query"] = query
        data["ai_meta"] = ai_meta
        return web.json_response(data)
    except Exception as e:
        print(f"[API best-price] Ошибка: {e}")
        return web.json_response({"error": "Ошибка поиска, попробуйте позже"}, status=500)

# ===== Авторизация =====

@routes.get("/api/me")
async def me_handler(request):
    """Текущий пользователь (или гость), его личные настройки и параметры входа."""
    user = request.get("user")
    bot_username = await asyncio.to_thread(get_bot_username)
    return web.json_response({
        "user": _public_user(user),
        "settings": user_settings_for(request),
        "auth": {
            "bot_username": bot_username,
            "dev_login": dev_login_allowed(request),
        },
        "shops": SHOP_KEYS,
    })

def _login_response(request, user):
    token = create_session(user["id"])
    response = web.json_response({"status": "ok", "user": _public_user(user)})
    set_session_cookie(response, request, token)
    print(f"[Auth] Вход пользователя {user['id']} (@{user.get('username') or '-'})")
    return response

def _initial_settings_for(user_id):
    # Администраторы получают личные пороги из старого однопользовательского settings.json
    dev_admin = ALLOW_DEV_LOGIN and not ADMIN_TELEGRAM_IDS and int(user_id) == DEV_ADMIN_ID
    return legacy_user_settings() if int(user_id) in ADMIN_TELEGRAM_IDS or dev_admin else {}

@routes.post("/api/auth/telegram")
async def telegram_login_handler(request):
    if auth_limiter.retry_after(f"ip:{client_ip(request)}"):
        return web.json_response({"status": "error", "message": "Слишком много попыток входа, попробуйте позже"}, status=429)
    try:
        data = await request.json()
        tg = verify_telegram_auth(data, get_bot_token())
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=401)
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)

    existing = get_user(int(tg["id"]))
    if existing and existing["is_blocked"]:
        return web.json_response({"status": "error", "message": "Аккаунт заблокирован администратором"}, status=403)

    user = upsert_telegram_user(tg, initial_settings=None if existing else _initial_settings_for(tg["id"]))
    return _login_response(request, user)

@routes.post("/api/auth/dev-login")
async def dev_login_handler(request):
    if not dev_login_allowed(request):
        return web.json_response({"status": "error", "message": "Вход разработчика отключен"}, status=403)
    uid = min(ADMIN_TELEGRAM_IDS) if ADMIN_TELEGRAM_IDS else DEV_ADMIN_ID
    existing = get_user(uid)
    user = upsert_telegram_user(
        {"id": uid, "username": "dev", "first_name": "Разработчик"},
        initial_settings=None if existing else _initial_settings_for(uid)
    )
    return _login_response(request, user)

@routes.post("/api/auth/logout")
async def logout_handler(request):
    delete_session(request.cookies.get(SESSION_COOKIE))
    response = web.json_response({"status": "ok"})
    clear_session_cookie(response)
    return response

# ===== Личные настройки =====

@routes.post("/api/me/settings")
@require_login
async def save_my_settings_handler(request):
    try:
        data = await request.json()
        clean = validate_user_settings(data)
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    saved = save_user_settings(request["user"]["id"], clean)
    return web.json_response({"status": "ok", "settings": saved})

@routes.post("/api/me/test-telegram")
@require_login
async def test_my_telegram_handler(request):
    user = request["user"]
    text = (
        "🎯 <b>KZ Price Hunter</b>\n\n"
        "✅ Уведомления настроены! Сюда будут приходить ценовые ошибки и скидки по вашим порогам."
    )
    try:
        res = await asyncio.to_thread(telegram_api, "sendMessage", {"chat_id": user["id"], "text": text, "parse_mode": "HTML"})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)
    if res.status_code == 200:
        return web.json_response({"status": "ok", "message": "Тестовое сообщение отправлено в ваш Telegram"})
    if res.status_code == 403:
        return web.json_response({"status": "error", "message": "Бот не может написать вам: откройте бота в Telegram и нажмите «Start»"}, status=400)
    return web.json_response({"status": "error", "message": f"Ошибка Telegram: {res.text}"}, status=400)

# ===== Администрирование =====

@routes.get("/api/admin/config")
@require_admin
async def get_admin_config_handler(request):
    bot_username = await asyncio.to_thread(get_bot_username)
    settings = load_settings()
    settings_masked = dict(settings)
    if settings_masked.get("gemini_api_key"):
        k = settings_masked["gemini_api_key"]
        settings_masked["gemini_api_key"] = k[:4] + "..." + k[-4:] if len(k) > 8 else "***"
    if settings_masked.get("openai_api_key"):
        k = settings_masked["openai_api_key"]
        settings_masked["openai_api_key"] = k[:4] + "..." + k[-4:] if len(k) > 8 else "***"
    return web.json_response({
        "settings": settings_masked,
        "bot": {"configured": bool(get_bot_token()), "username": bot_username},
        "ai": {k: v for k, v in get_ai_config().items() if k not in ("gemini_api_key", "openai_api_key")}
    })

@routes.post("/api/admin/config")
@require_admin
async def post_admin_config_handler(request):
    try:
        data = await request.json()
        current = load_settings()
        for k in ("gemini_api_key", "openai_api_key"):
            if k in data and ("..." in str(data[k]) or "***" in str(data[k])):
                data[k] = current.get(k, "")
        saved = save_settings(data)
        public_settings = dict(saved)
        for key in ("gemini_api_key", "openai_api_key"):
            if public_settings.get(key):
                public_settings[key] = "***"
        return web.json_response({"status": "ok", "settings": public_settings})
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": f"Ошибка сохранения: {e}"}, status=400)

@routes.get("/api/admin/users")
@require_admin
async def admin_users_handler(request):
    users = [{
        **_public_user(u),
        "is_blocked": u["is_blocked"],
        "created_at": u["created_at"],
        "last_login_at": u["last_login_at"],
        "telegram_notify_enabled": bool(u["settings"].get("telegram_notify_enabled")),
    } for u in list_users()]
    return web.json_response(users)

@routes.post("/api/admin/users/{user_id}/block")
@require_admin
async def admin_block_user_handler(request):
    try:
        user_id = int(request.match_info["user_id"])
        blocked = bool((await request.json()).get("blocked"))
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    if user_id in ADMIN_TELEGRAM_IDS:
        return web.json_response({"status": "error", "message": "Администратора нельзя заблокировать"}, status=400)
    if not get_user(user_id):
        return web.json_response({"status": "error", "message": "Пользователь не найден"}, status=404)
    set_user_blocked(user_id, blocked)
    return web.json_response({"status": "ok"})

async def _process_anomaly(p, anomaly, shop_name):
    """Записывает кандидата (с защитой от дублей) и рассылает его пользователям по их личным порогам."""
    if not anomaly or was_alert_sent_recently(p["id"], p["price"]):
        return False

    scan_state["anomalies_found"] += 1

    record_alert(
        product_id=p["id"],
        alert_type=anomaly["type"],
        old_price=anomaly["old_price"],
        new_price=anomaly["new_price"],
        discount_pct=anomaly["drop_pct"],
        savings_kzt=anomaly["savings"],
        shop=p.get("shop", shop_name),
        city=p.get("city", "Астана"),
        competitor_shop=anomaly.get("competitor_shop"),
        deliveries=prepare_deliveries(p, anomaly)
    )
    return True

# Реестр магазинов: ключ настроек -> (класс парсера, категории, название)
SHOP_REGISTRY = {
    "shopkz": (ShopKzScraper, SHOPKZ_CATEGORIES, "Белый Ветер"),
    "mechta": (MechtaScraper, MECHTA_CATEGORIES, "Мечта"),
    "forcecom": (ForcecomScraper, FORCECOM_CATEGORIES, "Forcecom"),
    "sulpak": (SulpakScraper, SULPAK_CATEGORIES, "Sulpak"),
    "evrika": (EvrikaScraper, EVRIKA_CATEGORIES, "Эврика"),
    "moon": (MoonScraper, MOON_CATEGORIES, "Moon.kz"),
    "technodom": (TechnodomScraper, TECHNODOM_CATEGORIES, "Технодом"),
    "alser": (AlserScraper, ALSER_CATEGORIES, "Alser"),
    "fourmobile": (FourMobileScraper, FOURMOBILE_CATEGORIES, "4mobile"),
    "kaspi": (KaspiScraper, KASPI_CATEGORIES, "Kaspi Магазин"),
    "dns": (DNSScraper, DNS_CATEGORIES, "DNS"),
    "flip": (FlipScraper, FLIP_CATEGORIES, "Flip.kz"),
    "halyk": (HalykScraper, HALYK_CATEGORIES, "Halyk Market"),
    "tgrad": (TgradScraper, TGRAD_CATEGORIES, "Tgrad"),
    "ants": (AntsScraper, ANTS_CATEGORIES, "ANTS"),
    "itmag": (ItmagScraper, ITMAG_CATEGORIES, "ITMag"),
    "ispace": (ISpaceScraper, ISPACE_CATEGORIES, "iSpace"),
}

# Сколько магазинов обходить одновременно (у каждого свой сайт, поэтому нагрузка не суммируется)
SHOP_CONCURRENCY = 4

def enabled_shop_keys(settings=None):
    s = settings if settings is not None else load_settings()
    enabled = s.get("enabled_shops", {})
    return [key for key in SHOP_REGISTRY if enabled.get(key, True)]

async def _save_and_detect(prods, shop_name, candidate_settings):
    """Сохраняет товары категории одной транзакцией и записывает кандидатов в аномалии.

    Пакетная запись важна не только для скорости: магазины сканируются параллельно,
    и отдельная транзакция на каждый товар создавала конкуренцию за блокировку базы.
    """
    if not prods:
        return

    from database import save_or_update_products_batch, get_price_history_batch

    # AI-нормализация не задерживает сохранение: товары без ключа от эвристики уходят
    # в фоновую очередь, ключи проставляются позже (см. ai_normalize_background_worker)
    queue_titles_for_ai(prods)

    # История цен читается ДО пакетной перезаписи, иначе прежняя цена будет потеряна
    history_map = await asyncio.to_thread(get_price_history_batch, [p["id"] for p in prods])
    await asyncio.to_thread(save_or_update_products_batch, prods)

    for p in prods:
        history = history_map.get(str(p["id"]), {"old_price": p["price"], "first_seen_price": p["price"]})
        if history["old_price"] > p["price"] or history["first_seen_price"] > p["price"] or (p.get("old_price_on_site") or 0) > p["price"]:
            await _process_anomaly(p, check_anomaly(p, history, custom_settings=candidate_settings), shop_name)

    # Check every saved offer; blocking DB work runs outside the event loop.
    for p in prods:
        anomaly = await asyncio.to_thread(check_market_arbitrage, p, custom_settings=candidate_settings)
        await _process_anomaly(p, anomaly, shop_name)

# Очередь фоновой AI-нормализации: название -> ID товаров с этим названием
_ai_pending: Dict[str, set] = {}
AI_PENDING_MAX_TITLES = 20000
AI_NORMALIZE_TITLES_PER_TICK = 200     # 10 вызовов AI по 20 названий
AI_NORMALIZE_INTERVAL_SECONDS = 30

def queue_titles_for_ai(prods) -> int:
    """Ставит в очередь товары, для которых эвристика не нашла канонический ключ."""
    from model_matching import extract_canonical_key
    added = 0
    for p in prods:
        title = (p.get("title") or "").strip()
        if not title or p.get("canonical_key") or extract_canonical_key(title):
            continue
        if title not in _ai_pending and len(_ai_pending) >= AI_PENDING_MAX_TITLES:
            continue
        _ai_pending.setdefault(title, set()).add(str(p["id"]))
        added += 1
    return added

async def process_ai_pending(max_titles: int = AI_NORMALIZE_TITLES_PER_TICK) -> int:
    """Один проход фоновой нормализации. Возвращает число товаров, получивших ключ."""
    from database import update_products_canonical_keys
    batch = list(_ai_pending)[:max_titles]
    if not batch:
        return 0
    results = await ai_service.normalize_product_titles_batch(
        batch, for_scan=True, max_ai_calls=-(-len(batch) // 20))
    pairs = []
    for title in batch:
        if title not in results:
            continue  # AI не дошел до названия (лимит) — остается в очереди
        ids = _ai_pending.pop(title, set())
        if results[title]:
            pairs.extend((results[title], pid) for pid in ids)
    if pairs:
        await asyncio.to_thread(update_products_canonical_keys, pairs)
    return len(pairs)

async def ai_normalize_background_worker(app):
    """Фоновая AI-нормализация названий с отдельным урезанным бюджетом (не мешает пользователям)."""
    while True:
        try:
            await asyncio.sleep(AI_NORMALIZE_INTERVAL_SECONDS)
            if _ai_pending:
                updated = await process_ai_pending()
                if updated:
                    print(f"[AI] Канонические ключи проставлены {updated} товарам, в очереди {len(_ai_pending)} названий")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[AI] Ошибка фоновой нормализации: {type(e).__name__}")

async def _scan_shop(key, candidate_settings, semaphore):
    """Обходит все категории одного магазина и отмечает результат в базе."""
    scraper_cls, categories, shop_name = SHOP_REGISTRY[key]
    async with semaphore:
        scraper = scraper_cls()
        started = time.monotonic()
        collected = 0
        failed_categories = []
        limited = False
        record_shop_scan_start(key)
        scan_state["current_shop"] = shop_name

        for cat in categories:
            scan_state["current_category"] = cat["name"]
            try:
                prods = await scraper.scrape(cat["name"], cat["url"], max_pages=cat.get("max_pages"))
                error = getattr(prods, "error", None)
                complete = getattr(prods, "complete", False)
                if not prods and not complete:
                    error = error or "Пустая выдача: требуется проверка"
                scan_state["total_scanned"] += len(prods)
                collected += len(prods)
                await _save_and_detect(prods, shop_name, candidate_settings)
                await asyncio.to_thread(reconcile_source, key, cat["url"], prods, complete and not error)
                if error:
                    failed_categories.append(f"{cat['name']}: {error}")
                elif not complete:
                    limited = True
                await asyncio.sleep(0.5)
            except Exception as e:
                failed_categories.append(f"{cat['name']}: {type(e).__name__}")
                print(f"[{shop_name}] Ошибка категории {cat['name']}: {type(e).__name__}")
            finally:
                scan_state["current_step"] += 1
                scan_state["progress_pct"] = int((scan_state["current_step"] / max(1, scan_state["total_steps"])) * 100)

        duration = time.monotonic() - started
        error = "; ".join(failed_categories[:3]) or None
        status = ("partial" if collected else "failed") if error else "limited" if limited else "complete"
        record_shop_scan_result(key, collected, duration, error, status)
        print(f"[{shop_name}] {status}: {collected} товаров за {duration:.0f}с")
        return collected

async def _do_scan_task(shop_keys=None):
    global scan_state
    # Проверка и установка флага до первого await — защита от параллельного запуска двух сканирований
    if scan_state["is_running"]:
        return
    scan_state["is_running"] = True
    scan_state["total_scanned"] = 0
    scan_state["anomalies_found"] = 0
    scan_state["current_step"] = 0
    scan_state["progress_pct"] = 0
    scan_state["error"] = None

    settings = load_settings()
    candidate_settings = get_candidate_settings(settings)
    keys = list(dict.fromkeys(k for k in (shop_keys if shop_keys is not None else enabled_shop_keys(settings)) if k in SHOP_REGISTRY))

    scan_state["total_steps"] = max(1, sum(len(SHOP_REGISTRY[k][1]) for k in keys))
    print(f"[Scan] Старт обхода {len(keys)} магазинов: {', '.join(SHOP_REGISTRY[k][2] for k in keys)}")

    try:
        semaphore = asyncio.Semaphore(SHOP_CONCURRENCY)
        results = await asyncio.gather(*[_scan_shop(k, candidate_settings, semaphore) for k in keys], return_exceptions=True)
        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                print(f"[Scan] Магазин {SHOP_REGISTRY[key][2]} упал: {res}")
                record_shop_scan_result(key, 0, 0, str(res))

        scan_state["progress_pct"] = 100
        scan_state["last_completed"] = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[Scan] Цикл завершен: {scan_state['total_scanned']} товаров, {scan_state['anomalies_found']} новых аномалий")
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        await asyncio.sleep(1.0)
        scan_state["is_running"] = False
        scan_state["current_shop"] = ""
        scan_state["current_category"] = ""

@routes.post("/api/scan/start")
@require_admin
async def start_scan_handler(request):
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running"})

    shops = None
    if request.can_read_body:
        try:
            payload = await request.json()
            shops = payload.get("shops")
            if shops is not None and (not isinstance(shops, list) or not shops
                or any(not isinstance(k, str) or k not in SHOP_REGISTRY for k in shops)):
                raise ValueError("Некорректный список магазинов")
        except (ValueError, AttributeError, TypeError):
            return web.json_response({"message": "Укажите непустой список известных магазинов"}, status=400)

    asyncio.create_task(_do_scan_task(shops))
    return web.json_response({"status": "started"})

@routes.get("/api/admin/shops")
@require_admin
async def admin_shops_handler(request):
    """Состояние обхода по каждому магазину."""
    return web.json_response({
        "shops": [
            {**row, "name": SHOP_REGISTRY[row["shop_key"]][2], "categories": len(SHOP_REGISTRY[row["shop_key"]][1])}
            for row in get_shops_scan_report(list(SHOP_REGISTRY))
        ],
        "enabled": enabled_shop_keys(),
        "notifications": notification_stats(),
        "scan_state": scan_state,
    })

@routes.post("/api/scan/shopkz-yml")
@require_admin
async def sync_shopkz_yml_handler(request):
    """Сверхбыстрая выгрузка всего каталога shop.kz через официальный YML фид."""
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running", "message": "Сканирование уже выполняется"})
    asyncio.create_task(_do_scan_task(["shopkz"]))
    return web.json_response({"status": "started", "message": "Обход Белого Ветра запущен"})

@routes.get("/api/logs")
@require_admin
async def logs_handler(request):
    """Возвращает системные логи в реальном времени с поддержкой since_id для инкрементальной передачи."""
    try:
        since_id = int(request.query.get("since", 0))
    except (ValueError, TypeError):
        since_id = 0

    try:
        limit = int(request.query.get("limit", 250))
    except (ValueError, TypeError):
        limit = 250

    level = request.query.get("level", None)
    query = request.query.get("q", None)

    logs = log_buffer.get_logs(since_id=since_id, limit=limit, level=level, query=query)
    return web.json_response({
        "logs": logs,
        "latest_id": log_buffer._counter,
        "total_buffer": len(log_buffer._buffer)
    })

@routes.post("/api/logs/clear")
@require_admin
async def logs_clear_handler(request):
    """Очищает кольцевой буфер логов."""
    log_buffer.clear()
    return web.json_response({"status": "ok", "message": "Буфер логов очищен"})

@routes.get("/api/logs/export")
@require_admin
async def logs_export_handler(request):
    """Экспортирует логи в текстовый файл .txt для скачивания."""
    text = log_buffer.export_text()
    return web.Response(
        text=text,
        content_type="text/plain",
        charset="utf-8",
        headers={"Content-Disposition": 'attachment; filename="kz_price_hunter_logs.txt"'}
    )

async def auto_scan_background_worker(app):
    """Фоновый монитор: следит за свежестью КАЖДОГО магазина и догоняет отставшие.

    Раньше свежесть считалась по всей базе (`MAX(updated_at)`), поэтому прерванный цикл
    (например, перезапуск контейнера при деплое) оставлял часть магазинов необойденной:
    база выглядела свежей и новый цикл не запускался.
    """
    print(f"[AutoScan] 🤖 Автономный фоновый монитор запущен (порог устаревания магазина: {get_scan_interval_seconds() // 60} мин)...")
    while True:
        try:
            await asyncio.sleep(20)
            if scan_state.get("is_running"):
                continue

            settings = load_settings()
            max_age_seconds = get_scan_interval_seconds(settings)
            stale = get_stale_shops(enabled_shop_keys(settings), max_age_seconds)
            if not stale:
                continue

            names = ", ".join(SHOP_REGISTRY[k][2] for k in stale[:4]) + ("..." if len(stale) > 4 else "")
            print(f"[AutoScan] ⏰ Требуют обновления {len(stale)} магазинов (порог {max_age_seconds // 60} мин): {names}")
            asyncio.create_task(_do_scan_task(stale))
        except asyncio.CancelledError:
            print("[AutoScan] Фоновый монитор остановлен.")
            break
        except Exception as e:
            print(f"[AutoScan] Ошибка в фоновом мониторе: {e}")
            await asyncio.sleep(15)

async def background_tasks(app):
    from telegram_bot import run_telegram_bot_task
    tasks = [asyncio.create_task(auto_scan_background_worker(app)),
             asyncio.create_task(ai_normalize_background_worker(app)),
             asyncio.create_task(notification_worker()),
             asyncio.create_task(run_telegram_bot_task())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

def create_app():
    init_db()
    app = web.Application(middlewares=[auth_middleware])
    if ALLOW_DEV_LOGIN:
        print("[Auth] 🧑‍💻 Вход разработчика включен (локальный запуск): кнопка «Вход разработчика» в шапке, права администратора")
    elif not ADMIN_TELEGRAM_IDS:
        print("[Auth] ⚠️ ADMIN_TELEGRAM_IDS не задан — администраторов нет, общие настройки и логи недоступны")
    if not get_bot_token():
        print("[Auth] ⚠️ TELEGRAM_BOT_TOKEN не задан — вход через Telegram и уведомления отключены")
    app.cleanup_ctx.append(background_tasks)
    app.add_routes(routes)
    return app
