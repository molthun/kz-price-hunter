#!/usr/bin/env python3
"""
Enrich Wave 4 categories (Headphones, Smartwatches, PC Desktops, Motherboards, Cooling,
Audio, Books & Stationery, Pet Supplies, Home & Garden, Kitchenware, Appliances, Tools,
Furniture, Vacuums, Refrigerators) in prices.db.
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog_classifier import KNOWN_BRANDS, _BRAND_PATTERN
from domain.attribute_indexing import sync_all_source_attributes
from repositories.category_repo import CategoryRepository


def _move_product(conn: sqlite3.Connection, pid: str, cat_id: str, cat_name: str, kind: str = "device") -> None:
    conn.execute("UPDATE product_category_tags SET category_id = ? WHERE product_id = ?", (cat_id, pid))
    conn.execute("UPDATE canonical_products SET category = ?, item_kind = ? WHERE id = ?", (cat_name, kind, pid))


def run_enrichment(db_path: str) -> None:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    # 1. Sync taxonomy schemas
    cat_repo = CategoryRepository(conn)
    synced_cats = cat_repo.sync_master_taxonomy()
    print(f"[1/5] Synced {synced_cats} categories in master taxonomy.")

    # 2. Reclassify misrouted items in generic buckets
    moved_counts = {
        "scales_to_beauty": 0,
        "turkas_pizzamakers_to_kitchen_appliances": 0,
        "pressure_washers_to_tools": 0,
        "electronics_to_cables_chargers": 0,
        "electronics_to_phone_cases": 0,
        "pets_to_cat_food": 0,
        "pets_to_dog_food": 0,
        "brands_backfilled": 0,
    }

    # 2a. Floor scales in Бытовая техника -> Красота и здоровье
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Бытовая техника'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(весы\s+напольн\w*|напольн\w*\s+весы|умные\s+весы|весы\s+tefal)\b", t, re.I):
            _move_product(conn, r["id"], "beauty", "Красота и здоровье", "device")
            moved_counts["scales_to_beauty"] += 1
        elif re.search(r"\b(пиццамейкер|турк\w*\s+электрическ\w*|электрическ\w*\s+турк\w*)\b", t, re.I):
            _move_product(conn, r["id"], "kitchen_appliances", "Техника для кухни", "device")
            moved_counts["turkas_pizzamakers_to_kitchen_appliances"] += 1

    # 2b. Electric turkas in Посуда и кухня -> Техника для кухни
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Посуда и кухня'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(турк\w*\s+электрическ\w*|электрическ\w*\s+турк\w*|электротурк\w*)\b", t, re.I):
            _move_product(conn, r["id"], "kitchen_appliances", "Техника для кухни", "device")
            moved_counts["turkas_pizzamakers_to_kitchen_appliances"] += 1

    # 2c. High-pressure washers in Пылесосы -> Инструменты и ремонт
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Пылесосы'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(минимойк\w*\s+высокого\s+давления|мойк\w*\s+высокого\s+давления|аппарат\s+высокого\s+давления)\b", t, re.I):
            _move_product(conn, r["id"], "tools_repair", "Инструменты и ремонт", "device")
            moved_counts["pressure_washers_to_tools"] += 1

    # 2d. Batteries, USB hubs, powerbanks, tablet cases in Электроника
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Электроника'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(набор\s+батареек|батарейк\w*\s+(?:литиевая|алкалиновая|щелочная|солевая)|аккумулятор\s+внешний|usb[- ]hub|юсб\s+хаб|разветвитель\s+usb)\b", t, re.I):
            _move_product(conn, r["id"], "cables_chargers", "Кабели и зарядные устройства", "accessory")
            moved_counts["electronics_to_cables_chargers"] += 1
        elif re.search(r"\b(чехол\s+для\s+планшет\w*|чехол-книжка\s+для)\b", t, re.I):
            _move_product(conn, r["id"], "phone_cases", "Чехлы и бамперы", "accessory")
            moved_counts["electronics_to_phone_cases"] += 1

    # 2e. Cat food & Dog food in Зоотовары
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Зоотовары'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(корм\s+для\s+кошек|корм\s+для\s+котят|корм\s+«?felix|корм\s+«?kitekat|корм\s+«?whiskas|корм\s+«?sheba|корм\s+«?gourmet|корм\s+happy\s+cat|корм\s+carny|наполнитель\s+для\s+кошачьего|когтеточк\w*)\b", t, re.I):
            _move_product(conn, r["id"], "cat_food", "Товары для кошек", "consumable")
            moved_counts["pets_to_cat_food"] += 1
        elif re.search(r"\b(корм\s+для\s+собак|корм\s+для\s+щенков|корм\s+happy\s+dog|\bhd\s+корм\s+для\s+собак|корм\s+«?pedigree|корм\s+«?chappi|лакомство\s+для\s+собак)\b", t, re.I):
            _move_product(conn, r["id"], "dog_food", "Товары для собак", "consumable")
            moved_counts["pets_to_dog_food"] += 1

    # 3. Backfill brands on canonical_products where brand IS NULL
    null_brand_rows = conn.execute(
        "SELECT id, title FROM canonical_products WHERE brand IS NULL OR brand = ''"
    ).fetchall()
    brand_updates = []
    for r in null_brand_rows:
        t = r["title"] or ""
        m = _BRAND_PATTERN.search(t)
        if m:
            matched_key = m.group(1).lower()
            canonical_brand = KNOWN_BRANDS.get(matched_key)
            if canonical_brand:
                brand_updates.append((canonical_brand, r["id"]))
    if brand_updates:
        conn.executemany("UPDATE canonical_products SET brand = ? WHERE id = ?", brand_updates)
        moved_counts["brands_backfilled"] = len(brand_updates)

    conn.commit()
    print(f"[2/5] Reclassified & backfilled items: {moved_counts}")

    # 4. Re-index attributes across all categories (limit=130000 to cover full 106k+ catalog)
    total_indexed = sync_all_source_attributes(conn, limit=130000)
    print(f"[3/5] Indexed {total_indexed} attributes across catalog.")

    # 5. Clear stale local_search_intents cache
    try:
        conn.execute("DELETE FROM local_search_intents")
        conn.commit()
        print("[4/5] Cleared local_search_intents cache.")
    except Exception as exc:
        print(f"[4/5] Note clearing cache: {exc}")

    print("[5/5] Summary of Wave 4 & Boosted Categories:")
    for cat in (
        "Наушники", "Смарт-часы", "Компьютеры и ПК", "Материнские платы", "Охлаждение и кулеры",
        "Аудиотехника", "Инструменты и ремонт", "Мебель", "Пылесосы", "Холодильники",
        "Бытовая техника", "Посуда и кухня", "Дом и сад", "Текстиль и декор", "Сад и огород",
        "Книги и канцелярия", "Книги", "Товары для кошек", "Товары для собак", "Зоотовары",
    ):
        total_p = conn.execute("SELECT COUNT(*) FROM canonical_products WHERE category = ?", (cat,)).fetchone()[0]
        attr_p = conn.execute(
            """
            SELECT COUNT(DISTINCT pa.product_id)
            FROM product_search_attributes pa
            JOIN canonical_products cp ON cp.id = pa.product_id
            WHERE cp.category = ? AND pa.key != 'color'
            """,
            (cat,),
        ).fetchone()[0]
        brand_p = conn.execute(
            "SELECT COUNT(*) FROM canonical_products WHERE category = ? AND brand IS NOT NULL AND brand != ''",
            (cat,),
        ).fetchone()[0]
        pct = (attr_p * 100.0 / total_p) if total_p else 0.0
        bpct = (brand_p * 100.0 / total_p) if total_p else 0.0
        print(f"  - {cat:28s}: {attr_p:5d} / {total_p:5d} ({pct:5.1f}%) non-color | {brand_p:5d} ({bpct:5.1f}%) brand")

    conn.close()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    run_enrichment(db_file)
