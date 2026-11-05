"""
Скрипт нормализации существующих записей в таблице product_search_attributes.
Схлопывает дубликаты (16 GB, 16 Гб, 16Gb, 16G -> 16 ГБ; RTX 5080 -> GeForce RTX 5080)
и удаляет некорректные артефакты (например, storage='Да' или chipset='NVIDIA').
"""
from __future__ import annotations
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.attribute_indexing import normalize_attribute_value


def normalize_db(db_path: str = "/app/data/prices.db") -> None:
    if not os.path.exists(db_path):
        db_path = "data/prices.db"
    if not os.path.exists(db_path):
        db_path = "prices.db"

    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    rows = cursor.execute("""
        SELECT rowid, product_id, key, value, evidence, source_title
        FROM product_search_attributes
    """).fetchall()

    print(f"Total attributes in DB: {len(rows)}")

    deleted_count = 0
    updated_count = 0

    # Группируем по (product_id, key) чтобы избежать нарушения первичного ключа
    seen_keys = {}

    for r in rows:
        rowid = r["rowid"]
        pid = r["product_id"]
        key = r["key"]
        val = r["value"]

        norm = normalize_attribute_value(key, val)

        if not norm:
            cursor.execute("DELETE FROM product_search_attributes WHERE rowid = ?", (rowid,))
            deleted_count += 1
            continue

        pair = (pid, key)
        if pair in seen_keys:
            # Уже есть значение для этого товара и ключа - удаляем лишний дубликат
            cursor.execute("DELETE FROM product_search_attributes WHERE rowid = ?", (rowid,))
            deleted_count += 1
            continue

        seen_keys[pair] = norm

        if norm != val:
            cursor.execute("""
                UPDATE product_search_attributes
                SET value = ?
                WHERE rowid = ?
            """, (norm, rowid))
            updated_count += 1

    conn.commit()
    conn.close()

    print(f"Normalization finished: {updated_count} updated, {deleted_count} removed.")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    normalize_db(db)
