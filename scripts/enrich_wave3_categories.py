#!/usr/bin/env python3
"""
Enrich Wave 3 categories (Shoes, Clothing, Toys & LEGO, Tools & Repair, Beauty, Perfume,
Furniture, PC Cases, CPUs, Consoles, Cables & Chargers, UPS & Power) in prices.db.
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
    print(f"[1/4] Synced {synced_cats} categories in master taxonomy.")

    # 2. Reclassify misrouted items
    moved_counts = {
        "starlink_to_network": 0,
        "softeners_to_household_chem": 0,
        "psu_to_psu": 0,
        "bins_to_household_chem": 0,
        "shoes_from_clothing": 0,
    }

    # 2a. Starlink in Игровые приставки -> Сетевое оборудование
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Игровые приставки' AND title LIKE '%Starlink%'"
    ).fetchall():
        _move_product(conn, r["id"], "network", "Сетевое оборудование", "device")
        moved_counts["starlink_to_network"] += 1

    # 2b. Laundry softeners in Стиральные машины -> Бытовая химия и хозтовары
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Стиральные машины'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(vernel|lenor|кондиционер\s+для\s+белья|смягчител\w*|гель\s+для\s+стирки|порошок)\b", t, re.I):
            _move_product(conn, r["id"], "household_chem", "Бытовая химия и хозтовары", "consumable")
            moved_counts["softeners_to_household_chem"] += 1

    # 2c. PC PSUs in ИБП и питание -> Блоки питания
    pc_psu_pat = re.compile(
        r"\b(atx|sfx|qdionatx|80\+|bronze|gold|platinum|titanium|deepcool|gamerstorm|ocypus|asus|msi|gigabyte|"
        r"gamemax|cougar|aerocool|chieftec|thermaltake|cooler\s*master|be\s*quiet|zalman|1stplayer|pccooler|"
        r"montech|formula|apnx|corsair|seasonic|fsp|silverstone|xpg|adata|powercase|gembird|crown|huntkey|"
        r"winard|exegate|vinga|lian\s*li|nzxt|fractal|phanteks|super\s*flower|enermax|antec|biostar)\b|"
        r"(?:[3-9]\d0|1[02356]\d0)\s*(?:w|вт\b)|"
        r"-(?:[4-9]\d0|1[02]\d0)[a-z]?\b|"
        r"\b(?:pf|pn|pk|pq|px|gm|vp|gp|gs|vx|smart|cylon|kcas|lux|proton|core|focus|prime|rm|cx|cv|hx|ax|sf|p)\s*-?\s*(?:[4-9]\d0|1[02]\d0)",
        re.I,
    )
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'ИБП и питание'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"^\s*блок\s+питания\b", t, re.I) and pc_psu_pat.search(t):
            _move_product(conn, r["id"], "psu", "Блоки питания", "device")
            moved_counts["psu_to_psu"] += 1

    # 2f. Backfill LEGO and major footwear/apparel brands in canonical_products
    lego_updated = conn.execute(
        "UPDATE canonical_products SET brand = 'LEGO' WHERE (title LIKE 'LEGO%' OR title LIKE '% LEGO %' OR title LIKE '%Конструктор LEGO%') AND (brand IS NULL OR brand != 'LEGO')"
    ).rowcount
    moved_counts["lego_brand_backfill"] = lego_updated
    for b_prefix, b_canonical in [
        ("T.TACCARDI", "T.TACCARDI"),
        ("Alessio Nesca", "Alessio Nesca"),
        ("TimeJump", "TimeJump"),
        ("Daniele Patrici", "Daniele Patrici"),
        ("Pierre Cardin", "Pierre Cardin"),
    ]:
        conn.execute(
            "UPDATE canonical_products SET brand = ? WHERE brand IS NULL AND title LIKE ?",
            (b_canonical, f"{b_prefix}%"),
        )

    # 2d. Trash bins in Мебель -> Бытовая химия и хозтовары
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Мебель'"
    ).fetchall():
        t = r["title"] or ""
        if re.search(r"\b(мусорн\w*\s+бак|ведро\s+для\s+мусора|бак\s+для\s+мусора)\b", t, re.I):
            _move_product(conn, r["id"], "household_chem", "Бытовая химия и хозтовары", "accessory")
            moved_counts["bins_to_household_chem"] += 1

    # 2e. Explicit footwear in Одежда и обувь -> Обувь
    footwear_pat = re.compile(
        r"\b(кроссовк\w*|кеды|ботинк\w*|полуботинк\w*|сапог\w*|полусапог\w*|ботильон\w*|бoтильоны|челси|туфл\w*|лофер\w*|"
        r"сандали\w*|босоножк\w*|слипон\w*|слингбэк\w*|балетк\w*|мокасин\w*|тапочк\w*|шлепанц\w*|сабо|дутик\w*|угги)\b",
        re.I,
    )
    for r in conn.execute(
        "SELECT id, title FROM canonical_products WHERE category = 'Одежда и обувь'"
    ).fetchall():
        t = r["title"] or ""
        if footwear_pat.search(t) and not re.search(r"\b(носки|следки|стельки|шнурки|крем\s+для\s+обуви|сушилка\s+для\s+обуви|ложка\s+для\s+обуви)\b", t, re.I):
            _move_product(conn, r["id"], "shoes", "Обувь", "device")
            moved_counts["shoes_from_clothing"] += 1

    conn.commit()
    print(f"[2/4] Reclassified items: {moved_counts}")

    # 3. Re-index attributes across all categories (limit=120000 to cover all 57k+ Wave 3 items)
    total_indexed = sync_all_source_attributes(conn, limit=120000)
    print(f"[3/4] Indexed {total_indexed} attributes across catalog.")

    # 4. Clear stale local_search_intents cache for Wave 3 queries
    try:
        conn.execute("DELETE FROM local_search_intents")
        conn.commit()
        print("[4/4] Cleared local_search_intents cache.")
    except Exception as exc:
        print(f"[4/4] Note clearing cache: {exc}")

    # Print summary counts
    for cat in (
        "Обувь", "Одежда и обувь", "Женская одежда", "Мужская одежда",
        "Игрушки и конструкторы", "Инструменты и ремонт", "Красота и здоровье", "Парфюмерия",
        "Мебель", "Корпуса", "Процессоры", "Игровые приставки",
        "Кабели и зарядные устройства", "ИБП и питание", "SSD накопители"
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
        print(f"  - {cat:28s}: {attr_p:5d} / {total_p:5d} products with non-color attributes")

    conn.close()


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    run_enrichment(db_file)
