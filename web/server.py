from __future__ import annotations
import asyncio
import json
import os
import socket
import uuid
from typing import Dict, Any, List, Optional, Tuple, Set
import datetime
import time
from pathlib import Path
from aiohttp import web
from security_logging import redact_secrets

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
    ISPACE_CATEGORIES,
    FORTE_CATEGORIES,
    VKUSMART_CATEGORIES,
    TWELVE_MONTHS_CATEGORIES,
    ZETA_CATEGORIES,
    KOMFORT_CATEGORIES,
    LEMANA_PRO_CATEGORIES,
    ARBUZ_CATEGORIES,
    MASTEROK_CATEGORIES,
    MAGNUM_CATEGORIES,
    INTERTOP_CATEGORIES,
    MARWIN_CATEGORIES,
    ITEKA_CATEGORIES,
    MEBEL_CATEGORIES,
    DETMIR_CATEGORIES,
    ASKONA_CATEGORIES,
    ZOOMARKET_CATEGORIES,
    PLANETA_CATEGORIES,
    MASTER_CATEGORIES,
    get_wave_plan,
    get_wave_interval_seconds,
    CYCLE_BUDGET_HOURS,
    DEFAULT_HOT_CATEGORIES
)
from version import get_version_info
from database import (
    init_db,
    reconcile_source,
    get_source_baseline,
    record_source_scan,
    notifications_muted,
    acquire_scheduler_lease,
    release_scheduler_lease,
    get_metadata,
    set_metadata,
    notification_stats,
    get_stats,
    get_alerts,
    dismiss_alert,
    get_products_list,
    get_products_count,
    get_product_by_id,
    was_alert_sent_recently,
    record_alert,
    record_shop_scan_start,
    record_shop_scan_result,
    get_stale_shops,
    get_shops_scan_report,
    upsert_telegram_user,
    save_user_settings,
    replace_user_settings,
    list_users,
    get_user,
    set_user_blocked,
    create_session,
    delete_session
)
from detector import check_anomaly, check_market_arbitrage
from offer_identity import assign_offer_ids
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
from scrapers.fortemarket import ForteMarketScraper
from scrapers.vkusmart import VkusmartScraper
from scrapers.twelve_months import TwelveMonthsScraper
from scrapers.zeta import ZetaScraper
from scrapers.komfort import KomfortScraper
from scrapers.lemanapro import LemanaProScraper
from scrapers.arbuz import ArbuzScraper
from scrapers.masterok import MasterOkScraper
from scrapers.magnum import MagnumScraper
from scrapers.intertop import IntertopScraper
from scrapers.marwin import MarwinScraper
from scrapers.iteka import ITekaScraper
from scrapers.mebel import MebelScraper
from scrapers.detmir import DetmirScraper
from scrapers.askona import AskonaScraper
from scrapers.zoomarket import ZooMarketScraper
from scrapers.planeta import PlanetaScraper
from search_engine import get_best_price_summary
import ai_service
import data_quality
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
    browser_read_action_allowed,
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
    "error": None,
    "scan_type": "manual",  # "manual", "auto", "category"
    "target_categories": None,
    "wave_info": None,
}

# Состояние ротации волн
wave_state = {
    "current_wave_index": 0,
    "current_cycle": 1,
    "last_wave_categories": [],
    "next_wave_categories": [],
    "cycle_budget_hours": CYCLE_BUDGET_HOURS,
}

# Прямой опрос магазинов создает нагрузку на их сайты — ограничиваем частоту
live_search_limiter = RateLimiter(max_calls=10, period=600)   # на пользователя
search_limiter = RateLimiter(max_calls=60, period=60)         # на IP
consultant_limiter = RateLimiter(max_calls=10, period=60)
telegram_test_limiter = RateLimiter(max_calls=3, period=60)
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

@routes.get("/privacy")
async def privacy_handler(request):
    return web.FileResponse(TEMPLATES_DIR / "privacy.html")

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
    stats = await asyncio.to_thread(get_stats, user_settings_for(request), request.query.get("city") or None)
    stats["scan_state"] = scan_state
    settings = load_settings()
    try:
        wave_state["current_wave_index"] = int(get_metadata("wave_index", wave_state["current_wave_index"]))
        wave_state["current_cycle"] = int(get_metadata("wave_cycle", wave_state.get("current_cycle", 1)))
    except (TypeError, ValueError):
        pass

    plan = get_wave_plan(
        enabled_categories=settings.get("enabled_categories"),
        hot_categories=settings.get("hot_categories", DEFAULT_HOT_CATEGORIES),
        wave_index=wave_state["current_wave_index"],
        wave_size=settings.get("wave_size", 2)
    )
    wave_interval_sec = get_wave_interval_seconds(settings, plan["total_waves"])
    stats["wave_info"] = {
        "wave_mode": settings.get("wave_mode", "rolling"),
        "current_cycle": wave_state.get("current_cycle", 1),
        "wave_index": plan["wave_index"],
        "total_waves": plan["total_waves"],
        "cycle_budget_hours": CYCLE_BUDGET_HOURS,
        "wave_interval_minutes": wave_interval_sec // 60,
        "hot_categories": [MASTER_CATEGORIES[c]["name"] for c in plan["hot_categories"] if c in MASTER_CATEGORIES],
        "wave_categories": [MASTER_CATEGORIES[c]["name"] for c in plan["wave_categories"] if c in MASTER_CATEGORIES],
        "next_wave_categories": [MASTER_CATEGORIES[c]["name"] for c in plan["next_wave_categories"] if c in MASTER_CATEGORIES],
    }
    return web.json_response(stats)

@routes.get("/api/alerts")
async def alerts_handler(request):
    city = request.query.get("city", None)
    alert_type = request.query.get("type", None)
    limit = _int_param(request, "limit", 150, minimum=1, maximum=500)
    alerts = get_alerts(limit=limit, city=city, alert_type=alert_type, user_settings=user_settings_for(request))
    return web.json_response(alerts)

@routes.get("/api/deals")
async def deals_handler(request):
    shop = request.query.get("shop", None)
    city = request.query.get("city", None)
    category = request.query.get("category", None)
    search = request.query.get("search", None)
    deal_type = request.query.get("type", "all")
    sort_by = request.query.get("sort", "discount_desc")
    limit = _int_param(request, "limit", 60, minimum=1, maximum=300)
    offset = _int_param(request, "offset", 0, minimum=0, maximum=10000)

    from database import get_store_deals
    data = await asyncio.to_thread(
        get_store_deals,
        shop=shop,
        city=city,
        category=category,
        search=search,
        deal_type=deal_type,
        sort_by=sort_by,
        limit=limit,
        offset=offset
    )
    return web.json_response(data)

@routes.post("/api/alerts/dismiss")
@require_admin
async def dismiss_alert_handler(request):
    try:
        data = await request.json()
        alert_id = int(data.get("id", 0))
    except Exception:
        return web.json_response({"error": "Некорректный JSON или ID"}, status=400)

    if alert_id <= 0:
        return web.json_response({"error": "ID должен быть положительным числом"}, status=400)

    dismiss_alert(alert_id)
    return web.json_response({"status": "ok", "dismissed_id": alert_id})

@routes.delete("/api/alerts/{id}")
@require_admin
async def delete_alert_handler(request):
    try:
        alert_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "Некорректный ID"}, status=400)

    dismiss_alert(alert_id)
    return web.json_response({"status": "ok", "dismissed_id": alert_id})

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

async def fetch_product_description_live(prod: Dict[str, Any]) -> str:
    """Описание со страницы магазина: белый список доменов, проверка редиректов, лимиты (product_details)."""
    from product_details import get_description
    return await get_description(prod)

