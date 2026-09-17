"""Консольный режим мониторинга: тот же обход магазинов, что и в веб-панели.

Веб-панель (`gui.py`) — основной режим. Этот скрипт удобен для разового прогона
и отладки: он использует тот же реестр магазинов, детектор и рассылку уведомлений.
"""
import asyncio
import argparse
from datetime import datetime

from config import CHECK_INTERVAL_SECONDS, get_bot_token, load_settings
from database import init_db, get_stats, get_shops_scan_report
from web.server import SHOP_REGISTRY, enabled_shop_keys, _do_scan_task, scan_state

async def run_cycle():
    keys = enabled_shop_keys()
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚀 Старт обхода {len(keys)} магазинов...")
    await _do_scan_task(keys)

    stats = get_stats()
    print("\n" + "=" * 60)
    print("🏁 Цикл мониторинга завершен!")
    print(f"Товаров обработано за цикл: {scan_state['total_scanned']}")
    print(f"Новых аномалий найдено: {scan_state['anomalies_found']}")
    print(f"Всего товаров в базе: {stats['total_products']}")
    for row in get_shops_scan_report(keys):
        name = SHOP_REGISTRY[row["shop_key"]][2]
        status = row["last_error"] or "ок"
        print(f"  {name}: {row['last_items']} товаров, {row['last_duration_sec']}с — {status}")
    print("=" * 60 + "\n")

async def main_loop(run_once: bool = False):
    init_db()

    print("🤖 KZ Price Hunter — консольный мониторинг запущен!")
    print("Магазины из настроек:", ", ".join(SHOP_REGISTRY[k][2] for k in enabled_shop_keys()))
    if get_bot_token():
        print("✅ Telegram-бот настроен: алерты рассылаются пользователям с включенными уведомлениями.")
    else:
        print("ℹ️ TELEGRAM_BOT_TOKEN не задан. Алерты выводятся в консоль.")

    while True:
        try:
            await run_cycle()
        except Exception as e:
            print(f"Непредвиденная ошибка в основном цикле: {e}")

        if run_once:
            print("Флаг --once установлен. Завершение работы.")
            break

        interval = load_settings().get("check_interval_seconds", CHECK_INTERVAL_SECONDS)
        print(f"Следующий круг через {interval} секунд...\n")
        await asyncio.sleep(interval)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="KZ Price Hunter — консольный мониторинг цен")
    parser.add_argument("--once", action="store_true", help="Выполнить один цикл и завершить работу")
    args = parser.parse_args()

    asyncio.run(main_loop(run_once=args.once))
