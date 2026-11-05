"""
Скрипт миграции категорий комплектующих для ПК (БП, корпуса, охлаждение) и характеристик (KZPH-G5-07).
Обновляет категории ~4,200 товаров из общей 'Комплектующие для ПК' в специализированные:
- 'Блоки питания' (psu)
- 'Корпуса' (pc_cases)
- 'Охлаждение и кулеры' (cooling)
- 'Видеокарты' (gpus)
- 'Процессоры' (cpus)
Синхронизирует product_category_tags (с учетом триггера classification_category_guard).
Удаляет ошибочный атрибут format: LP для кулеров/железа и индексирует мощность, форм-фактор и тип охлаждения.
"""
from __future__ import annotations
import sqlite3
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from repositories.category_repo import CategoryRepository
from domain.attribute_indexing import extract_title_attributes, index_product_facts


def migrate(db_path: str = "/app/data/prices.db") -> None:
    if not os.path.exists(db_path):
        db_path = "data/prices.db"
    if not os.path.exists(db_path):
        db_path = "prices.db"

    print(f"Connecting to {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Синхронизируем мастер-таксономию
    print("1. Syncing master taxonomy...")
    cat_repo = CategoryRepository(conn)
    synced = cat_repo.sync_master_taxonomy()
    conn.commit()
    print(f"   Synced {synced} categories.")

    # 2. Выбираем товары из Комплектующие для ПК
    cursor = conn.cursor()
    products = cursor.execute("""
        SELECT id, title, category FROM canonical_products
        WHERE category = 'Комплектующие для ПК'
    """).fetchall()
    print(f"2. Found {len(products)} products in 'Комплектующие для ПК'")

    counts = {
        'Блоки питания': 0,
        'Корпуса': 0,
        'Охлаждение и кулеры': 0,
        'Видеокарты': 0,
        'Процессоры': 0,
        'Осталось в Комплектующие для ПК': 0
    }

    updated_ids = []

    for p in products:
        pid = p["id"]
        title = p["title"]
        tl = title.lower()

        target_info = None  # (name, id)
        # 1. Защита: проверяем, не ноутбук ли это
        clf_res = CatalogClassifier().classify_rules(title)
        if clf_res and clf_res.category_id == 'laptops':
            target_info = ('Ноутбуки', 'laptops')
        elif any(k in tl for k in ['держатель', 'подставка для видеокарт', 'кронштейн', 'gpu stand']):
            target_info = ('Корпуса', 'pc_cases')
        elif any(k in tl for k in ['видеозахват', 'avermedia']):
            target_info = ('Комплектующие для ПК', 'pc_components')
        elif 'блок питания' in tl or re.search(r'\bбп\s+\d+', tl) or 'power supply' in tl:
            target_info = ('Блоки питания', 'psu')
        elif any(k in tl for k in ['компьютерный корпус', 'корпус для пк', 'корпус пк', 'корпус']) or tl.startswith('корпус'):
            target_info = ('Корпуса', 'pc_cases')
        elif any(k in tl for k in ['кулер', 'охлаждени', 'вентилятор', 'сжо', 'сво', 'термопаст', 'cpu cooler']):
            target_info = ('Охлаждение и кулеры', 'cooling')
        elif 'видеокарт' in tl or 'geforce' in tl or 'radeon' in tl:
            target_info = ('Видеокарты', 'gpus')
        elif 'процессор' in tl or 'intel core' in tl or 'amd ryzen' in tl:
            target_info = ('Процессоры', 'cpus')

        if target_info:
            cat_name, cat_id = target_info
            counts[cat_name] += 1
            cursor.execute("UPDATE product_category_tags SET category_id = ? WHERE product_id = ?", (cat_id, pid))
            cursor.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (cat_name, pid))
            updated_ids.append((pid, title))
        else:
            counts['Осталось в Комплектующие для ПК'] += 1

    conn.commit()
    print("   Reclassification results:", counts)

    # 3. Индексируем атрибуты для обновленных товаров
    print("3. Indexing attributes (power, form_factor, cooling type, socket)...")
    indexed_facts = 0
    for pid, title in updated_ids:
        attrs = extract_title_attributes(title)
        facts = [{'key': k, 'value': v, 'source_title': title} for k, v in attrs.items()]
        if facts:
            indexed_facts += index_product_facts(conn, pid, facts, product_title=title)

    conn.commit()
    print(f"   Indexed {indexed_facts} search attribute facts.")

    # 4. Удаляем ошибочные записи format='LP' у комплектующих ПК
    print("4. Cleaning false-positive 'format: LP' from PC components...")
    deleted = cursor.execute("""
        DELETE FROM product_search_attributes
        WHERE key = 'format' AND product_id IN (
            SELECT p.id FROM canonical_products p
            WHERE p.category IN ('Блоки питания', 'Корпуса', 'Охлаждение и кулеры', 'Комплектующие для ПК', 'Видеокарты', 'Процессоры')
               OR p.title LIKE '%Кулер%' OR p.title LIKE '%охлажден%' OR p.title LIKE '%корпус%'
        )
    """).rowcount
    conn.commit()
    print(f"   Removed {deleted} false-positive 'format' entries.")

    conn.close()
    print("Migration finished successfully!")


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    migrate(db)