@routes.get("/api/products/{id}")
async def product_detail_handler(request):
    pid = request.match_info.get("id")
    if not pid:
        return web.json_response({"error": "Product ID is required"}, status=400)
    prod = get_product_by_id(pid)
    if not prod:
        return web.json_response({"error": "Product not found"}, status=404)
    data_quality.annotate(prod)

    # Если описания нет в базе, подтягиваем его вживую со страницы магазина и кэшируем в БД
    if not prod.get("description") and prod.get("url"):
        fetched_desc = await fetch_product_description_live(prod)
        if fetched_desc:
            prod["description"] = fetched_desc
            from database import get_connection

            def _save_description():
                with get_connection() as conn:
                    conn.execute("UPDATE products SET description = ? WHERE id = ?", (fetched_desc, pid))
                    conn.commit()
            try:
                await asyncio.to_thread(_save_description)
            except Exception as e:
                print(f"[Details] Описание не сохранено: {type(e).__name__}")

    from database import get_price_observations
    prod["price_history"] = await asyncio.to_thread(get_price_observations, pid, 50)
    return web.json_response(prod)

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
            SELECT id, shop, title, current_price, old_price_on_site, url, image_url, city, canonical_key, updated_at
            FROM products
            WHERE canonical_key = ? {city_clause} AND current_price > 0 AND """ + active_product_clause() + """
            ORDER BY current_price ASC
        """, params).fetchall()

    from model_matching import same_model
    reference = title or (rows[0]["title"] if rows else "")
    items = [data_quality.annotate(dict(r)) for r in rows if same_model(reference, r["title"])]
    # Сравнение цен — только по неустаревшим предложениям (P02); устаревшие остаются в списке с бейджем
    priced = [it for it in items if it["freshness"] != data_quality.STALE]
    min_p = priced[0]["current_price"] if priced else 0
    max_p = priced[-1]["current_price"] if priced else 0
    diff = max_p - min_p if len(priced) > 1 else 0
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

    if live and not user:
        return web.json_response({"error": "Прямой опрос магазинов доступен после входа через Telegram"}, status=401)
    if (live or use_ai) and not browser_read_action_allowed(request):
        return web.json_response({"error": "Запустите обновление или AI-поиск со страницы сайта"}, status=403)

    wait = search_limiter.retry_after(f"ip:{client_ip(request)}")
    if wait:
        return web.json_response({"error": f"Слишком много запросов, повторите через {int(wait) + 1} сек"}, status=429)

    if live:
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
            user_search=True,     # обращение человека: одна запись в аналитике спроса (P06)
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
    print("[Auth] Пользователь вошёл в систему")
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
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=401)
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
    uid = DEV_ADMIN_ID
    existing = get_user(uid)
    if existing and existing.get("is_blocked"):
        return web.json_response({"status": "error", "message": "Локальная учётная запись заблокирована"}, status=403)
    user = upsert_telegram_user(
        {"id": uid, "username": "dev", "first_name": "Разработчик"},
        initial_settings=None if existing else {**_initial_settings_for(uid), "telegram_notify_enabled": False}
    )
    return _login_response(request, user)

@routes.post("/api/auth/logout")
async def logout_handler(request):
    delete_session(request.cookies.get(SESSION_COOKIE))
    response = web.json_response({"status": "ok"})
    clear_session_cookie(response)
    return response

# ===== Мои данные: выгрузка и удаление аккаунта (M12) =====

@routes.get("/api/me/export")
@require_login
async def export_my_data_handler(request):
    from database import export_user_data
    data = await asyncio.to_thread(export_user_data, request["user"]["id"])
    if data is None:
        return web.json_response({"status": "error", "message": "Аккаунт не найден"}, status=404)
    response = web.json_response(data, dumps=lambda obj: json.dumps(obj, ensure_ascii=False, indent=2))
    response.headers["Content-Disposition"] = 'attachment; filename="kz-price-hunter-my-data.json"'
    return response


@routes.post("/api/me/delete")
@require_login
async def delete_my_account_handler(request):
    """Удаление аккаунта самим пользователем: нужно явное подтверждение в теле запроса."""
    try:
        confirmed = (await request.json()).get("confirm") is True
    except Exception:
        confirmed = False
    if not confirmed:
        return web.json_response({"status": "error", "message": "Нужно подтверждение удаления"}, status=400)
    from database import delete_user_account
    counts = await asyncio.to_thread(delete_user_account, request["user"]["id"])
    print("[Auth] Пользователь удалил свой аккаунт")
    response = web.json_response({"status": "ok", "deleted": counts})
    clear_session_cookie(response)
    return response

# ===== Личные настройки =====

@routes.post("/api/me/settings")
@require_login
async def save_my_settings_handler(request):
    try:
        data = await request.json()
        clean = validate_user_settings(data, (request["user"] or {}).get("settings"))
        if int(request["user"]["id"]) == DEV_ADMIN_ID:
            clean["telegram_notify_enabled"] = False
    except ValueError as e:
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=400)
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    saved = save_user_settings(request["user"]["id"], clean)
    return web.json_response({"status": "ok", "settings": saved})

@routes.get("/api/me/watches")
@require_login
async def my_watches_handler(request):
    """Мои наблюдения и последние срабатывания (P07). Чужие не видны: выборка всегда по своему id."""
    from database import list_watches, watch_events
    user_id = request["user"]["id"]
    watches, events = await asyncio.to_thread(
        lambda: (list_watches(user_id), watch_events(user_id, limit=30)))
    import watches as watch_rules
    return web.json_response({
        "status": "ok", "watches": watches, "events": events,
        "limits": {"max": watch_rules.MAX_WATCHES_PER_USER},
        "kinds": watch_rules.KINDS, "conditions": watch_rules.CONDITIONS, "labels": watch_rules.LABELS,
    }, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/me/watches")
@require_login
async def create_watch_handler(request):
    from database import create_watch
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    try:
        watch = await asyncio.to_thread(create_watch, request["user"]["id"], data)
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    return web.json_response({"status": "ok", "watch": watch},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/me/watches/{watch_id}")
@require_login
async def update_watch_handler(request):
    from database import update_watch
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    try:
        watch = await asyncio.to_thread(update_watch, request["user"]["id"],
                                        int(request.match_info["watch_id"]), data)
    except (ValueError, TypeError) as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    if not watch:
        return web.json_response({"status": "error", "message": "Наблюдение не найдено"}, status=404)
    return web.json_response({"status": "ok", "watch": watch},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.delete("/api/me/watches/{watch_id}")
@require_login
async def delete_watch_handler(request):
    from database import delete_watch
    try:
        removed = await asyncio.to_thread(delete_watch, request["user"]["id"],
                                          int(request.match_info["watch_id"]))
    except (ValueError, TypeError):
        return web.json_response({"status": "error", "message": "Наблюдение не найдено"}, status=404)
    if not removed:
        return web.json_response({"status": "error", "message": "Наблюдение не найдено"}, status=404)
    return web.json_response({"status": "ok"})


@routes.post("/api/me/settings/reset")
@require_login
async def reset_my_settings_handler(request):
    """Личные настройки к значениям по умолчанию (P05); включённость Telegram-уведомлений сохраняется."""
    from config import USER_DEFAULTS, USER_RESET_KEEP
    user = request["user"]
    from config import merge_user_settings
    current = merge_user_settings(user.get("settings") or {})
    defaults = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
                for k, v in USER_DEFAULTS.items()}
    defaults.update({k: current[k] for k in USER_RESET_KEEP})
    saved = await asyncio.to_thread(replace_user_settings, user["id"], defaults)
    return web.json_response({"status": "ok", "settings": saved})


@routes.post("/api/me/test-telegram")
@require_login
async def test_my_telegram_handler(request):
    user = request["user"]
    if int(user["id"]) <= 0:
        return web.json_response({"status": "error", "message": "Для уведомлений войдите через Telegram"}, status=400)
    retry = telegram_test_limiter.retry_after(str(user["id"]))
    if retry:
        return web.json_response({"status": "error", "message": "Подождите перед повторной проверкой"}, status=429, headers={"Retry-After": str(max(1, int(retry) + 1))})
    text = (
        "🎯 <b>KZ Price Hunter</b>\n\n"
        "✅ Уведомления настроены! Сюда будут приходить ценовые ошибки и скидки по вашим порогам."
    )
    try:
        res = await asyncio.to_thread(telegram_api, "sendMessage", {"chat_id": user["id"], "text": text, "parse_mode": "HTML"})
    except Exception as e:
        return web.json_response({"status": "error", "message": "Не удалось связаться с Telegram. Повторите позже."}, status=500)
    if res.status_code == 200:
        return web.json_response({"status": "ok", "message": "Тестовое сообщение отправлено в ваш Telegram"})
    if res.status_code == 403:
        return web.json_response({"status": "error", "message": "Бот не может написать вам: откройте бота в Telegram и нажмите «Start»"}, status=400)
    return web.json_response({"status": "error", "message": f"Telegram отклонил запрос (HTTP {res.status_code})"}, status=400)

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

# Настройки со своей процедурой перехода: их нельзя менять общим сохранением, иначе шаг включения
# сменился бы без проверки перехода и без снимка «до», а автооткат остался бы без основания (M03).
GUARDED_SETTINGS = {
    "adaptive_scheduler_stage": "POST /api/admin/scheduler/rollout",
    "ai_matching_mode": "POST /api/admin/monitoring/matching/ai",
}


@routes.post("/api/admin/config")
@require_admin
async def post_admin_config_handler(request):
    try:
        data = await request.json()
        guarded = [k for k in GUARDED_SETTINGS if k in (data or {})]
        if guarded:
            where = ", ".join(f"«{k}» — {GUARDED_SETTINGS[k]}" for k in guarded)
            return web.json_response(
                {"status": "error",
                 "message": f"Эти настройки меняются своим переключателем: {where}"}, status=400)
        current = load_settings()
        for k in ("gemini_api_key", "openai_api_key"):
            if k in data and ("..." in str(data[k]) or "***" in str(data[k])):
                data[k] = current.get(k, "")
        saved = save_settings(data)
        # Модель, отвергнутая по 404, исключается из ротации на сутки. Владелец правит ключ или
        # выбирает другую модель именно здесь — если не снять исключения, правка не подействует
        # до завтра, и это выглядело бы как «ничего не изменилось».
        if any(k.startswith(("gemini_", "openai_", "ai_")) for k in (data or {})):
            ai_service.clear_model_cooldowns()
        public_settings = dict(saved)
        for key in ("gemini_api_key", "openai_api_key"):
            if public_settings.get(key):
                public_settings[key] = "***"
        return web.json_response({"status": "ok", "settings": public_settings})
    except ValueError as e:
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": "Не удалось сохранить настройки"}, status=400)

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
    if not anomaly:
        return False
    # Проверка дублей, запись алерта и подбор получателей — запросы к БД, выполняются вне event loop
    return await asyncio.to_thread(_process_anomaly_sync, p, anomaly, shop_name)


def _process_anomaly_sync(p, anomaly, shop_name):
    if not anomaly or was_alert_sent_recently(p["id"], p["price"]):
        return False

    alert_id = record_alert(
        product_id=p["id"],
        alert_type=anomaly["type"],
        old_price=anomaly["old_price"],
        new_price=anomaly["new_price"],
        discount_pct=anomaly["drop_pct"],
        savings_kzt=anomaly["savings"],
        shop=p.get("shop", shop_name),
        city=p.get("city", "Астана"),
        competitor_shop=anomaly.get("competitor_shop"),
        competitor_seen_at=anomaly.get("competitor_seen_at"),
        # После пересборки каталога первый цикл не рассылает уже известные скидки повторно
        deliveries=[] if notifications_muted() else prepare_deliveries(p, anomaly)
    )
    if not alert_id:
        return False  # дубль, записанный параллельно, или отбракованная цена
    scan_state["anomalies_found"] += 1
    try:
        from telemetry import telemetry, EVENT_ANOMALY_DETECTED, SEVERITY_INFO, COMPONENT_DETECTOR
        telemetry.record_event(
            EVENT_ANOMALY_DETECTED, SEVERITY_INFO, COMPONENT_DETECTOR,
            f"Алерт {anomaly['type']}: −{anomaly['drop_pct']}%", shop=p.get("shop", shop_name),
            data={"alert_id": alert_id, "product_id": str(p["id"]), "type": anomaly["type"],
                  "old_price": anomaly["old_price"], "new_price": anomaly["new_price"],
                  "drop_pct": anomaly["drop_pct"], "competitor_shop": anomaly.get("competitor_shop")},
            throttle_key="anomaly", throttle_per_minute=100)
    except Exception:
        pass
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
    "dns": (DNSScraper, DNS_CATEGORIES, "DNS Казахстан"),
    "flip": (FlipScraper, FLIP_CATEGORIES, "Flip.kz"),
    "halyk": (HalykScraper, HALYK_CATEGORIES, "Halyk Market"),
    "tgrad": (TgradScraper, TGRAD_CATEGORIES, "Tgrad"),
    "ants": (AntsScraper, ANTS_CATEGORIES, "ANTS"),
    "itmag": (ItmagScraper, ITMAG_CATEGORIES, "ITMag"),
    "ispace": (ISpaceScraper, ISPACE_CATEGORIES, "iSpace"),
    "fortemarket": (ForteMarketScraper, FORTE_CATEGORIES, "Forte Market"),
    "vkusmart": (VkusmartScraper, VKUSMART_CATEGORIES, "Вкусмарт"),
    "twelve_months": (TwelveMonthsScraper, TWELVE_MONTHS_CATEGORIES, "12 Месяцев"),
    "zeta": (ZetaScraper, ZETA_CATEGORIES, "Zeta"),
    "komfort": (KomfortScraper, KOMFORT_CATEGORIES, "Комфорт"),
    "lemanapro": (LemanaProScraper, LEMANA_PRO_CATEGORIES, "Лемана ПРО"),
    "arbuz": (ArbuzScraper, ARBUZ_CATEGORIES, "Arbuz"),
    "masterok": (MasterOkScraper, MASTEROK_CATEGORIES, "MasterOK"),
    "magnum": (MagnumScraper, MAGNUM_CATEGORIES, "Магнум"),
    "intertop": (IntertopScraper, INTERTOP_CATEGORIES, "Интертоп"),
    "marwin": (MarwinScraper, MARWIN_CATEGORIES, "Меломан"),
    "iteka": (ITekaScraper, ITEKA_CATEGORIES, "i-Teka"),
    "mebel": (MebelScraper, MEBEL_CATEGORIES, "Mebel.kz"),
    "detmir": (DetmirScraper, DETMIR_CATEGORIES, "Детский мир"),
    "askona": (AskonaScraper, ASKONA_CATEGORIES, "Askona"),
    "zoomarket": (ZooMarketScraper, ZOOMARKET_CATEGORIES, "Зоомаркет"),
    "planeta": (PlanetaScraper, PLANETA_CATEGORIES, "Планета Электроники"),
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

    changes = {"items": len(prods), "new": 0, "price_down": 0, "price_up": 0, "discount_candidates": 0,
               "alerts_recorded": 0, "arbitrage_candidates": 0}
    for p in prods:
        known = str(p["id"]) in history_map
        history = history_map.get(str(p["id"]), {"old_price": p["price"], "first_seen_price": p["price"]})
        if not known:
            changes["new"] += 1
        elif history["old_price"] > p["price"]:
            changes["price_down"] += 1
        elif history["old_price"] < p["price"]:
            changes["price_up"] += 1
        if history["old_price"] > p["price"] or history["first_seen_price"] > p["price"] or (p.get("old_price_on_site") or 0) > p["price"]:
            anomaly = check_anomaly(p, history, custom_settings=candidate_settings)
            changes["discount_candidates"] += bool(anomaly)
            changes["alerts_recorded"] += bool(await _process_anomaly(p, anomaly, shop_name))

    # Личные наблюдения (P07): проверяются только изменившиеся и новые предложения, отдельной задачей
    # вне event loop. Сбой наблюдений не должен ломать обход, поэтому ошибка только логируется.
    changed_ids = [str(p["id"]) for p in prods
                   if str(p["id"]) not in history_map
                   or history_map[str(p["id"])]["old_price"] != p["price"]]
    if changed_ids:
        try:
            from database import evaluate_watches, watched_offers
            await asyncio.to_thread(lambda: evaluate_watches(watched_offers(changed_ids)))
        except Exception as e:
            print(f"[Watches] Ошибка проверки наблюдений: {type(e).__name__}")
            from telemetry import telemetry, COMPONENT_SYSTEM
            telemetry.record_system_error(COMPONENT_SYSTEM, "evaluate_watches", e)

    # Сравнение с рынком для всей пачки — одна задача вне event loop, а не переключение
    # потока на каждый товар (у Белого Ветра ~14 тыс. за обход) (M06)
    def _arbitrage_batch():
        found = []
        for p in prods:
            anomaly = check_market_arbitrage(p, custom_settings=candidate_settings)
            if anomaly:
                found.append((p, anomaly))
        return found

    arbitrage_ids = []
    for p, anomaly in await asyncio.to_thread(_arbitrage_batch):
        changes["arbitrage_candidates"] += 1
        recorded = bool(await _process_anomaly(p, anomaly, shop_name))
        changes["alerts_recorded"] += recorded
        if recorded:
            arbitrage_ids.append(str(p["id"]))

    # Наблюдения за разницей цен между магазинами (P07 V2) срабатывают на записанный алерт, поэтому
    # проверяются после него — до этого места находки ещё не существует.
    if arbitrage_ids:
        try:
            from database import evaluate_watches, watched_offers
            await asyncio.to_thread(lambda: evaluate_watches(watched_offers(arbitrage_ids)))
        except Exception as e:
            print(f"[Watches] Ошибка проверки наблюдений за арбитражем: {type(e).__name__}")
            from telemetry import telemetry, COMPONENT_SYSTEM
            telemetry.record_system_error(COMPONENT_SYSTEM, "evaluate_watches_arbitrage", e)
    _record_price_changes(shop_name, changes)


def _record_price_changes(shop_name, changes) -> None:
    """Сводка цен и сопоставления по пачке категории (P01); scan_id/category — из контекста обхода.

    Отдельное событие на каждую смену цены не пишется: у крупных магазинов их тысячи за обход.
    """
    try:
        from telemetry import telemetry, EVENT_PRICE_CHANGED, SEVERITY_INFO, COMPONENT_DETECTOR
        telemetry.record_event(
            EVENT_PRICE_CHANGED, SEVERITY_INFO, COMPONENT_DETECTOR,
            f"[{shop_name}] цены: ↓{changes['price_down']} ↑{changes['price_up']} новых {changes['new']}, "
            f"алертов {changes['alerts_recorded']}",
            shop=shop_name, data=changes)
    except Exception:
        pass

# Очередь фоновой AI-нормализации: название -> ID товаров с этим названием
_ai_pending: Dict[str, set] = {}
AI_PENDING_MAX_TITLES = 20000
AI_PENDING_MAX_IDS_PER_TITLE = 200
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
        ids = _ai_pending.setdefault(title, set())
        if len(ids) >= AI_PENDING_MAX_IDS_PER_TITLE:
            continue  # у одного названия может быть много предложений (города) — число id ограничено
        ids.add(str(p["id"]))
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
    import environment
    while True:
        try:
            await asyncio.sleep(AI_NORMALIZE_INTERVAL_SECONDS)
            await asyncio.to_thread(environment.heartbeat, environment.AI_NORMALIZE,
                                    f"в очереди {len(_ai_pending)} названий")
            if _ai_pending:
                updated = await process_ai_pending()
                if updated:
                    print(f"[AI] Канонические ключи проставлены {updated} товарам, в очереди {len(_ai_pending)} названий")
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[AI] Ошибка фоновой нормализации: {type(e).__name__}")
            from telemetry import telemetry, COMPONENT_AI
            telemetry.record_system_error(COMPONENT_AI, "ai_normalize_worker", e)

def get_categories_overview():
    """Возвращает информацию обо всех мастер-категориях: количество товаров в базе, статус волн, магазины."""
    from database import get_connection
    settings = load_settings()
    enabled_cats = settings.get("enabled_categories") or {k: True for k in MASTER_CATEGORIES}
    hot_cats = set(settings.get("hot_categories", DEFAULT_HOT_CATEGORIES))
    wave_mode = settings.get("wave_mode", "rolling")
    wave_size = settings.get("wave_size", 2)

    name_to_master = {}
    master_to_shops = {k: set() for k in MASTER_CATEGORIES}
    for shop_key, (cls, cats, shop_name) in SHOP_REGISTRY.items():
        for c in cats:
            m = c.get("master")
            if m and m in MASTER_CATEGORIES:
                name_to_master[c["name"]] = m
                master_to_shops[m].add(shop_name)
            elif m == "all":
                for mk in MASTER_CATEGORIES:
                    master_to_shops[mk].add(shop_name)

    def classify(c_name: str) -> str:
        if c_name in name_to_master:
            return name_to_master[c_name]
        cl = (c_name or "").lower()
        if "ноутбук" in cl: return "laptops"
        if "смартфон" in cl or "телефон" in cl or "iphone" in cl: return "smartphones"
        if "телевизор" in cl or "led" in cl or "oled" in cl: return "tvs"
        if "монитор" in cl or "моноблок" in cl: return "monitors"
        if any(k in cl for k in ["наушник", "гарнитур", "акустик", "колонк", "airpods"]): return "audio"
        if any(k in cl for k in ["видеокарт", "процессор", "материнск", "памят", "ssd", "hdd", "диск", "корпус", "блок питания", "охлажден"]): return "pc_components"
        if "планшет" in cl or "ipad" in cl or "час" in cl or "watch" in cl: return "tablets_watches"
        if any(k in cl for k in ["приставк", "консол", "ps5", "xbox"]): return "consoles"
        if any(k in cl for k in ["холодильник", "стиральн", "кондиционер", "посудомоечн", "вытяжк", "плит"]): return "appliances_large"
        if any(k in cl for k in ["пылесос", "утюг", "кофе", "микроволн", "мультиварк", "блендер", "фен", "бритв"]): return "appliances_small"
        if any(k in cl for k in ["принтер", "мфу", "роутер", "маршрутизатор"]): return "office_network"
        if "акци" in cl or "распродаж" in cl: return "actions"
        if any(k in cl for k in ["продукт", "бакале", "чай", "кофе", "сладост"]): return "grocery"
        if any(k in cl for k in ["бытов", "хими", "чистот", "стирк", "уборк", "гигиен", "хранен", "вешалк", "обувниц", "стеллаж"]): return "household"
        if any(k in cl for k in ["инструмент", "дрел", "перфорат", "шуруповерт", "пила", "сварк", "сантехник", "смесител", "строй", "стремянк", "отделочн", "садов", "электротовар"]): return "diy"
        return "other"

    counts = {k: 0 for k in MASTER_CATEGORIES}
    with get_connection() as conn:
        rows = conn.execute("SELECT category, count(*) FROM products WHERE is_active = 1 GROUP BY category").fetchall()
        for r in rows:
            m = classify(r[0] or "")
            if m in counts:
                counts[m] += r[1]

    try:
        wave_state["current_wave_index"] = int(get_metadata("wave_index", wave_state["current_wave_index"]))
        wave_state["current_cycle"] = int(get_metadata("wave_cycle", wave_state.get("current_cycle", 1)))
    except (TypeError, ValueError):
        pass

    plan = get_wave_plan(enabled_categories=enabled_cats, hot_categories=list(hot_cats), wave_index=wave_state["current_wave_index"], wave_size=wave_size)
    wave_interval_sec = get_wave_interval_seconds(settings, plan["total_waves"])
    plan["current_cycle"] = wave_state.get("current_cycle", 1)
    plan["wave_interval_minutes"] = wave_interval_sec // 60
    wave_state["current_cycle"] = wave_state.get("current_cycle", 1)
    wave_state["wave_interval_minutes"] = wave_interval_sec // 60

    categories_list = []
    for cat_id, meta in MASTER_CATEGORIES.items():
        is_enabled = bool(enabled_cats.get(cat_id, True))
        is_hot = cat_id in hot_cats
        is_in_wave = cat_id in plan.get("wave_categories", [])
        is_in_next_wave = cat_id in plan.get("next_wave_categories", [])
        categories_list.append({
            "id": cat_id,
            "name": meta["name"],
            "icon": meta["icon"],
            "description": meta["description"],
            "enabled": is_enabled,
            "is_hot": is_hot,
            "is_in_wave": is_in_wave,
            "is_in_next_wave": is_in_next_wave,
            "products_count": counts.get(cat_id, 0),
            "shops_count": len(master_to_shops.get(cat_id, set())),
        })

    from database import get_tracked_categories, get_hierarchical_categories
    tracked = get_tracked_categories(active_only=False, limit=100)
    tree = get_hierarchical_categories()

    return {
        "categories": categories_list,
        "wave_mode": wave_mode,
        "wave_size": wave_size,
        "wave_plan": plan,
        "wave_state": wave_state,
        "tracked_categories": tracked,
        "tree": tree,
    }

async def _scan_shop(key, candidate_settings, semaphore, target_categories=None):
    """Обходит категории одного магазина (с фильтрацией по target_categories при наличии)."""
    scraper_cls, all_categories, shop_name = SHOP_REGISTRY[key]
    if target_categories is not None:
        categories = [cat for cat in all_categories if cat.get("master") == "all" or cat.get("master") in target_categories]
    else:
        categories = all_categories

    if not categories:
        return 0

    async with semaphore:
        scraper = scraper_cls()
        try:
            return await _scan_shop_categories(key, scraper, categories, shop_name, candidate_settings)
        finally:
            close = getattr(scraper, "close", None)
            if close:
                close()


async def _scan_shop_categories(key, scraper, categories, shop_name, candidate_settings):
    from telemetry import (
        telemetry, current_shop, current_category, current_http_trace, current_scan_id,
        EVENT_SCAN_CATEGORY, EVENT_SCAN_ERROR, SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, COMPONENT_SCRAPER
    )
    started = time.monotonic()
    collected = 0
    failed_categories = []
    limited = False
    record_shop_scan_start(key)
    scan_state["current_shop"] = shop_name
    token_shop = current_shop.set(shop_name)

    try:
        for cat in categories:
            scan_state["current_category"] = cat["name"]
            token_cat = current_category.set(cat["name"])
            # Сводка HTTP страниц категории (коды, число запросов) попадает в событие итога категории (A05)
            http_trace = {}
            token_trace = current_http_trace.set(http_trace)
            cat_started = datetime.datetime.now(datetime.timezone.utc).isoformat()
            try:
                prods = await scraper.scrape(cat["name"], cat["url"], max_pages=cat.get("max_pages"))
                # Предложение = товар магазина + подтверждённый город (id вида kaspi_1@astana)
                assign_offer_ids(prods)
                error = getattr(prods, "error", None)
                complete = getattr(prods, "complete", False)
                if not prods and not complete:
                    error = error or "Пустая выдача: требуется проверка"
                # Качество до снятия товаров (P02): «тихая поломка» с complete не снимает каталог и не обучает норму.
                # Метрики — по всей выдаче (дубли видны), дальше — только первая строка каждого id (C01)
                kind = "complete" if complete else "limited"
                metrics = data_quality.measure(prods)
                prods = data_quality.dedupe(prods)
                baseline = None if error else await asyncio.to_thread(get_source_baseline, key, cat["url"], kind)
                assessment = data_quality.assess(metrics, complete=complete, error=error, baseline=baseline)
                if assessment["quality"] == data_quality.DEGRADED:
                    error = "Качество: " + "; ".join(assessment["reasons"])
                scan_state["total_scanned"] += len(prods)
                collected += len(prods)
                _ensure_lease()  # результаты пишет только владелец аренды (R-H03)
                await _save_and_detect(prods, shop_name, candidate_settings)
                await asyncio.to_thread(reconcile_source, key, cat["url"], prods,
                                        assessment["may_retire"] and not error)
                await asyncio.to_thread(record_source_scan, key, cat["url"], cat["name"], current_scan_id.get(),
                                        cat_started, kind, assessment, metrics)
                if error:
                    failed_categories.append(f"{cat['name']}: {error}")
                elif not complete:
                    limited = True

                telemetry.record_event(
                    event_type=EVENT_SCAN_CATEGORY,
                    severity=SEVERITY_WARNING if error else SEVERITY_INFO,
                    component=COMPONENT_SCRAPER,
                    message=f"[{shop_name}] Категория {cat['name']}: {len(prods)} товаров (complete={complete})",
                    shop=shop_name,
                    category=cat["name"],
                    data={"items_count": len(prods), "complete": complete, "error": error, "http": http_trace,
                          "quality": {"quality": assessment["quality"], "reasons": assessment["reasons"],
                                      "warnings": assessment["warnings"], "baseline": assessment["baseline"],
                                      "basis": assessment["basis"], **metrics}}
                )
                await asyncio.sleep(0.5)
            except LeaseLost:
                raise
            except Exception as e:
                failed_categories.append(f"{cat['name']}: {type(e).__name__}")
                print(f"[{shop_name}] Ошибка категории {cat['name']}: {type(e).__name__}")
                try:
                    await asyncio.to_thread(
                        record_source_scan, key, cat["url"], cat["name"], current_scan_id.get(), cat_started, "limited",
                        data_quality.assess(data_quality.measure([]), complete=False, error=type(e).__name__, baseline=None),
                        data_quality.measure([]))
                except Exception:
                    pass
                telemetry.record_event(
                    event_type=EVENT_SCAN_ERROR,
                    severity=SEVERITY_ERROR,
                    component=COMPONENT_SCRAPER,
                    message=f"[{shop_name}] Ошибка категории {cat['name']}: {type(e).__name__}",
                    data={"error": type(e).__name__, "http": http_trace},
                )
            finally:
                current_http_trace.reset(token_trace)
                current_category.reset(token_cat)
                scan_state["current_step"] += 1
                scan_state["progress_pct"] = int((scan_state["current_step"] / max(1, scan_state["total_steps"])) * 100)
    finally:
        current_shop.reset(token_shop)

    duration = time.monotonic() - started
    error = "; ".join(failed_categories[:3]) or None
    status = ("partial" if collected else "failed") if error else "limited" if limited else "complete"
    record_shop_scan_result(key, collected, duration, error, status)
    print(f"[{shop_name}] {status}: {collected} товаров за {duration:.0f}с")
    return collected

# Аренда планировщика продлевается во время обхода; упавший процесс теряет её через TTL
SCHEDULER_OWNER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
SCHEDULER_LEASE_SECONDS = 180
SCHEDULER_LEASE_RENEW_SECONDS = 60

# Запущенные задачи обхода удерживаются, чтобы при остановке их отменить (не «висят» без владельца)
_scan_tasks: Set[asyncio.Task] = set()


def spawn_scan(*args, **kwargs) -> asyncio.Task:
    task = asyncio.create_task(_do_scan_task(*args, **kwargs))
    _scan_tasks.add(task)
    task.add_done_callback(_scan_tasks.discard)
    return task


class LeaseLost(RuntimeError):
    """Аренда планировщика перешла к другому процессу: этот обход больше не пишет результаты."""


# Состояние аренды текущего обхода (R-H03)
_lease_state = {"lost": False, "last_ok": 0.0}


def _ensure_lease() -> None:
    if _lease_state["lost"]:
        raise LeaseLost("аренда планировщика потеряна")


async def _keep_scheduler_lease(work: asyncio.Task):
    """Продлевает аренду; при отказе (аренда у другого) или долгой ошибке продления — останавливает обход."""
    while True:
        await asyncio.sleep(SCHEDULER_LEASE_RENEW_SECONDS)
        try:
            renewed = await asyncio.to_thread(acquire_scheduler_lease, SCHEDULER_OWNER, SCHEDULER_LEASE_SECONDS)
        except Exception as e:
            renewed = None
            print(f"[Scan] Аренда не продлена: {type(e).__name__}")
        now = time.monotonic()
        if renewed:
            _lease_state["last_ok"] = now
            continue
        if renewed is False or now - _lease_state["last_ok"] >= SCHEDULER_LEASE_SECONDS:
            _lease_state["lost"] = True
            print("[Scan] ⛔ Аренда планировщика потеряна — обход остановлен, результаты больше не записываются")
            work.cancel()
            return


async def _do_scan_task(shop_keys=None, target_categories=None, scan_type="manual"):
    global scan_state, wave_state
    # Проверка и установка флага до первого await — защита от параллельного запуска двух сканирований
    if scan_state["is_running"]:
        return
    scan_state["is_running"] = True
    # Один планировщик на базу: второй процесс (main.py рядом с gui.py, второй контейнер) не обходит.
    # Вызов синхронный и без await до установки флага — гонки внутри процесса нет.
    try:
        leased = acquire_scheduler_lease(SCHEDULER_OWNER, SCHEDULER_LEASE_SECONDS)
    except Exception as e:
        leased = False
        print(f"[Scan] Аренда планировщика недоступна: {type(e).__name__}")
    if not leased:
        scan_state["is_running"] = False
        print("[Scan] Обход уже выполняет другой процесс (аренда планировщика) — пропуск")
        return
    _lease_state.update(lost=False, last_ok=time.monotonic())
    # Один scan_id связывает планировщик, страницы (HTTP) и итог обхода: create_task/to_thread
    # копируют контекст, поэтому значение видно во всех задачах обхода (P01)
    from telemetry import telemetry, current_scan_id, EVENT_SCAN_START, EVENT_SCAN_END, SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ERROR, COMPONENT_SCHEDULER
    scan_id = uuid.uuid4().hex[:16]
    scan_token = current_scan_id.set(scan_id)
    scan_started = time.monotonic()
    telemetry.record_event(EVENT_SCAN_START, SEVERITY_INFO, COMPONENT_SCHEDULER,
                           f"Старт обхода ({scan_type})",
                           data={"scan_type": scan_type, "shop_keys": list(shop_keys or []),
                                 "target_categories": sorted(target_categories or [])})
    # Вся работа — отдельная задача под одним try/finally: исключение в подготовке не оставит
    # флаг «идёт обход» и продление аренды; потеря аренды отменяет работу (R-H03)
    work = asyncio.create_task(_scan_task_body(shop_keys, target_categories, scan_type))
    keeper = asyncio.create_task(_keep_scheduler_lease(work))
    # Исход обхода для телеметрии: отмена и потеря аренды не выдаются за успешное завершение (A04)
    outcome = "cancelled"
    try:
        await work
        outcome = "failed" if scan_state.get("error") else "completed"
    except asyncio.CancelledError:
        if not _lease_state["lost"]:
            raise  # остановка приложения
        outcome = "lease_lost"
        scan_state["error"] = "Обход остановлен: аренда планировщика перешла к другому процессу"
    except Exception as e:
        outcome = "failed"
        scan_state["error"] = f"Ошибка сканирования ({type(e).__name__})"
    finally:
        keeper.cancel()
        if not work.done():
            work.cancel()
        try:
            release_scheduler_lease(SCHEDULER_OWNER)  # удаляет только свою аренду
        except Exception as e:
            print(f"[Scan] Аренда не освобождена: {type(e).__name__}")
        # Запись только в память и не бросает; сброс в БД делает фоновый поток, очистка ниже от него не зависит
        try:
            severity = {"completed": SEVERITY_INFO, "failed": SEVERITY_ERROR}.get(outcome, SEVERITY_WARNING)
            error = scan_state.get("error") if outcome != "cancelled" else None
            telemetry.record_event(EVENT_SCAN_END, severity, COMPONENT_SCHEDULER,
                                   f"Итог обхода ({scan_type}): {outcome}" + (f" — {error}" if error else ""),
                                   data={"scan_type": scan_type, "outcome": outcome,
                                         "total_scanned": scan_state.get("total_scanned", 0),
                                         "anomalies_found": scan_state.get("anomalies_found", 0),
                                         "error": error,
                                         "duration_sec": round(time.monotonic() - scan_started, 1)})
        except Exception:
            pass
        current_scan_id.reset(scan_token)
        await asyncio.sleep(1.0)
        scan_state["is_running"] = False
        scan_state["current_shop"] = ""
        scan_state["current_category"] = ""
        scan_state["scan_type"] = "manual"
        scan_state["target_categories"] = None


def _failed_shop_keys(keys) -> List[str]:
    from database import get_connection
    marks = ",".join("?" * len(keys))
    with get_connection() as conn:
        return [r[0] for r in conn.execute(
            f"SELECT shop_key FROM shop_scans WHERE shop_key IN ({marks}) AND status = 'failed'", list(keys))] if keys else []


def _record_wave_outcome(info, keys, failed) -> None:
    """Итог волны: круг засчитывается только если последняя волна прошла без упавших магазинов."""
    outcome = {**info, "finished_at": time.time(), "shops": len(keys), "failed_shops": failed}
    set_metadata("wave_last_result", json.dumps(outcome, ensure_ascii=False))
    if not info["is_last"]:
        return
    if failed:
        print(f"[Wave] Круг #{info['cycle']} не засчитан: последняя волна с ошибками у {len(failed)} магазинов ({', '.join(failed[:5])})")
        return
    next_cycle = info["cycle"] + 1
    wave_state["current_cycle"] = next_cycle
    set_metadata("wave_cycle", next_cycle)
    set_metadata("wave_last_cycle_completed_at", str(time.time()))
    print(f"[Wave] 🏁 Полный круг #{info['cycle']} завершён: все {info['total_waves']} волн выполнены. Старт круга #{next_cycle}")


async def _scan_task_body(shop_keys, target_categories, scan_type):
    scan_state["total_scanned"] = 0
    scan_state["anomalies_found"] = 0
    scan_state["current_step"] = 0
    scan_state["progress_pct"] = 0
    scan_state["error"] = None
    scan_state["scan_type"] = scan_type
    scan_state["target_categories"] = list(target_categories) if target_categories else None

    wave_plan_info = None
    settings = load_settings()
    candidate_settings = get_candidate_settings(settings)
    keys = list(dict.fromkeys(k for k in (shop_keys if shop_keys is not None else enabled_shop_keys(settings)) if k in SHOP_REGISTRY))

    # Если плановый авто-запуск: рассчитываем волну категорий
    if scan_type == "auto" and target_categories is None:
        wave_mode = settings.get("wave_mode", "rolling")
        if wave_mode == "rolling":
            # Номер волны и круга хранятся в БД: перезапуск (выкат) не сбивает ротацию
            try:
                wave_state["current_wave_index"] = int(get_metadata("wave_index", wave_state["current_wave_index"]))
            except (TypeError, ValueError):
                pass
            try:
                wave_state["current_cycle"] = int(get_metadata("wave_cycle", wave_state.get("current_cycle", 1)))
            except (TypeError, ValueError):
                pass

            plan = get_wave_plan(
                enabled_categories=settings.get("enabled_categories"),
                hot_categories=settings.get("hot_categories", DEFAULT_HOT_CATEGORIES),
                wave_index=wave_state["current_wave_index"],
                wave_size=settings.get("wave_size", 2)
            )
            target_categories = plan["active_categories"]
            wave_state["last_wave_categories"] = plan["wave_categories"]
            wave_state["next_wave_categories"] = plan["next_wave_categories"]
            wave_interval_sec = get_wave_interval_seconds(settings, plan["total_waves"])

            current_idx = plan["wave_index"]
            next_idx = plan["next_wave_index"]
            is_circle_finished = plan["is_last_wave_of_cycle"]

            wave_state["current_wave_index"] = next_idx
            set_metadata("wave_index", next_idx)
            set_metadata("wave_last_run_at", str(time.time()))

            current_cycle_val = wave_state.get("current_cycle", 1)
            # Номер следующей волны сдвигается сразу (справедливая ротация даже после сбоя),
            # а «круг завершён» записывается только после успешного выполнения этой волны (R-M04)
            wave_plan_info = {"cycle": current_cycle_val, "wave_index": current_idx,
                              "total_waves": plan["total_waves"], "is_last": is_circle_finished}

            scan_state["wave_info"] = {
                "current_cycle": current_cycle_val,
                "wave_index": plan["wave_index"],
                "total_waves": plan["total_waves"],
                "cycle_budget_hours": CYCLE_BUDGET_HOURS,
                "wave_interval_minutes": wave_interval_sec // 60,
                "hot_categories": plan["hot_categories"],
                "wave_categories": plan["wave_categories"],
                "next_wave_categories": plan["next_wave_categories"],
                "is_last_wave": is_circle_finished
            }
            print(f"[Wave] 🌊 Круг #{current_cycle_val} | Волна #{plan['wave_index'] + 1}/{plan['total_waves']} (интервал: {wave_interval_sec // 60}м, лимит круга: 24ч): Hot={plan['hot_categories']}, Wave={plan['wave_categories']}")
        else:
            enabled_cats = settings.get("enabled_categories")
            if enabled_cats:
                target_categories = {k for k, v in enabled_cats.items() if v and k in MASTER_CATEGORIES}
            scan_state["wave_info"] = None
    elif scan_type == "category":
        cat_names = [MASTER_CATEGORIES[c]["name"] for c in target_categories if c in MASTER_CATEGORIES]
        print(f"[Scan] ⚡️ On-Demand сбор категории: {', '.join(cat_names) or target_categories}")

    # Подсчитываем точное количество шагов с учетом фильтрации категорий
    total_steps = 0
    for k in keys:
        all_cats = SHOP_REGISTRY[k][1]
        if target_categories is not None:
            filtered = [c for c in all_cats if c.get("master") == "all" or c.get("master") in target_categories]
        else:
            filtered = all_cats
        total_steps += len(filtered)
    scan_state["total_steps"] = max(1, total_steps)

    cat_desc = f" [{len(target_categories)} категорий]" if target_categories else ""
    print(f"[Scan] Старт обхода {len(keys)} магазинов{cat_desc}: {', '.join(SHOP_REGISTRY[k][2] for k in keys)}")

    try:
        semaphore = asyncio.Semaphore(SHOP_CONCURRENCY)
        results = await asyncio.gather(
            *[_scan_shop(k, candidate_settings, semaphore, target_categories=target_categories) for k in keys],
            return_exceptions=True
        )
        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                print(f"[Scan] Магазин {SHOP_REGISTRY[key][2]} упал: {res}")
                record_shop_scan_result(key, 0, 0, str(res))
                from telemetry import telemetry, EVENT_SCAN_ERROR, SEVERITY_ERROR, COMPONENT_SCRAPER
                telemetry.record_event(EVENT_SCAN_ERROR, SEVERITY_ERROR, COMPONENT_SCRAPER,
                                       f"Магазин упал: {type(res).__name__}", shop=SHOP_REGISTRY[key][2],
                                       data={"shop_key": key, "error": type(res).__name__})
        _ensure_lease()
        crashed = [k for k, res in zip(keys, results) if isinstance(res, BaseException)]
        failed = await asyncio.to_thread(_failed_shop_keys, keys)
        if wave_plan_info:
            await asyncio.to_thread(_record_wave_outcome, wave_plan_info, keys, sorted(set(crashed) | set(failed)))

        # Дополнительный этап волны: фоновое обновление порции отслеживаемых категорий из поиска
        try:
            from database import get_due_tracked_categories, mark_tracked_category_scanned, get_tracked_categories_counts
            tracked_counts = await asyncio.to_thread(get_tracked_categories_counts)
            rolling_tracked_count = tracked_counts.get("rolling", 0)
            total_waves_count = max(1, scan_state.get("wave_info", {}).get("total_waves", 1) if scan_state.get("wave_info") else 1)
            tracked_rolling_batch_size = max(1, -(-rolling_tracked_count // total_waves_count)) if rolling_tracked_count > 0 else 3
            due_tracked = await asyncio.to_thread(get_due_tracked_categories, tracked_rolling_batch_size, True)
            if due_tracked:
                print(f"[Wave] 🔍 Обновление {len(due_tracked)} категорий из поиска в текущей волне: {', '.join(c['name'] for c in due_tracked)}")
                from search_engine import search_live_stores
                for cat in due_tracked:
                    try:
                        await search_live_stores(cat["query"], city="Астана")
                        await asyncio.to_thread(mark_tracked_category_scanned, cat["id"])
                    except Exception as ex:
                        print(f"[Wave] Ошибка обновления категории {cat['name']}: {ex}")
        except Exception as e:
            print(f"[Wave] Ошибка обновления tracked categories: {e}")

        # Сроки хранения: история цен — 180 дней, завершённые уведомления — 30 дней
        try:
            from database import prune_price_observations, prune_notification_outbox
            pruned = await asyncio.to_thread(prune_price_observations)
            pruned_outbox = await asyncio.to_thread(prune_notification_outbox)
            from database import prune_source_scans, prune_search_analytics, prune_ai_usage
            await asyncio.to_thread(prune_source_scans)
            await asyncio.to_thread(prune_search_analytics)
            await asyncio.to_thread(prune_ai_usage)
            from database import prune_daily_reports, prune_matching_pairs
            await asyncio.to_thread(prune_daily_reports)
            await asyncio.to_thread(prune_matching_pairs)
            # Спорные пары сопоставления разбирает модель — вне пути сравнения цен (P09, AI-часть)
            try:
                import catalog_ai
                await catalog_ai.resolve_pending()
            except Exception as e:
                print(f"[Matching] Разбор спорных пар не выполнен: {type(e).__name__}")
            # Самопроверка копий: раз в сутки развернуть свежую копию во временную базу (P15)
            from backup_health import verify_backups_if_due
            await asyncio.to_thread(verify_backups_if_due)
            # Признаки ухудшения после включения адаптивного порядка — откат сразу, без ожидания (P11)
            await asyncio.to_thread(check_adaptive_rollback)
            if pruned or pruned_outbox:
                print(f"[DB] Удалено старых наблюдений цен: {pruned}, записей уведомлений: {pruned_outbox}")
        except Exception as e:
            print(f"[DB] Ошибка очистки по срокам хранения: {type(e).__name__}")

        scan_state["progress_pct"] = 100
        scan_state["last_completed"] = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[Scan] Цикл завершен: {scan_state['total_scanned']} товаров, {scan_state['anomalies_found']} новых аномалий")
    except Exception as e:
        scan_state["error"] = f"Ошибка сканирования ({type(e).__name__})"

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

    spawn_scan(shops, scan_type="manual")
    return web.json_response({"status": "started"})

@routes.get("/api/admin/categories")
@require_admin
async def admin_categories_handler(request):
    """Справочник мастер-категорий со статистикой по базе, активным волнам и магазинам."""
    overview = await asyncio.to_thread(get_categories_overview)
    return web.json_response(overview)

@routes.post("/api/admin/categories")
@require_admin
async def admin_save_categories_handler(request):
    """Сохранение включенных категорий, hot-категорий и параметров волн."""
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("Ожидается JSON объект")
        current = load_settings()
        updates = {}
        if "enabled_categories" in payload:
            if not isinstance(payload["enabled_categories"], dict):
                raise ValueError("enabled_categories должен быть объектом {category_id: bool}")
            updates["enabled_categories"] = {
                k: bool(v) for k, v in payload["enabled_categories"].items() if k in MASTER_CATEGORIES
            }
        if "hot_categories" in payload:
            if not isinstance(payload["hot_categories"], list):
                raise ValueError("hot_categories должен быть массивом")
            updates["hot_categories"] = [c for c in payload["hot_categories"] if c in MASTER_CATEGORIES]
        if "wave_mode" in payload:
            if payload["wave_mode"] not in ("rolling", "all"):
                raise ValueError("Некорректный wave_mode (допустимо: rolling, all)")
            updates["wave_mode"] = payload["wave_mode"]
        if "wave_size" in payload:
            updates["wave_size"] = max(1, min(int(payload["wave_size"]), len(MASTER_CATEGORIES)))

        saved = save_settings({**current, **updates})
        overview = await asyncio.to_thread(get_categories_overview)
        return web.json_response({"status": "ok", "settings": saved, "overview": overview})
    except ValueError as e:
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": "Не удалось сохранить настройки"}, status=400)

@routes.post("/api/scan/category")
@require_admin
async def start_category_scan_handler(request):
    """On-Demand точечный запуск сканирования мастер-категории или отслеживаемой категории."""
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running", "message": "Сканирование уже выполняется"}, status=409)

    category = None
    shops = None
    query_str = None
    cat_name = None
    if request.can_read_body:
        try:
            payload = await request.json()
            category = str(payload.get("category") or "").strip()
            shops = payload.get("shops")
            query_str = payload.get("query")
            cat_name = payload.get("name")
        except Exception:
            pass

    if not category:
        return web.json_response({"status": "error", "message": "Параметр category обязателен"}, status=400)

    # 1. Если это отслеживаемая категория (tracked:ID или tracked_ID)
    if category.startswith("tracked:") or category.startswith("tracked_"):
        raw_id = category.split(":", 1)[-1] if ":" in category else category.split("_", 1)[-1]
        try:
            cid = int(raw_id)
            from database import get_tracked_categories, mark_tracked_category_scanned
            cats = await asyncio.to_thread(get_tracked_categories, False, 200)
            target = next((c for c in cats if c["id"] == cid), None)
            if not target:
                return web.json_response({"status": "error", "message": "Отслеживаемая категория не найдена"}, status=404)

            from search_engine import search_live_stores
            found = await search_live_stores(target["query"], city="Астана")
            await asyncio.to_thread(mark_tracked_category_scanned, cid)
            return web.json_response({
                "status": "completed",
                "category": target["name"],
                "category_name": target["name"],
                "items_found": len(found)
            })
        except Exception as ex:
            return web.json_response({"status": "error", "message": f"Ошибка сбора: {ex}"}, status=400)

    # 2. Если это создание новой категории по запросу (create_query:...)
    if category.startswith("create_query:") or category.startswith("new:"):
        q = query_str or (category.split(":", 1)[1] if ":" in category else "")
        q = q.strip()
        if not q:
            return web.json_response({"status": "error", "message": "Запрос не может быть пустым"}, status=400)
        from database import save_tracked_category, mark_tracked_category_scanned
        from search_engine import determine_category_and_master, search_live_stores
        name = cat_name or q.capitalize()
        det_name, master_id = determine_category_and_master("", q)
        saved = await asyncio.to_thread(save_tracked_category, name, q, master_id)
        found = await search_live_stores(q, city="Астана")
        if saved and saved.get("id"):
            await asyncio.to_thread(mark_tracked_category_scanned, saved["id"])
        return web.json_response({
            "status": "completed",
            "category": name,
            "category_name": name,
            "items_found": len(found)
        })

    # 3. Стандартная мастер-категория (12 групп)
    if category not in MASTER_CATEGORIES:
        return web.json_response({"status": "error", "message": f"Укажите корректный category_id из списка {list(MASTER_CATEGORIES.keys())}"}, status=400)

    if shops is not None and (not isinstance(shops, list) or any(not isinstance(k, str) or k not in SHOP_REGISTRY for k in shops)):
        return web.json_response({"status": "error", "message": "Некорректный список магазинов"}, status=400)

    spawn_scan(shops, target_categories={category}, scan_type="category")
    return web.json_response({
        "status": "started",
        "category": category,
        "category_name": MASTER_CATEGORIES[category]["name"]
    })

@routes.get("/api/categories/tracked")
@require_admin
async def get_tracked_categories_handler(request):
    """Возвращает список отслеживаемых категорий из поисковых запросов."""
    from database import get_tracked_categories
    cats = await asyncio.to_thread(get_tracked_categories, False, 100)
    return web.json_response(cats)

@routes.post("/api/categories/tracked")
@require_admin
async def add_tracked_category_handler(request):
    """Ручное добавление категории/запроса в отслеживаемые волнами."""
    try:
        data = await request.json()
        name = (data.get("name") or "").strip()
        query = (data.get("query") or "").strip()
        master = data.get("master_category") or None
        if not name or not query:
            return web.json_response({"error": "Имя и поисковый запрос обязательны"}, status=400)
        from database import save_tracked_category
        res = await asyncio.to_thread(save_tracked_category, name, query, master)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"error": redact_secrets(str(e))}, status=400)

@routes.post("/api/categories/tracked/{id}/toggle")
@require_admin
async def toggle_tracked_category_handler(request):
    """Включение или выключение отслеживаемой категории из волн."""
    try:
        cid = int(request.match_info["id"])
        data = await request.json()
        is_active = bool(data.get("is_active", True))
        from database import toggle_tracked_category
        await asyncio.to_thread(toggle_tracked_category, cid, is_active)
        return web.json_response({"status": "ok", "id": cid, "is_active": is_active})
    except Exception as e:
        return web.json_response({"error": redact_secrets(str(e))}, status=400)

@routes.post("/api/categories/tracked/{id}/hot")
@require_admin
async def toggle_tracked_category_hot_handler(request):
    """«Горячая» отслеживаемая категория: обновляется в каждой волне (не больше limit-1 мест)."""
    try:
        cid = int(request.match_info["id"])
        is_hot = bool((await request.json()).get("is_hot", True))
    except Exception:
        return web.json_response({"error": "Некорректный запрос"}, status=400)
    from database import toggle_tracked_category_hot
    await asyncio.to_thread(toggle_tracked_category_hot, cid, is_hot)
    return web.json_response({"status": "ok", "id": cid, "is_hot": is_hot})

@routes.delete("/api/categories/tracked/{id}")
@require_admin
async def delete_tracked_category_handler(request):
    """Удаление категории из отслеживаемых."""
    try:
        cid = int(request.match_info["id"])
        from database import delete_tracked_category
        await asyncio.to_thread(delete_tracked_category, cid)
        return web.json_response({"status": "ok", "id": cid})
    except Exception as e:
        return web.json_response({"error": redact_secrets(str(e))}, status=400)

@routes.post("/api/categories/tracked/{id}/scan")
@require_admin
async def scan_single_tracked_category_handler(request):
    """Мгновенное обновление товаров конкретной отслеживаемой категории."""
    try:
        cid = int(request.match_info["id"])
        from database import get_tracked_categories, mark_tracked_category_scanned
        cats = await asyncio.to_thread(get_tracked_categories, False, 200)
        target = next((c for c in cats if c["id"] == cid), None)
        if not target:
            return web.json_response({"error": "Категория не найдена"}, status=404)
        from search_engine import search_live_stores
        found = await search_live_stores(target["query"], city="Астана")
        await asyncio.to_thread(mark_tracked_category_scanned, cid)
        return web.json_response({"status": "ok", "id": cid, "name": target["name"], "items_found": len(found)})
    except Exception as e:
        return web.json_response({"error": redact_secrets(str(e))}, status=400)

@routes.get("/api/admin/categories/tree")
@require_admin
async def admin_categories_tree_handler(request):
    """Иерархическое дерево родительских групп и дочерних категорий из поиска."""
    from database import get_hierarchical_categories
    tree = await asyncio.to_thread(get_hierarchical_categories)
    return web.json_response(tree)

@routes.post("/api/categories/tracked/{id}/parent")
@require_admin
async def set_category_parent_handler(request):
    """Перемещение категории в родительскую мастер-группу."""
    try:
        cid = int(request.match_info["id"])
        data = await request.json()
        master = data.get("master_category")
        from database import set_tracked_category_parent
        await asyncio.to_thread(set_tracked_category_parent, cid, master)
        return web.json_response({"status": "ok", "id": cid, "master_category": master})
    except Exception as e:
        return web.json_response({"error": redact_secrets(str(e))}, status=400)

@routes.post("/api/admin/categories/ai-classify")
@require_admin
async def admin_categories_ai_classify_handler(request):
    """Интеллектуальная AI-классификация неразобранных категорий каталога."""
    try:
        from database import get_hierarchical_categories, set_tracked_category_parent
        from ai_service import classify_categories_batch_ai

        tree = await asyncio.to_thread(get_hierarchical_categories)
        unassigned = tree.get("unassigned", [])
        if not unassigned:
            return web.json_response({"status": "ok", "message": "Нет неразобранных категорий", "count": 0, "assigned": []})

        assigned_map = await classify_categories_batch_ai(unassigned)
        updated = []
        for cid, master_id in assigned_map.items():
            if master_id:
                await asyncio.to_thread(set_tracked_category_parent, cid, master_id)
                updated.append({"id": cid, "master_category": master_id})

        return web.json_response({
            "status": "ok",
            "count": len(updated),
            "assigned": updated
        })
    except Exception as e:
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=500)

@routes.post("/api/admin/catalog/reset")
@require_admin
async def admin_catalog_reset_handler(request):
    """Безопасный сброс каталога с созданием резервной копии."""
    if scan_state["is_running"]:
        return web.json_response({"status": "error", "message": "Нельзя сбросить каталог во время активного сканирования"}, status=409)
    try:
        from scripts.reset_catalog import reset_catalog
        res = await asyncio.to_thread(reset_catalog, True)
        return web.json_response(res)
    except Exception as e:
        return web.json_response({"status": "error", "message": redact_secrets(str(e))}, status=500)

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

# ===== Monitoring Center V1 (P03): только чтение, только администратор =====

def _monitoring_registry():
    return {key: (SHOP_REGISTRY[key][2], len(SHOP_REGISTRY[key][1])) for key in SHOP_REGISTRY}


@routes.get("/monitoring")
async def monitoring_page_handler(request):
    # Сама страница не содержит данных; данные отдаёт /api/admin/monitoring только администратору
    return web.FileResponse(TEMPLATES_DIR / "monitoring.html")


@routes.get("/api/admin/monitoring")
@require_admin
async def monitoring_overview_handler(request):
    import monitoring
    settings = load_settings()
    data = await asyncio.to_thread(monitoring.overview, _monitoring_registry(), enabled_shop_keys(settings),
                                   dict(scan_state), get_wave_interval_seconds(settings))
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/shop/{key}")
@require_admin
async def monitoring_shop_handler(request):
    import monitoring
    key = request.match_info["key"]
    if key not in SHOP_REGISTRY:
        return web.json_response({"status": "error", "message": "Неизвестный магазин"}, status=404)
    _cls, categories, name = SHOP_REGISTRY[key]
    data = await asyncio.to_thread(monitoring.shop_detail, key, name, categories, key in enabled_shop_keys())
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/events")
@require_admin
async def monitoring_events_handler(request):
    import monitoring
    q = request.query
    try:
        limit = int(q.get("limit", 200))
    except ValueError:
        limit = 200
    rows = await asyncio.to_thread(monitoring.events, q.get("component") or None, q.get("severity") or None,
                                   q.get("type") or None, q.get("shop") or None, q.get("scan_id") or None,
                                   limit, q.get("before") or None)
    return web.json_response({"events": rows}, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/incidents")
@require_admin
async def monitoring_incidents_handler(request):
    import monitoring
    names = {name: key for key, (name, _n) in _monitoring_registry().items()}
    rows = await asyncio.to_thread(monitoring.incidents, monitoring.INCIDENT_WINDOW_DAYS, None, names)
    # Отдаётся ограниченное число (открытые первыми): страница не рендерит тысячи карточек
    return web.json_response({"incidents": rows[:monitoring.INCIDENTS_LIMIT], "total": len(rows),
                              "open_total": sum(1 for r in rows if r["open"])},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/backups")
@require_admin
async def monitoring_backups_handler(request):
    """Резервные копии, результат их настоящей проверки и сроки хранения (P15). Только чтение."""
    import monitoring
    data = await asyncio.to_thread(monitoring.backup_section)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/environment")
@require_admin
async def monitoring_environment_handler(request):
    """Фактическое окружение и пульс фоновых работников (P14). Только чтение."""
    import monitoring
    data = await asyncio.to_thread(monitoring.environment_section)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


def check_adaptive_rollback():
    """Проверяет метрики пробных магазинов и возвращает прежний порядок при ухудшении (P11)."""
    try:
        import scheduler_rollout as rollout
        if rollout.stage_of() == rollout.OFF:
            return None
        from database import scheduler_candidates
        record = rollout.check_and_rollback(scheduler_candidates(days=7))
        if record:
            print(f"[AutoScan] ⏮ Адаптивный порядок выключен: {record['reason']}")
        return record
    except Exception as e:
        print(f"[AutoScan] Проверка отката не выполнена: {type(e).__name__}")
        return None


@routes.post("/api/admin/assistant")
@require_admin
async def admin_assistant_handler(request):
    """AI-помощник администратора (P12): только чтение, цифры из отчётов мониторинга."""
    import admin_assistant
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)
    question = str(data.get("question") or "").strip()
    if not question:
        return web.json_response({"status": "error", "message": "Задайте вопрос"}, status=400)
    try:
        days = max(1, min(90, int(data.get("days") or admin_assistant.DEFAULT_DAYS)))
    except (TypeError, ValueError):
        days = admin_assistant.DEFAULT_DAYS
    result = await admin_assistant.answer(question, days=days)
    return web.json_response({"status": "ok", **result},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/daily")
@require_admin
async def admin_daily_digest_handler(request):
    """Суточная сводка (P13): цифры из собственных данных, пересказ — отдельным запросом."""
    import daily_digest
    import database as db
    day = (request.query.get("day") or "").strip() or None
    tz = (request.query.get("tz") or daily_digest.DEFAULT_TZ).strip()
    refresh = request.query.get("refresh") == "1"
    if day:
        try:
            datetime.date.fromisoformat(day)
        except ValueError:
            return web.json_response({"status": "error", "message": "Дата в формате ГГГГ-ММ-ДД"}, status=400)
    try:
        report = await asyncio.to_thread(daily_digest.report, day, tz, refresh)
    except Exception as e:
        print(f"[Daily] Отчёт не собран: {type(e).__name__}")
        return web.json_response({"status": "error", "message": "Не удалось собрать сводку"}, status=500)
    days = await asyncio.to_thread(db.daily_reports, 14)
    view = await asyncio.to_thread(daily_digest.settings_view)
    people = await asyncio.to_thread(daily_digest.recipients)
    telegram = {**view, "recipients": len(people),
                "note": "Сводка уходит администраторам после указанного часа, один раз за сутки"
                        if view["enabled"] else "Отправка в Telegram выключена"}
    return web.json_response({"status": "ok", "report": report, "days": days, "telegram": telegram},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/admin/monitoring/daily/summary")
@require_admin
async def admin_daily_summary_handler(request):
    """Пересказ сводки словами. Число, которого нет в отчёте, отменяет пересказ целиком."""
    import daily_digest
    try:
        data = await request.json()
    except Exception:
        data = {}
    day = str(data.get("day") or "").strip() or None
    tz = str(data.get("tz") or daily_digest.DEFAULT_TZ).strip()
    report = await asyncio.to_thread(daily_digest.report, day, tz, False)
    result = await daily_digest.summarize(report)
    return web.json_response({"status": "ok", **result},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/admin/monitoring/daily/telegram")
@require_admin
async def admin_daily_telegram_handler(request):
    """Включение суточной сводки в Telegram (P13): отдельный выключатель, как и просил план."""
    import config
    import daily_digest
    try:
        data = await request.json()
    except Exception:
        data = {}
    payload = {}
    if "enabled" in data:
        payload["daily_digest_telegram_enabled"] = bool(data.get("enabled"))
    if "hour" in data:
        try:
            payload["daily_digest_hour"] = int(data.get("hour"))
        except (TypeError, ValueError):
            return web.json_response({"status": "error", "message": "Час — целое число от 0 до 23"},
                                     status=400)
    if not payload:
        return web.json_response({"status": "error", "message": "Нечего менять"}, status=400)
    try:
        await asyncio.to_thread(config.save_settings, payload)
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    view = await asyncio.to_thread(daily_digest.settings_view)
    people = await asyncio.to_thread(daily_digest.recipients)
    return web.json_response({"status": "ok", "telegram": {**view, "recipients": len(people)}})


@routes.get("/api/admin/scheduler/rollout")
@require_admin
async def scheduler_rollout_status_handler(request):
    """Состояние контролируемого включения планировщика (P11)."""
    import scheduler_rollout as rollout
    data = await asyncio.to_thread(rollout.status)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/admin/scheduler/rollout")
@require_admin
async def scheduler_rollout_switch_handler(request):
    """Переключение шага включения. Вперёд — по одному шагу, назад и в «выключено» — всегда."""
    import scheduler_rollout as rollout
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)

    target = str(data.get("stage") or "").strip()
    if target not in rollout.STAGES:
        return web.json_response({"status": "error", "message": f"неизвестный шаг: {target}"}, status=400)

    def switch():
        from database import scheduler_candidates
        candidates = scheduler_candidates(days=7) if target != rollout.OFF else []
        return rollout.switch_stage(target, candidates)

    try:
        # Подготовка и активация — один путь: при отказе шаг не включается (M03)
        started = await asyncio.to_thread(switch)
    except rollout.StageRefused as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    except Exception as e:
        # Тип ошибки видно сразу в интерфейсе: «не удалось подготовить состояние» не говорит ничего
        # ни владельцу, ни тому, кто будет разбираться. Текст ошибки чистится от возможных секретов.
        print(f"[Rollout] Переход не выполнен: {type(e).__name__}: {e}")
        try:
            from telemetry import telemetry, COMPONENT_SCHEDULER
            telemetry.record_system_error(COMPONENT_SCHEDULER, "scheduler_rollout_switch", e)
        except Exception:
            pass
        detail = redact_secrets(f"{type(e).__name__}: {e}")[:200]
        return web.json_response({"status": "error",
                                  "message": f"Шаг не включён: {detail}"},
                                 status=500)
    status = await asyncio.to_thread(rollout.status)
    return web.json_response({"status": "ok", "switched": started, "state": status},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/scheduler")
@require_admin
async def monitoring_scheduler_handler(request):
    """Предложения теневого планировщика (P10). Только чтение: обходы отсюда не запускаются."""
    import monitoring
    try:
        days = max(1, min(90, int(request.query.get("days") or monitoring.SCHEDULER_SHADOW_DAYS)))
    except ValueError:
        days = monitoring.SCHEDULER_SHADOW_DAYS
    data = await asyncio.to_thread(monitoring.scheduler_suggestions, days)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/matching")
@require_admin
async def monitoring_matching_handler(request):
    """Теневой отчёт сопоставления товаров (P09): фасовка, спорные случаи и примеры."""
    import monitoring
    try:
        days = max(1, min(90, int(request.query.get("days") or monitoring.MATCHING_SHADOW_DAYS)))
    except ValueError:
        days = monitoring.MATCHING_SHADOW_DAYS
    data = await asyncio.to_thread(monitoring.matching_quality, days)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


_AI_MODELS_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
AI_MODELS_CACHE_SECONDS = 300


@routes.get("/api/admin/ai/models")
@require_admin
async def admin_ai_models_handler(request):
    """Список моделей от самих провайдеров: имена меняются, зашивать их в код нельзя.

    Наружу уходят только имена моделей — ключи остаются на сервере. Ответ ненадолго кэшируется,
    чтобы открытие настроек не дёргало провайдеров на каждый клик.
    """
    import ai_service
    import time as _time
    fresh = request.query.get("refresh") == "1"
    if not fresh and _AI_MODELS_CACHE["data"] and \
            _time.time() - _AI_MODELS_CACHE["at"] < AI_MODELS_CACHE_SECONDS:
        return web.json_response({"status": "ok", "cached": True, **_AI_MODELS_CACHE["data"]})

    cfg = get_ai_config()
    gemini, openai = await asyncio.gather(
        ai_service.list_gemini_models(cfg.get("gemini_api_key") or ""),
        ai_service.list_openai_models(cfg.get("openai_api_key") or "", cfg.get("openai_api_base") or ""),
    )
    data = {"gemini": gemini, "openai": openai,
            "current": {"gemini": cfg.get("gemini_model"), "openai": cfg.get("openai_model")},
            "prices": load_settings().get("ai_model_prices") or {},
            "note": "Список приходит от провайдера по вашему ключу. Цены провайдеры не отдают — "
                    "их вы задаёте сами, в долларах за миллион токенов."}
    _AI_MODELS_CACHE.update({"at": _time.time(), "data": data})
    return web.json_response({"status": "ok", "cached": False, **data},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/ai/prices")
@require_admin
async def admin_ai_prices_handler(request):
    """Подсказка цен с публичного прайса провайдера. Ничего не сохраняет — решает владелец."""
    import ai_prices
    models = [m.strip() for m in (request.query.get("models") or "").split(",") if m.strip()][:10]
    if not models:
        return web.json_response({"status": "error", "message": "Не указано, для каких моделей"},
                                 status=400)
    data = await ai_prices.suggest(models)
    return web.json_response({"status": "ok", **data},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/admin/monitoring/matching/ai")
@require_admin
async def monitoring_matching_ai_handler(request):
    """Режим AI-части сопоставления (P09): off → shadow → on. Включение — решение владельца."""
    import catalog_ai
    import config
    import monitoring
    try:
        data = await request.json()
    except Exception:
        data = {}
    mode = str(data.get("mode") or "").strip()
    if mode not in catalog_ai.MODES:
        return web.json_response({"status": "error",
                                  "message": f"Режим: {', '.join(catalog_ai.MODES)}"}, status=400)
    try:
        await asyncio.to_thread(config.save_settings, {"ai_matching_mode": mode})
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    view = await asyncio.to_thread(monitoring._matching_ai_view)
    return web.json_response({"status": "ok", "ai": view},
                             dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/ai-usage")
@require_admin
async def monitoring_ai_usage_handler(request):
    """Расходы AI по задачам и моделям (P08): вызовы, токены, деньги, кэш и запасные провайдеры."""
    import monitoring
    try:
        days = max(1, min(90, int(request.query.get("days") or monitoring.AI_USAGE_DAYS)))
    except ValueError:
        days = monitoring.AI_USAGE_DAYS
    data = await asyncio.to_thread(monitoring.ai_spending, days)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/api/admin/monitoring/search")
@require_admin
async def monitoring_search_handler(request):
    """Отчёт по поиску (P06): точные дневные агрегаты, доля успеха, частые и проблемные запросы."""
    import monitoring
    q = request.query
    try:
        days = max(1, min(90, int(q.get("days") or monitoring.SEARCH_ANALYTICS_DAYS)))
    except ValueError:
        days = monitoring.SEARCH_ANALYTICS_DAYS
    data = await asyncio.to_thread(monitoring.search_analytics, days, q.get("city") or None)
    return web.json_response(data, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.post("/api/scan/shopkz-yml")
@require_admin
async def sync_shopkz_yml_handler(request):
    """Сверхбыстрая выгрузка всего каталога shop.kz через официальный YML фид."""
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running", "message": "Сканирование уже выполняется"})
    spawn_scan(["shopkz"])
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

def apply_adaptive_order(target_shops):
    """Порядок обхода волны с учётом шага включения адаптивного планировщика (P11).

    Возвращает (список магазинов, пояснение). При выключенном шаге и при любой ошибке возвращается
    исходный список: новый порядок — необязательная надстройка, он не имеет права ломать обходы.
    """
    try:
        import scheduler_rollout as rollout
        stage = rollout.stage_of()
        if stage == rollout.OFF:
            return list(target_shops), ""
        from database import scheduler_candidates
        candidates = scheduler_candidates(days=7)
        result = rollout.select_targets(stage, list(target_shops), candidates)
        return result["targets"], result["explanation"] if result["changed"] else ""
    except Exception as e:
        print(f"[AutoScan] Адаптивный порядок не применён: {type(e).__name__}")
        return list(target_shops), ""


async def auto_scan_background_worker(app):
    """Фоновый монитор: строгое поочередное волновое сканирование за 24 часа + контроль свежести магазинов."""
    settings = load_settings()
    init_plan = get_wave_plan(
        enabled_categories=settings.get("enabled_categories"),
        hot_categories=settings.get("hot_categories", DEFAULT_HOT_CATEGORIES),
        wave_size=settings.get("wave_size", 2),
        wave_mode="rolling"
    )
    init_wave_interval = get_wave_interval_seconds(settings, init_plan["total_waves"])
    print(f"[AutoScan] 🤖 Автономный фоновый монитор запущен (интервал волны: {init_wave_interval // 60} мин, лимит полного круга: 24ч)...")
    import environment
    while True:
        try:
            # Пульс работника (P14): «сайт отвечает» ещё не значит, что обходы идут
            await asyncio.to_thread(environment.heartbeat, environment.SCHEDULER, "цикл планировщика")
            await asyncio.sleep(20)
            if scan_state.get("is_running"):
                continue

            settings = load_settings()
            wave_mode = settings.get("wave_mode", "rolling")
            enabled_keys = enabled_shop_keys(settings)
            if not enabled_keys:
                continue

            if wave_mode == "rolling":
                plan = get_wave_plan(
                    enabled_categories=settings.get("enabled_categories"),
                    hot_categories=settings.get("hot_categories", DEFAULT_HOT_CATEGORIES),
                    wave_size=settings.get("wave_size", 2),
                    wave_mode="rolling"
                )
                total_waves = max(1, plan.get("total_waves", 1))
                wave_interval_sec = get_wave_interval_seconds(settings, total_waves)

                last_run_raw = get_metadata("wave_last_run_at")
                now = time.time()
                try:
                    last_run_time = float(last_run_raw) if last_run_raw else None
                except (ValueError, TypeError):
                    last_run_time = None

                is_wave_due = (last_run_time is None) or ((now - last_run_time) >= wave_interval_sec)
                stale = get_stale_shops(enabled_keys, wave_interval_sec)

                if is_wave_due or stale:
                    target_shops = enabled_keys if is_wave_due else stale
                    reason = f"время очередной волны (шаг: {wave_interval_sec // 60}м, круговой лимит: 24ч)" if is_wave_due else f"устарели {len(stale)} магазинов"
                    # Контролируемое включение адаптивного порядка (P11): по умолчанию выключено, а
                    # включённый порядок может только переставить и сократить эту же волну
                    target_shops, adaptive_note = apply_adaptive_order(target_shops)
                    if adaptive_note:
                        reason += f"; {adaptive_note}"
                    print(f"[AutoScan] 🌊 Запуск волны: {reason}")
                    spawn_scan(target_shops, scan_type="auto")
            else:
                max_age_seconds = get_scan_interval_seconds(settings)
                stale = get_stale_shops(enabled_keys, max_age_seconds)
                if stale:
                    names = ", ".join(SHOP_REGISTRY[k][2] for k in stale[:4]) + ("..." if len(stale) > 4 else "")
                    print(f"[AutoScan] ⏰ Требуют обновления {len(stale)} магазинов (порог {max_age_seconds // 60} мин): {names}")
                    spawn_scan(stale, scan_type="auto")
        except asyncio.CancelledError:
            print("[AutoScan] Фоновый монитор остановлен.")
            break
        except Exception as e:
            print(f"[AutoScan] Ошибка в фоновом мониторе: {e}")
            from telemetry import telemetry, COMPONENT_SCHEDULER
            telemetry.record_system_error(COMPONENT_SCHEDULER, "auto_scan_worker", e)
            await asyncio.sleep(15)

async def background_tasks(app):
    from telegram_bot import run_telegram_bot_task
    tasks = [asyncio.create_task(auto_scan_background_worker(app)),
             asyncio.create_task(ai_normalize_background_worker(app)),
             asyncio.create_task(notification_worker()),
             asyncio.create_task(run_telegram_bot_task())]
    from telemetry import telemetry
    telemetry.start()
    yield
    for task in tasks + list(_scan_tasks):
        task.cancel()
    await asyncio.gather(*tasks, *list(_scan_tasks), return_exceptions=True)
    await asyncio.to_thread(telemetry.stop)

@web.middleware
async def telemetry_middleware(request, handler):
    """Необработанные исключения обработчиков → system_error (маршрут-шаблон, без параметров и тела; P01)."""
    try:
        return await handler(request)
    except (web.HTTPException, asyncio.CancelledError):
        raise
    except Exception as e:
        from telemetry import telemetry, COMPONENT_SYSTEM
        route = getattr(getattr(request.match_info, "route", None), "resource", None)
        telemetry.record_system_error(COMPONENT_SYSTEM, "http_handler", e,
                                      data={"method": request.method,
                                            "route": getattr(route, "canonical", None) or "unmatched"})
        raise


def create_app():
    init_db()
    app = web.Application(middlewares=[telemetry_middleware, auth_middleware])
    if ALLOW_DEV_LOGIN:
        print("[Auth] 🧑‍💻 Вход разработчика включен (локальный запуск): кнопка «Вход разработчика» в шапке, права администратора")
    elif not ADMIN_TELEGRAM_IDS:
        print("[Auth] ⚠️ ADMIN_TELEGRAM_IDS не задан — администраторов нет, общие настройки и логи недоступны")
    if not get_bot_token():
        print("[Auth] ⚠️ TELEGRAM_BOT_TOKEN не задан — вход через Telegram и уведомления отключены")
    app.cleanup_ctx.append(background_tasks)
    app.add_routes(routes)
    # Статические модули витрины (P04 U08): без листинга каталогов
    app.router.add_static("/static/", BASE_DIR / "web" / "static", show_index=False)
    return app
