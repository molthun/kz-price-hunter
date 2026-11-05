"""
Скрипт исправления ошибочно классифицированных ноутбуков, попавших в «Видеокарты» и «SSD накопители».
Синхронизирует canonical_products, product_category_tags и очищает искаженные атрибуты GPU (чипсет, vram, interface и т.д.).
"""
from __future__ import annotations
import sqlite3
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog_classifier import CatalogClassifier
from domain.attribute_indexing import extract_title_attributes, normalize_attribute_value


def fix_catalog(db_path: str = "/app/data/prices.db") -> None:
    if not os.path.exists(db_path):
        db_path = "data/prices.db"
    if not os.path.exists(db_path):
        db_path = "prices.db"

    print(f"Connecting to database: {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    clf = CatalogClassifier()

    # 1. Поиск ошибочных товаров в Видеокартах, SSD накопителях и Комплектующих для ПК
    rows = cursor.execute("""
        SELECT id, title, category FROM canonical_products
        WHERE category IN ('Видеокарты', 'SSD накопители', 'Комплектующие для ПК')
    """).fetchall()

    print(f"Examining {len(rows)} products in target categories...")

    moved_laptops = 0
    moved_stands = 0
    moved_captures = 0
    cleaned_attrs_total = 0

    for r in rows:
        pid = r["id"]
        title = r["title"]
        old_cat = r["category"]

        res = clf.classify_rules(title)
        if not res:
            continue

        target_cat_name = None
        target_cat_id = None

        if res.category_id == 'laptops' and old_cat in ('Видеокарты', 'SSD накопители', 'Комплектующие для ПК'):
            target_cat_name = 'Ноутбуки'
            target_cat_id = 'laptops'
            moved_laptops += 1
        elif res.category_id == 'pc_cases' and old_cat == 'Видеокарты':
            target_cat_name = 'Корпуса'
            target_cat_id = 'pc_cases'
            moved_stands += 1
        elif res.category_id == 'pc_components' and old_cat == 'Видеокарты':
            target_cat_name = 'Комплектующие для ПК'
            target_cat_id = 'pc_components'
            moved_captures += 1

        if target_cat_name and target_cat_id:
            # 1. Обновляем product_category_tags (с учетом триггера classification_category_guard)
            cursor.execute("UPDATE product_category_tags SET category_id = ? WHERE product_id = ?", (target_cat_id, pid))
            if cursor.rowcount == 0:
                cursor.execute("INSERT OR REPLACE INTO product_category_tags (product_id, category_id) VALUES (?, ?)", (pid, target_cat_id))

            # 2. Обновляем canonical_products
            cursor.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (target_cat_name, pid))

            # 3. Чистим неверные атрибуты GPU
            del_keys = ['chipset', 'vram', 'interface', 'clock_speed', 'diaper_type', 'socket', 'form_factor', 'power']
            placeholders = ','.join('?' for _ in del_keys)
            cursor.execute(f"DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ({placeholders})", [pid] + del_keys)
            cleaned_attrs_total += cursor.rowcount

            # 4. Если это ноутбук, извлекаем актуальные атрибуты (RAM, storage, экран, цвет, CPU)
            if target_cat_id == 'laptops':
                attrs = extract_title_attributes(title)
                for k, v in attrs.items():
                    if k in ('ram', 'storage', 'screen_size', 'color', 'cpu'):
                        norm_v = normalize_attribute_value(k, v)
                        if norm_v:
                            cursor.execute("""
                                INSERT OR REPLACE INTO product_search_attributes
                                (product_id, key, value, evidence, source_title, updated_at)
                                VALUES (?, ?, ?, ?, ?, datetime('now'))
                            """, (pid, k, norm_v, f"title:{v}", title))

    # 2. Дополнительная зачистка любых некорректных чипсетов (где ошибочно записаны CPU)
    stray_chipsets = cursor.execute("""
        SELECT rowid, value FROM product_search_attributes
        WHERE key = 'chipset' AND (
            value LIKE '%Core Ultra%' OR
            value LIKE '%Ryzen%' OR
            value LIKE '%Intel Core%' OR
            value LIKE '%275HX%' OR
            value LIKE '%NVIDIA%' OR
            value LIKE '%AMD%'
        )
    """).fetchall()

    stray_deleted = 0
    for sc in stray_chipsets:
        # Проверяем через нормализатор
        norm = normalize_attribute_value('chipset', sc['value'])
        if not norm:
            cursor.execute("DELETE FROM product_search_attributes WHERE rowid = ?", (sc['rowid'],))
            stray_deleted += 1

    conn.commit()
    conn.close()

    print("\n--- Summary ---")
    print(f"Laptops moved to 'Ноутбуки': {moved_laptops}")
    print(f"GPU stands moved to 'Корпуса': {moved_stands}")
    print(f"Capture cards moved to 'Комплектующие для ПК': {moved_captures}")
    print(f"Corrupted attributes deleted from moved items: {cleaned_attrs_total}")
    print(f"Stray invalid chipsets deleted: {stray_deleted}")
    print("Catalog cleanup complete!")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    fix_catalog(db)
