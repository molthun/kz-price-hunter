"""
Migration and enrichment script for Wave 2 Categories (v6.28.25):
- Monitors (Мониторы)
- Peripherals (Клавиатуры и мыши)
- Tablets (Планшеты)
- Kitchen Appliances (Техника для кухни)
- Network Equipment (Сетевое оборудование)
- Printers & MFPs (Принтеры и МФУ)
- Air Conditioners (Кондиционеры)

Also performs data hygiene:
- Synchronizes master taxonomy in categories table.
- Removes bogus diaper_type / diaper_size / weight_range attributes from non-baby categories.
- Reclassifies tablets with bundled keyboard/stylus from 'Клавиатуры и мыши' back to 'Планшеты'.
- Reclassifies toy blenders to 'Игрушки', electric planers to 'Инструменты и ремонт', office paper to 'Книги и канцелярия', and PCI/M.2 expansion cards to 'Комплектующие для ПК'.
- Cleans up stale storage/ram attributes on actual peripherals.
- Runs sync_all_source_attributes to populate attributes across all Wave 2 categories.
"""
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _move_product(conn: sqlite3.Connection, pid: str, cat_id: str, cat_name: str, item_kind: str = "device") -> None:
    conn.execute(
        "UPDATE product_category_tags SET category_id = ?, item_kind = ? WHERE product_id = ?",
        (cat_id, item_kind, pid),
    )
    conn.execute("UPDATE canonical_products SET category = ? WHERE id = ?", (cat_name, pid))


