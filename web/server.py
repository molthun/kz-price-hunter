import asyncio
import datetime
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
    FOURMOBILE_CATEGORIES
)
from version import get_version_info
from database import (
    init_db,
    get_stats,
    get_alerts,
    get_products_list,
    get_products_count,
    save_or_update_product,
    was_alert_sent_recently,
    record_alert,
    get_db_freshness,
    upsert_telegram_user,
    save_user_settings,
    list_users,
    get_user,
    set_user_blocked,
    create_session,
    delete_session
)
from detector import check_anomaly, check_market_arbitrage
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
from notifier import dispatch_alert, telegram_api
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

@routes.get("/api/best-price")
async def best_price_handler(request):
    query = request.query.get("q", "").strip()
    live = request.query.get("live", "false").lower() in ("true", "1", "yes")
    shop = request.query.get("shop", None)
    city = request.query.get("city", None)
    user = request.get("user")

    # Расширенные гибкие фильтры
    min_price_param = request.query.get("min_price", None)
    min_price = int(min_price_param) if min_price_param and min_price_param.isdigit() else None

    max_price_param = request.query.get("max_price", None)
    max_price = int(max_price_param) if max_price_param and max_price_param.isdigit() else None

    exclude_acc_param = request.query.get("exclude_acc", "1")
    exclude_accessories = exclude_acc_param in ("1", "true", "yes")

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

    try:
        data = await get_best_price_summary(
            query=query,
            live=live,
            shop=shop,
            city=city,
            min_price=min_price,
            max_price=max_price,
            exclude_accessories=exclude_accessories,
            match_mode=match_mode,
            sort_by=sort_by,
            negative_keywords=negative_keywords,
            junk_keywords=user_settings_for(request).get("junk_keywords", [])
        )
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
    return legacy_user_settings() if int(user_id) in ADMIN_TELEGRAM_IDS else {}

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
    uid = min(ADMIN_TELEGRAM_IDS) if ADMIN_TELEGRAM_IDS else 1
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
    return web.json_response({
        "settings": load_settings(),
        "bot": {"configured": bool(get_bot_token()), "username": bot_username},
    })

@routes.post("/api/admin/config")
@require_admin
async def post_admin_config_handler(request):
    try:
        data = await request.json()
        saved = save_settings(data)
        return web.json_response({"status": "ok", "settings": saved})
    except ValueError as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)
    except Exception:
        return web.json_response({"status": "error", "message": "Некорректный запрос"}, status=400)

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
        competitor_shop=anomaly.get("competitor_shop")
    )
    await asyncio.to_thread(dispatch_alert, p, anomaly)
    return True

async def _do_scan_task():
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
    enabled = settings.get("enabled_shops", {})
    candidate_settings = get_candidate_settings(settings)

    scrapers_map = []
    if enabled.get("shopkz", True):
        scrapers_map.append((ShopKzScraper(), SHOPKZ_CATEGORIES, "Белый Ветер"))
    if enabled.get("forcecom", True):
        scrapers_map.append((ForcecomScraper(), FORCECOM_CATEGORIES, "Forcecom"))
    if enabled.get("mechta", True):
        scrapers_map.append((MechtaScraper(), MECHTA_CATEGORIES, "Мечта"))
    if enabled.get("sulpak", True):
        scrapers_map.append((SulpakScraper(), SULPAK_CATEGORIES, "Sulpak"))
    if enabled.get("alser", True):
        scrapers_map.append((AlserScraper(), ALSER_CATEGORIES, "Alser"))
    if enabled.get("evrika", True):
        scrapers_map.append((EvrikaScraper(), EVRIKA_CATEGORIES, "Эврика"))
    if enabled.get("moon", True):
        scrapers_map.append((MoonScraper(), MOON_CATEGORIES, "Moon.kz"))
    if enabled.get("kaspi", True):
        scrapers_map.append((KaspiScraper(), KASPI_CATEGORIES, "Kaspi Магазин"))
    if enabled.get("fourmobile", True):
        scrapers_map.append((FourMobileScraper(), FOURMOBILE_CATEGORIES, "4mobile"))
    if enabled.get("dns", True):
        scrapers_map.append((DNSScraper(), DNS_CATEGORIES, "DNS"))
    if enabled.get("technodom", True):
        scrapers_map.append((TechnodomScraper(), TECHNODOM_CATEGORIES, "Технодом"))

    total_categories_count = sum(len(cats) for _, cats, _ in scrapers_map)
    scan_state["total_steps"] = max(1, total_categories_count)
    completed_steps = 0

    try:
        for scraper, cats, shop_name in scrapers_map:
            scan_state["current_shop"] = shop_name
            for cat in cats:
                scan_state["current_category"] = cat["name"]
                # Обновляем процент прогресса перед началом категории
                scan_state["progress_pct"] = int((completed_steps / scan_state["total_steps"]) * 100)

                try:
                    prods = await scraper.scrape(cat["name"], cat["url"], max_pages=cat.get("max_pages", 1))
                    scan_state["total_scanned"] += len(prods)

                    if len(prods) > 200:
                        from database import save_or_update_products_batch, get_price_history_batch
                        # История цен читается ДО пакетной перезаписи, иначе прежняя цена будет потеряна
                        history_map = await asyncio.to_thread(get_price_history_batch, [p["id"] for p in prods])
                        await asyncio.to_thread(save_or_update_products_batch, prods)

                        for p in prods:
                            history = history_map.get(str(p["id"]), {"old_price": p["price"], "first_seen_price": p["price"]})
                            if history["old_price"] > p["price"] or history["first_seen_price"] > p["price"] or p.get("old_price_on_site", 0) > p["price"]:
                                await _process_anomaly(p, check_anomaly(p, history, custom_settings=candidate_settings), shop_name)

                        # Межмагазинный арбитраж (FTS-запрос на товар) — только для ограниченного набора кандидатов
                        arbitrage_candidates = [p for p in prods if p.get("old_price_on_site", 0) > p.get("price", 0) or p.get("price", 0) >= 100000][:100]
                        for p in arbitrage_candidates:
                            await _process_anomaly(p, check_market_arbitrage(p, custom_settings=candidate_settings), shop_name)
                    else:
                        for p in prods:
                            history = save_or_update_product(p)
                            anomaly = check_anomaly(p, history, custom_settings=candidate_settings)
                            if not anomaly:
                                anomaly = check_market_arbitrage(p, custom_settings=candidate_settings)
                            await _process_anomaly(p, anomaly, shop_name)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Ошибка категории {cat['name']}: {e}")

                completed_steps += 1
                scan_state["current_step"] = completed_steps
                scan_state["progress_pct"] = int((completed_steps / scan_state["total_steps"]) * 100)

        scan_state["progress_pct"] = 100
        scan_state["last_completed"] = datetime.datetime.now().strftime("%H:%M:%S")
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
    global scan_state
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running"})

    asyncio.create_task(_do_scan_task())
    return web.json_response({"status": "started"})

