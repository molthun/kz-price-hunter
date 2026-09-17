import asyncio
import argparse
from datetime import datetime

from config import (
    DNS_CATEGORIES,
    SHOPKZ_CATEGORIES,
    TECHNODOM_CATEGORIES,
    FORCECOM_CATEGORIES,
    SULPAK_CATEGORIES,
    MECHTA_CATEGORIES,
    ALSER_CATEGORIES,
    EVRIKA_CATEGORIES,
    MOON_CATEGORIES,
    FOURMOBILE_CATEGORIES,
    CHECK_INTERVAL_SECONDS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID
)
from database import (
    init_db,
    save_or_update_product,
    was_alert_sent_recently,
    record_alert,
    get_stats
)
from detector import check_anomaly
from scrapers.dns import DNSScraper
from scrapers.shopkz import ShopKzScraper
from scrapers.technodom import TechnodomScraper
from scrapers.forcecom import ForcecomScraper
from scrapers.sulpak import SulpakScraper
from scrapers.mechta import MechtaScraper
from scrapers.alser import AlserScraper
from scrapers.evrika import EvrikaScraper
from scrapers.moon import MoonScraper
from scrapers.fourmobile import FourMobileScraper
from notifier import send_alert

async def scan_category_list(scraper, categories):
    total = 0
    anomalies = 0

    for cat in categories:
        name = cat["name"]
        url = cat["url"]
        max_pages = cat.get("max_pages", 1)

        print(f"\n--- Сканирование: {name} ---")
        try:
            products = await scraper.scrape(name, url, max_pages=max_pages)
            print(f"Собрано {len(products)} товаров")
            total += len(products)

            for p in products:
                history = save_or_update_product(p)
                anomaly = check_anomaly(p, history)

                if anomaly:
                    if not was_alert_sent_recently(p["id"], p["price"]):
                        anomalies += 1
                        send_alert(p, anomaly)
                        record_alert(
                            product_id=p["id"],
                            alert_type=anomaly["type"],
                            old_price=anomaly["old_price"],
                            new_price=anomaly["new_price"],
                            discount_pct=anomaly["drop_pct"],
                            savings_kzt=anomaly["savings"],
                            shop=p.get("shop", "Неизвестно"),
                            competitor_shop=anomaly.get("competitor_shop")
                        )

            await asyncio.sleep(1.0)

        except Exception as e:
            print(f"Ошибка при сканировании {name}: {e}")

    return total, anomalies