def run_enrichment(db_path: str = "/app/data/prices.db") -> int:
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return 1

    print(f"Connecting to database {db_path}...")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # 1. Sync master taxonomy
    print("1. Synchronizing master taxonomy...")
    from repositories.category_repo import CategoryRepository
    cat_repo = CategoryRepository(conn)
    synced_count = cat_repo.sync_master_taxonomy()
    print(f"   Synchronized {synced_count} categories in database.")

    # 2. Clean up bogus diaper_type / diaper_size / weight_range in non-baby categories
    print("2. Cleaning up bogus diaper attributes in non-baby categories...")
    bogus_rows = conn.execute(
        """SELECT pa.product_id, pa.key
           FROM product_search_attributes pa
           JOIN canonical_products p ON p.id = pa.product_id
           WHERE pa.key IN ('diaper_type', 'diaper_size', 'weight_range')
             AND p.category NOT IN ('Подгузники и гигиена', 'Детские товары', 'diapers_hygiene')"""
    ).fetchall()
    print(f"   Found {len(bogus_rows)} bogus diaper attribute rows outside baby categories.")
    for r in bogus_rows:
        conn.execute(
            "DELETE FROM product_search_attributes WHERE product_id = ? AND key = ?",
            (r["product_id"], r["key"]),
        )
        try:
            conn.execute(
                f"UPDATE canonical_products SET attributes_json = json_remove(COALESCE(NULLIF(attributes_json, ''), '{{}}'), '$.\"{r['key']}\"') WHERE id = ?",
                (r["product_id"],),
            )
        except Exception:
            pass

    # 3. Fix misclassified products
    print("3. Fixing misclassified products...")

    # 3a. Планшеты с клавиатурой или стилусом в разделе «Клавиатуры и мыши» -> «Планшеты»
    periph_rows = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category IN ('Клавиатуры и мыши', 'peripherals')"
    ).fetchall()
    moved_tablets = 0
    for r in periph_rows:
        pid, title = r["id"], r["title"] or ""
        if re.search(r"^\s*(?:(?:\d+(?:\.\d+)?\s*[\"”']?\s*)?планшет|комплект\s+планшет)\b", title, re.I) and not re.search(
            r"\b(графическ\w*\s+планшет|чехол|стекло|пленка)\b", title, re.I
        ):
            _move_product(conn, pid, "tablets", "Планшеты", "device")
            moved_tablets += 1
    print(f"   Moved {moved_tablets} tablets with keyboard/stylus from 'Клавиатуры и мыши' to 'Планшеты'.")

    # Delete stale storage/ram attributes from remaining actual peripherals
    conn.execute(
        """DELETE FROM product_search_attributes
           WHERE key IN ('storage', 'ram')
             AND product_id IN (SELECT id FROM canonical_products WHERE category IN ('Клавиатуры и мыши', 'peripherals'))"""
    )

    # 3b. Игрушечные блендеры и электрорубанки в «Техника для кухни»
    kitchen_rows = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category IN ('Техника для кухни', 'kitchen_appliances')"
    ).fetchall()
    moved_kitchen = 0
    for r in kitchen_rows:
        pid, title = r["id"], r["title"] or ""
        if re.search(r"\b(детск\w*\s+игрушк\w*|игрушк\w*\s+детск\w*|игрушечн\w*)\b", title, re.I):
            _move_product(conn, pid, "toys", "Игрушки", "device")
            moved_kitchen += 1
        elif re.search(r"\b(электрический\s+рубанок|электрорубанок|рубанок|перфоратор|шуруповерт|дрель|болгарка|электролобзик)\b", title, re.I):
            _move_product(conn, pid, "tools_repair", "Инструменты и ремонт", "device")
            moved_kitchen += 1
    print(f"   Moved {moved_kitchen} non-kitchen items out of 'Техника для кухни'.")

    # 3c. Офисная бумага в «Принтеры и МФУ» -> «Книги и канцелярия»
    printer_rows = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category IN ('Принтеры и МФУ', 'printers')"
    ).fetchall()
    moved_paper = 0
    for r in printer_rows:
        pid, title = r["id"], r["title"] or ""
        if re.search(r"\b(бумага\s+для\s+(?:печати|принтера|офисной)|бумага\s+офисная|бумага\s+а4)\b", title, re.I):
            _move_product(conn, pid, "books", "Книги и канцелярия", "consumable")
            moved_paper += 1
    print(f"   Moved {moved_paper} office paper items from 'Принтеры и МФУ' to 'Книги и канцелярия'.")

    # 3d. Контроллеры PCI / M.2 в «Сетевое оборудование» -> «Комплектующие для ПК»
    net_rows = conn.execute(
        "SELECT id, title FROM canonical_products WHERE category IN ('Сетевое оборудование', 'network')"
    ).fetchall()
    moved_pci = 0
    for r in net_rows:
        pid, title = r["id"], r["title"] or ""
        if re.search(r"\b(контроллер\s+pci|плата\s+расширения\s+pci|адаптер\s+(?:asus\s+)?hyper\s+m\.2)\b", title, re.I) and not re.search(
            r"\b(сетев\w*|lan|ethernet|rj45|wi-?fi|10g|2\.5g|1000\s*мбит)\b", title, re.I
        ):
            _move_product(conn, pid, "pc_components", "Комплектующие для ПК", "device")
            moved_pci += 1
    print(f"   Moved {moved_pci} PCI/M.2 controller items from 'Сетевое оборудование' to 'Комплектующие для ПК'.")

    # 4. Clear stale local_search_intents for Wave 2 queries so fresh schemas take effect
    try:
        conn.execute(
            """DELETE FROM local_search_intents
               WHERE normalized_query IN ('монитор', 'мониторы', 'клавиатура', 'мышь', 'роутер', 'маршрутизатор',
                                          'принтер', 'мфу', 'кофемашина', 'чайник', 'микроволновка', 'блендер',
                                          'кондиционер', 'планшет')"""
        )
    except Exception:
        pass

    conn.commit()

    # 5. Re-index search attributes
    print("4. Indexing attributes across all categories...")
    from domain.attribute_indexing import sync_all_source_attributes
    indexed = sync_all_source_attributes(conn, limit=100000)
    print(f"   Indexed {indexed} attributes successfully.")

    # 6. Show summary stats
    print("\nSummary of indexed attributes for Wave 2 categories:")
    for cat, keys in [
        ("Мониторы", ["screen_size", "refresh_rate", "panel_type", "resolution", "aspect_ratio", "curved"]),
        ("Клавиатуры и мыши", ["type", "connection_type", "keyboard_type", "sensor_dpi", "color"]),
        ("Планшеты", ["screen_size", "storage", "ram", "color"]),
        ("Техника для кухни", ["type", "power", "capacity", "color"]),
        ("Сетевое оборудование", ["type", "wifi_standard", "ports_count", "lan_speed", "poe"]),
        ("Принтеры и МФУ", ["type", "print_tech", "print_color", "paper_format", "connectivity"]),
        ("Кондиционеры", ["type", "power_btu", "area_sqm", "inverter", "color"]),
    ]:
        print(f"\n[{cat}]")
        for k in keys:
            row = conn.execute(
                """SELECT COUNT(DISTINCT pa.product_id) as p_cnt, COUNT(*) as a_cnt
                   FROM product_search_attributes pa
                   JOIN canonical_products p ON p.id = pa.product_id
                   WHERE p.category = ? AND pa.key = ?""",
                (cat, k),
            ).fetchone()
            print(f"  {k}: {row['p_cnt']} products ({row['a_cnt']} values)")

    conn.close()
    print("\nWave 2 enrichment complete.")
    return 0


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    sys.exit(run_enrichment(db))
