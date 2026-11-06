#!/usr/bin/env python3
"""
Enrich Wave 5 categories (Food, Drinks, Dairy, Meat & Poultry, Medicine & Vitamins,
Household Chem, Kids, Baby Care, Baby Food, Strollers, Makeup, Haircare, Skincare,
Auto, Phone Cases, Printer Ink, Tourism & Camping, Board Games, Photo & Video, Electronics)
in prices.db.
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

    # 1b. Purge any leaked test sellers from test_seller_admin.py
    test_sids = [
        r["id"] for r in conn.execute(
            "SELECT id FROM sellers WHERE name IN ('Тест Стор Алматы', 'Новый Магазин', 'Тест Магазин', 'Тест Стор Обновленный') OR domain = 'test-store.kz'"
        ).fetchall()
    ]
    if test_sids:
        ph = ",".join("?" for _ in test_sids)
        for tbl in ("offer_price_history", "offers", "social_sources", "content_observations", "source_profiles", "seller_identities", "channels"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE seller_id IN ({ph})", test_sids)
            except Exception:
                pass
        conn.execute(f"DELETE FROM sellers WHERE id IN ({ph})", test_sids)
        print(f"[1b/5] Purged {len(test_sids)} test sellers: {test_sids}")

    # 2. Reclassify misrouted items in Wave 5 categories
    moved_counts = {
        "doll_strollers_to_toys": 0,
        "water_in_dairy_to_drinks": 0,
        "toothpaste_in_medicine_to_beauty": 0,
        "electronics_bags_cases_to_phone_cases": 0,
        "electronics_wall_clocks_to_home_textile": 0,
        "face_toner_in_printer_ink_to_skincare": 0,
        "water_filter_in_printer_ink_to_kitchen_appliances": 0,
        "sunglasses_in_phone_cases_to_clothing": 0,
        "vinyl_in_photo_video_to_vinyl": 0,
        "brands_backfilled": 0,
    }

    # 2a. Doll strollers in Коляски и автокресла -> Игрушки и конструкторы
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Коляски и автокресла'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(для\s+кукол|для\s+куклы|игрушечн\w*)\b", t, re.I):
            _move_product(conn, r["id"], "toys", "Игрушки и конструкторы", "device")
            moved_counts["doll_strollers_to_toys"] += 1

    # 2b. Mineral/drinking water in Молочные продукты -> Напитки и соки
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Молочные продукты'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"^\s*вода\s+|\b(минеральн\w*\s+вод\w*|боржоми|borjomi|samal|tassay)\b", t, re.I):
            _move_product(conn, r["id"], "drinks", "Напитки и соки", "consumable")
            moved_counts["water_in_dairy_to_drinks"] += 1

    # 2c. Toothpaste/oral care in Лекарства и витамины -> Красота и здоровье
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Лекарства и витамины'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(паста\s+зубная|зубн\w*\s+паст\w*|зубн\w*\s+щетк\w*|щетка\s+зубная)\b", t, re.I):
            _move_product(conn, r["id"], "beauty", "Красота и здоровье", "consumable")
            moved_counts["toothpaste_in_medicine_to_beauty"] += 1

    # 2d. Universal tablet/ebook cases, laptop bags, and wall clocks in Электроника
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Электроника'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(сумк\w*\s+для\s+(?:ноутбук\w*|планшет\w*|электронных\s+устройств)|рюкзак\s+для\s+ноутбук\w*|чехол\s+для\s+(?:планшет\w*|ноутбук\w*|электронн\w*\s+книг\w*))\b", t, re.I):
            _move_product(conn, r["id"], "phone_cases", "Чехлы и бамперы", "accessory")
            moved_counts["electronics_bags_cases_to_phone_cases"] += 1
        elif re.search(r"\b(часы\s+настенн\w*|настенн\w*\s+часы)\b", t, re.I):
            _move_product(conn, r["id"], "home_textile", "Текстиль и декор", "device")
            moved_counts["electronics_wall_clocks_to_home_textile"] += 1

    # 2e. Cosmetic face toners & water filter cartridges in Картриджи и чернила
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Картриджи и чернила'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(для\s+лица|двухфазн\w*\s+тонер|face\s+toner)\b", t, re.I):
            _move_product(conn, r["id"], "skincare", "Уход за кожей", "consumable")
            moved_counts["face_toner_in_printer_ink_to_skincare"] += 1
        elif re.search(r"\b(для\s+(?:воды|фильтра|кувшина|очистки\s+воды|механической\s+очистки)|аквафор|барьер|гейзер|аквабрайт)\b", t, re.I):
            _move_product(conn, r["id"], "kitchen_appliances", "Техника для кухни", "consumable")
            moved_counts["water_filter_in_printer_ink_to_kitchen_appliances"] += 1

    # 2f. Sunglasses in Чехлы и бамперы -> Одежда и обувь
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Чехлы и бамперы'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(солнцезащитн\w*\s+очк\w*|очки\s+солнцезащитн\w*)\b", t, re.I):
            _move_product(conn, r["id"], "clothing", "Одежда и обувь", "accessory")
            moved_counts["sunglasses_in_phone_cases_to_clothing"] += 1

    # 2g. Vinyl records in Фото и видео -> Виниловые пластинки
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Фото и видео'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(vinyl|\d*lp)\b", t, re.I):
            _move_product(conn, r["id"], "vinyl", "Виниловые пластинки", "device")
            moved_counts["vinyl_in_photo_video_to_vinyl"] += 1

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

    print("[5/5] Summary of Wave 5 Categories:")
    for cat in (
        "Продукты питания", "Напитки и соки", "Молочные продукты", "Мясо и птица",
        "Лекарства и витамины", "Бытовая химия и хозтовары",
        "Детские товары", "Товары для малышей", "Коляски и автокресла",
        "Декоративная косметика", "Уход за волосами", "Уход за кожей", "Красота и здоровье",
        "Автотовары", "Чехлы и бамперы", "Картриджи и чернила",
        "Туризм и кемпинг", "Настольные игры", "Фото и видео",
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
        pct_attr = (attr_p / total_p * 100.0) if total_p else 0.0
        pct_brand = (brand_p / total_p * 100.0) if total_p else 0.0
        print(f"  - {cat:28s}: total={total_p:5d} | non-color_attrs={attr_p:5d} ({pct_attr:5.1f}%) | brands={brand_p:5d} ({pct_brand:5.1f}%)")

    conn.close()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prices.db")
    run_enrichment(db_file)