@routes.post("/api/scan/shopkz-yml")
@require_admin
async def sync_shopkz_yml_handler(request):
    """Сверхбыстрая выгрузка всего каталога shop.kz через официальный YML фид."""
    async def _do_yml_sync():
        try:
            scraper = ShopKzScraper()
            items = await asyncio.to_thread(scraper.scrape_yml)
            from database import save_or_update_products_batch
            saved_count = await asyncio.to_thread(save_or_update_products_batch, items)
            print(f"[ShopKZ-YML] ✅ Загружено и сохранено {saved_count} товаров из официального YML!")
        except Exception as e:
            print(f"[ShopKZ-YML] Ошибка загрузки YML: {e}")

    asyncio.create_task(_do_yml_sync())
    return web.json_response({
        "status": "started",
        "message": "Синхронизация официального YML каталога shop.kz (16k+ товаров) запущена в фоне"
    })

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
    """Фоновый воркер: непрерывно следит за возрастом базы данных.
    Если база старше порога из настроек (scan_interval_minutes), автономно запускает фоновое обновление.
    """
    print(f"[AutoScan] 🤖 Автономный фоновый монитор запущен (порог устаревания базы: {get_scan_interval_seconds() // 60} мин)...")
    while True:
        try:
            await asyncio.sleep(20)
            if scan_state.get("is_running"):
                continue

            settings = load_settings()
            max_age_seconds = get_scan_interval_seconds(settings)
            max_age_minutes = max_age_seconds // 60

            freshness = get_db_freshness(threshold_seconds=max_age_seconds)
            age = freshness.get("age_seconds")

            # Если товаров нет вообще или данные старше порога из настроек
            if age is None or age >= max_age_seconds:
                age_desc = f"{age // 60} мин" if age is not None else "база пуста"
                print(f"[AutoScan] ⏰ База требует обновления (возраст: {age_desc} >= {max_age_minutes} мин). Запуск автономного сканирования...")
                asyncio.create_task(_do_scan_task())
        except asyncio.CancelledError:
            print("[AutoScan] Фоновый монитор остановлен.")
            break
        except Exception as e:
            print(f"[AutoScan] Ошибка в фоновом мониторе: {e}")
            await asyncio.sleep(15)

async def background_tasks(app):
    task = asyncio.create_task(auto_scan_background_worker(app))
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

def create_app():
    init_db()
    app = web.Application(middlewares=[auth_middleware])
    if not ADMIN_TELEGRAM_IDS:
        print("[Auth] ⚠️ ADMIN_TELEGRAM_IDS не задан — администраторов нет, общие настройки и логи недоступны")
    if not get_bot_token():
        print("[Auth] ⚠️ TELEGRAM_BOT_TOKEN не задан — вход через Telegram и уведомления отключены")
    app.cleanup_ctx.append(background_tasks)
    app.add_routes(routes)
    return app
