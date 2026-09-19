#!/usr/bin/env python3
"""Безопасный сброс каталога товаров KZ Price Hunter.

1. Создаёт резервную копию SQLite базы.
2. Очищает накопленные товары (products), историю цен (price_observations), алерты (alerts) и кэш нормализации.
3. Сохраняет в полной неприкосновенности пользователей (users) и сессии (sessions).
4. Инициализирует постоянную HOT-категорию «🔥 Все акции и распродажи».
5. Выполняет сжатие базы (VACUUM).
"""

import os
import sys
import shutil
import time
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import database
from database import get_connection


def reset_catalog(backup: bool = True) -> dict:
    db_file = Path(database.DB_PATH)
    backup_path = None

    if not db_file.exists():
        return {"status": "error", "message": f"База данных не найдена: {db_file}"}

    # 1. Резервная копия
    if backup:
        ts = int(time.time())
        backup_path = db_file.with_name(f"{db_file.name}.backup_{ts}")
        shutil.copy2(db_file, backup_path)
        print(f"[Reset] 💾 Создан бэкап: {backup_path}")

    # 2. Очистка каталога
    with get_connection() as conn:
        cursor = conn.cursor()

        # Подсчитываем удаляемое
        prod_count = cursor.execute("SELECT count(*) FROM products").fetchone()[0]
        obs_count = cursor.execute("SELECT count(*) FROM price_observations").fetchone()[0]
        alert_count = cursor.execute("SELECT count(*) FROM alerts").fetchone()[0]

        # Пользователи и сессии (проверяем сохранность)
        users_count = cursor.execute("SELECT count(*) FROM users").fetchone()[0]
        sessions_count = cursor.execute("SELECT count(*) FROM sessions").fetchone()[0]

        # Очистка
        cursor.execute("DELETE FROM products")
        cursor.execute("DELETE FROM price_observations")
        cursor.execute("DELETE FROM alerts")
        try:
            cursor.execute("DELETE FROM title_canonical_cache")
        except sqlite3.OperationalError:
            pass

        cursor.execute("DELETE FROM tracked_categories")

        # Сброс волн сканирования к началу
        try:
            cursor.execute("UPDATE scheduler_lease SET expires_at = 0")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("UPDATE schema_metadata SET value = '0' WHERE name IN ('wave_index', 'wave_cycle')")
        except sqlite3.OperationalError:
            pass

        # 3. Инициализация постоянной базовой HOT-категории «🔥 Все акции и распродажи»
        cursor.execute("""
            INSERT INTO tracked_categories (name, query, master_category, search_count, last_searched_at, is_active, is_hot)
            VALUES ('🔥 Все акции и распродажи', 'акции', 'actions', 100, CURRENT_TIMESTAMP, 1, 1)
        """)

        conn.commit()

        # 4. VACUUM
        cursor.execute("VACUUM")
        conn.commit()

    result = {
        "status": "ok",
        "backup_path": str(backup_path) if backup_path else None,
        "deleted_products": prod_count,
        "deleted_observations": obs_count,
        "deleted_alerts": alert_count,
        "preserved_users": users_count,
        "preserved_sessions": sessions_count,
    }
    print(f"[Reset] ✅ Каталог очищен: удалено {prod_count} товаров, {obs_count} наблюдений цен, {alert_count} алертов.")
    print(f"[Reset] 🛡 Сохранено пользователей: {users_count}, активных сессий: {sessions_count}.")
    print("[Reset] 🔥 Создана базовая категория «🔥 Все акции и распродажи» (is_hot=1).")
    return result


if __name__ == "__main__":
    res = reset_catalog(backup=True)
    print("Готово:", res)
