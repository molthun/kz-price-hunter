import asyncio
import json
import os
from pathlib import Path
from aiohttp import web

from config import (
    load_settings,
    save_settings,
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
    get_db_freshness
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
from notifier import send_alert
from log_manager import install_log_interceptor, log_buffer
import requests

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

routes = web.RouteTableDef()

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
    stats = get_stats()
    stats["scan_state"] = scan_state
    return web.json_response(stats)

@routes.get("/api/alerts")
async def alerts_handler(request):
    city = request.query.get("city", None)
    alerts = get_alerts(limit=50, city=city)
    return web.json_response(alerts)

@routes.get("/api/products")
async def products_handler(request):
    shop = request.query.get("shop", None)
    city = request.query.get("city", None)
    search = request.query.get("search", None)
    limit = int(request.query.get("limit", 50))
    offset = int(request.query.get("offset", 0))

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
            negative_keywords=negative_keywords
        )
        return web.json_response(data)
    except Exception as e:
        print(f"[API best-price] Ошибка: {e}")
        return web.json_response({"error": str(e)}, status=500)

@routes.get("/api/config")
async def get_config_handler(request):
    settings = load_settings()
    return web.json_response(settings)

@routes.post("/api/config")
async def post_config_handler(request):
    try:
        data = await request.json()
        saved = save_settings(data)
        return web.json_response({"status": "ok", "settings": saved})
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=400)

@routes.post("/api/test-telegram")
async def test_telegram_handler(request):
    settings = load_settings()
    token = settings.get("telegram_bot_token")
    chat_id = settings.get("telegram_chat_id")

    if not token or not chat_id:
        return web.json_response({"status": "error", "message": "Токен или Chat ID не заданы в настройках!"}, status=400)

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "🎯 <b>KZ Price Hunter</b>\n\n✅ Связь с Telegram успешно настроена! Бот готов присылать уведомления о ценовых ошибках и скидках.",
            "parse_mode": "HTML"
        }
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return web.json_response({"status": "ok", "message": "Тестовое сообщение успешно отправлено!"})
        else:
            return web.json_response({"status": "error", "message": f"Ошибка Telegram: {res.text}"}, status=400)
    except Exception as e:
        return web.json_response({"status": "error", "message": str(e)}, status=500)

async def _do_scan_task():
    global scan_state
    scan_state["is_running"] = True
    scan_state["total_scanned"] = 0
    scan_state["anomalies_found"] = 0
    scan_state["current_step"] = 0
    scan_state["progress_pct"] = 0
    scan_state["error"] = None

    settings = load_settings()
    enabled = settings.get("enabled_shops", {})

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
                        from database import save_or_update_products_batch
                        await asyncio.to_thread(save_or_update_products_batch, prods)
                        # Для детекции аномалий проверяем товары со скидками на сайте
                        to_check = [p for p in prods if p.get("old_price_on_site", 0) > p.get("price", 0) or p.get("price", 0) >= 100000][:100]
                        for p in to_check:
                            anomaly = check_anomaly(p, {"old_price": p["price"], "first_seen_price": p["price"]}, custom_settings=settings)
                            if not anomaly:
                                anomaly = check_market_arbitrage(p, custom_settings=settings)
                            if anomaly and not was_alert_sent_recently(p["id"], p["price"]):
                                scan_state["anomalies_found"] += 1
                                record_alert(
                                    product_id=p["id"],
                                    alert_type=anomaly["type"],
                                    old_price=anomaly["old_price"],
                                    new_price=anomaly["new_price"],
                                    discount_pct=anomaly["drop_pct"],
                                    savings_kzt=anomaly["savings"],
                                    shop=p.get("shop", shop_name),
                                    city=p.get("city", "Астана")
                                )
                    else:
                        for p in prods:
                            history = save_or_update_product(p)
                            anomaly = check_anomaly(p, history, custom_settings=settings)
                            if not anomaly:
                                anomaly = check_market_arbitrage(p, custom_settings=settings)

                            if anomaly and not was_alert_sent_recently(p["id"], p["price"]):
                                scan_state["anomalies_found"] += 1
                                
                                # Фильтрация уровня уведомлений в Telegram
                                lvl = settings.get("telegram_notify_level", "ALL")
                                should_send_tg = True
                                if lvl == "CRITICAL_ONLY":
                                    should_send_tg = (anomaly["type"] == "ZERO_GLITCH")
                                elif lvl == "HIGH_SAVINGS":
                                    should_send_tg = (anomaly["type"] == "ZERO_GLITCH" or anomaly.get("drop_pct", 0) >= 75.0 or anomaly.get("savings", 0) >= 100000)

                                if should_send_tg:
                                    send_alert(p, anomaly)

                                record_alert(
                                    product_id=p["id"],
                                    alert_type=anomaly["type"],
                                    old_price=anomaly["old_price"],
                                    new_price=anomaly["new_price"],
                                    discount_pct=anomaly["drop_pct"],
                                    savings_kzt=anomaly["savings"],
                                    shop=p.get("shop", shop_name),
                                    city=p.get("city", "Астана")
                                )
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Ошибка категории {cat['name']}: {e}")

                completed_steps += 1
                scan_state["current_step"] = completed_steps
                scan_state["progress_pct"] = int((completed_steps / scan_state["total_steps"]) * 100)

        scan_state["progress_pct"] = 100
        import datetime
        scan_state["last_completed"] = datetime.datetime.now().strftime("%H:%M:%S")
    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        await asyncio.sleep(1.0)
        scan_state["is_running"] = False
        scan_state["current_shop"] = ""
        scan_state["current_category"] = ""

@routes.post("/api/scan/start")
async def start_scan_handler(request):
    global scan_state
    if scan_state["is_running"]:
        return web.json_response({"status": "already_running"})

    asyncio.create_task(_do_scan_task())
    return web.json_response({"status": "started"})

@routes.post("/api/scan/shopkz-yml")
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
async def logs_clear_handler(request):
    """Очищает кольцевой буфер логов."""
    log_buffer.clear()
    return web.json_response({"status": "ok", "message": "Буфер логов очищен"})

@routes.get("/api/logs/export")
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
    Если база старше 3 часов (или порога в настройках), автономно запускает фоновое обновление.
    """
    print("[AutoScan] 🤖 Автономный фоновый монитор запущен (порог устаревания базы: 3 часа)...")
    while True:
        try:
            await asyncio.sleep(20)
            if scan_state.get("is_running"):
                continue

            settings = load_settings()
            max_age_minutes = int(settings.get("scan_interval_minutes", 180))
            max_age_seconds = max(300, max_age_minutes * 60)

            freshness = get_db_freshness(threshold_seconds=max_age_seconds)
            age = freshness.get("age_seconds")

            # Если товаров нет вообще или данные старше порога (3 часа)
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
    app = web.Application()
    app.cleanup_ctx.append(background_tasks)
    app.add_routes(routes)
    return app