async def run_multi_scan_cycle(
    dns_scraper,
    shopkz_scraper,
    technodom_scraper,
    forcecom_scraper,
    sulpak_scraper,
    mechta_scraper,
    alser_scraper,
    evrika_scraper,
    moon_scraper,
    fourmobile_scraper
):
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 Старт мониторинга по 10 магазинам (Астана / Казахстан)...")

    total_scanned = 0
    total_anomalies = 0

    # 1. Белый Ветер (shop.kz)
    print("\n📦 [1/9] Белый Ветер (shop.kz)...")
    scanned_shopkz, anom_shopkz = await scan_category_list(shopkz_scraper, SHOPKZ_CATEGORIES)
    total_scanned += scanned_shopkz
    total_anomalies += anom_shopkz

    # 2. Forcecom (forcecom.kz)
    print("\n📦 [2/9] Forcecom (forcecom.kz)...")
    scanned_fc, anom_fc = await scan_category_list(forcecom_scraper, FORCECOM_CATEGORIES)
    total_scanned += scanned_fc
    total_anomalies += anom_fc

    # 3. Мечта (mechta.kz)
    print("\n📦 [3/9] Мечта (mechta.kz)...")
    scanned_mechta, anom_mechta = await scan_category_list(mechta_scraper, MECHTA_CATEGORIES)
    total_scanned += scanned_mechta
    total_anomalies += anom_mechta

    # 4. Sulpak (sulpak.kz)
    print("\n📦 [4/9] Sulpak (sulpak.kz)...")
    scanned_sulpak, anom_sulpak = await scan_category_list(sulpak_scraper, SULPAK_CATEGORIES)
    total_scanned += scanned_sulpak
    total_anomalies += anom_sulpak

    # 5. Alser (alser.kz)
    print("\n📦 [5/9] Alser (alser.kz)...")
    scanned_alser, anom_alser = await scan_category_list(alser_scraper, ALSER_CATEGORIES)
    total_scanned += scanned_alser
    total_anomalies += anom_alser

    # 6. Эврика (evrika.com)
    print("\n📦 [6/9] Эврика (evrika.com)...")
    scanned_evrika, anom_evrika = await scan_category_list(evrika_scraper, EVRIKA_CATEGORIES)
    total_scanned += scanned_evrika
    total_anomalies += anom_evrika

    # 7. Moon.kz (moon.kz)
    print("\n📦 [7/9] Moon.kz (moon.kz)...")
    scanned_moon, anom_moon = await scan_category_list(moon_scraper, MOON_CATEGORIES)
    total_scanned += scanned_moon
    total_anomalies += anom_moon

    # 8. DNS Казахстан (dns-shop.kz)
    print("\n📦 [8/9] DNS Казахстан (dns-shop.kz)...")
    scanned_dns, anom_dns = await scan_category_list(dns_scraper, DNS_CATEGORIES)
    total_scanned += scanned_dns
    total_anomalies += anom_dns

    # 9. Технодом (technodom.kz)
    print("\n📦 [9/10] Технодом (technodom.kz)...")
    scanned_td, anom_td = await scan_category_list(technodom_scraper, TECHNODOM_CATEGORIES)
    total_scanned += scanned_td
    total_anomalies += anom_td

    # 10. 4mobile (4mobile.pages.dev)
    print("\n📦 [10/10] 4mobile (4mobile.pages.dev)...")
    scanned_4m, anom_4m = await scan_category_list(fourmobile_scraper, FOURMOBILE_CATEGORIES)
    total_scanned += scanned_4m
    total_anomalies += anom_4m

    stats = get_stats()
    print("\n" + "=" * 60)
    print("🏁 Полный цикл мониторинга 10 магазинов завершен!")
    print(f"Товаров обработано за цикл: {total_scanned}")
    print(f"Новых аномалий найдено: {total_anomalies}")
    print(f"Всего товаров в базе: {stats['total_products']}")
    print(f"Товары по магазинам: {stats['shops']}")
    print(f"Всего алертов в истории: {stats['total_alerts']}")
    print("=" * 60 + "\n")

async def main_loop(run_once: bool = False):
    init_db()

    dns_scraper = DNSScraper()
    shopkz_scraper = ShopKzScraper()
    technodom_scraper = TechnodomScraper()
    forcecom_scraper = ForcecomScraper()
    sulpak_scraper = SulpakScraper()
    mechta_scraper = MechtaScraper()
    alser_scraper = AlserScraper()
    evrika_scraper = EvrikaScraper()
    moon_scraper = MoonScraper()
    fourmobile_scraper = FourMobileScraper()

    print("🤖 Multi-Store Price Glitch Monitor запущен!")
    print("Подключенные магазины:")
    print("  🟧 DNS Казахстан (dns-shop.kz)")
    print("  🟦 Белый Ветер (shop.kz)")
    print("  🔴 Технодом (technodom.kz)")
    print("  ⚡️ Forcecom (forcecom.kz)")
    print("  🟢 Sulpak (sulpak.kz)")
    print("  🟣 Мечта (mechta.kz)")
    print("  🟡 Alser (alser.kz)")
    print("  🔷 Эврика (evrika.com)")
    print("  🚀 Moon.kz (moon.kz)")
    print("  📱 4mobile (4mobile.pages.dev)")

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("✅ Telegram-уведомления ВКЛЮЧЕНЫ.")
    else:
        print("ℹ️ Telegram не настроен. Алерты выводятся в консоль.")

    while True:
        try:
            await run_multi_scan_cycle(
                dns_scraper,
                shopkz_scraper,
                technodom_scraper,
                forcecom_scraper,
                sulpak_scraper,
                mechta_scraper,
                alser_scraper,
                evrika_scraper,
                moon_scraper,
                fourmobile_scraper
            )
        except Exception as e:
            print(f"Непредвиденная ошибка в основном цикле: {e}")

        if run_once:
            print("Флаг --once установлен. Завершение работы.")
            break

        print(f"Следующий круг через {CHECK_INTERVAL_SECONDS} секунд...\n")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Store KZ Price Glitch Monitor")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершить работу")
    args = parser.parse_args()

    asyncio.run(main_loop(run_once=args.once))
