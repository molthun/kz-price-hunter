"""
Migration and enrichment script for Refrigerators, Washing Machines, Vacuums and Tires (v6.28.23).
- Synchronizes master taxonomy in categories table.
- Fixes misclassified vacuum sealers (to kitchen_appliances) and chainsaw bars (to tools_repair).
- Runs sync_all_source_attributes to populate attributes in product_search_attributes.
"""
import sys
import os
import json
import sqlite3
from typing import Optional

def run_enrichment(db_path: str = "/app/data/prices.db"):
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

    # 2. Fix misclassified products
    print("2. Fixing misclassified products...")
    
    # 2a. Вакууматоры -> kitchen_appliances
    cur = conn.execute(
        "SELECT id, title, category FROM canonical_products WHERE title LIKE '%вакууматор%' OR title LIKE '%вакуумный упаковщик%'"
    )
    vac_sealers = cur.fetchall()
    print(f"   Found {len(vac_sealers)} vacuum sealers.")
    for r in vac_sealers:
        pid, title, cat = r['id'], r['title'], r['category']
        if cat in ('vacuums', 'Пылесосы'):
            # Update product_category_tags first (avoids classification_category_guard conflict)
            conn.execute("UPDATE product_category_tags SET category_id = 'kitchen_appliances', item_kind = 'device' WHERE product_id = ?", (pid,))
            conn.execute("UPDATE canonical_products SET category = 'Техника для кухни' WHERE id = ?", (pid,))
            # Delete vacuum-specific attributes if any
            conn.execute("DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ('suction_power', 'dust_collector_type', 'cleaning_type')", (pid,))
            print(f"   Moved to kitchen_appliances: {title[:50]}...")

    # 2b. Пильные шины -> tools_repair
    cur = conn.execute(
        "SELECT id, title, category FROM canonical_products WHERE title LIKE '%шина для пил%' OR title LIKE '%пильная шина%'"
    )
    chainsaw_bars = cur.fetchall()
    print(f"   Found {len(chainsaw_bars)} chainsaw bars.")
    for r in chainsaw_bars:
        pid, title, cat = r['id'], r['title'], r['category']
        if cat in ('tires', 'Шины и диски'):
            conn.execute("UPDATE product_category_tags SET category_id = 'tools_repair', item_kind = 'accessory' WHERE product_id = ?", (pid,))
            conn.execute("UPDATE canonical_products SET category = 'Инструменты и ремонт' WHERE id = ?", (pid,))
            conn.execute("DELETE FROM product_search_attributes WHERE product_id = ? AND key IN ('diameter', 'tire_width', 'tire_profile', 'tire_size', 'season')", (pid,))
            print(f"   Moved to tools_repair: {title[:50]}...")

    conn.commit()

    # 3. Re-index search attributes
    print("3. Indexing attributes for appliances and tires...")
    from domain.attribute_indexing import sync_all_source_attributes
    indexed = sync_all_source_attributes(conn, limit=100000)
    print(f"   Indexed {indexed} attributes successfully.")

    # 4. Show summary stats
    print("\nSummary of indexed attributes:")
    for cat, keys in [
        ("Холодильники", ["refrigeration_defrost", "doors_type", "height_cm", "compressor_type"]),
        ("Стиральные машины", ["load_capacity", "drying", "spin_speed", "depth_type", "motor_type"]),
        ("Пылесосы", ["type", "cleaning_type", "dust_collector_type", "suction_power"]),
        ("Шины и диски", ["diameter", "season", "tire_width", "tire_size"]),
    ]:
        print(f"\n[{cat}]")
        for k in keys:
            row = conn.execute(
                """SELECT COUNT(DISTINCT pa.product_id) as p_cnt, COUNT(*) as a_cnt
                   FROM product_search_attributes pa
                   JOIN canonical_products p ON p.id = pa.product_id
                   WHERE (p.category = ? OR p.category IN (SELECT name FROM categories WHERE id = ?)) AND pa.key = ?""",
                (cat, cat, k)
            ).fetchone()
            print(f"  {k}: {row['p_cnt']} products ({row['a_cnt']} values)")

    conn.close()
    print("\nEnrichment complete.")
    return 0

if __name__ == '__main__':
    db = sys.argv[1] if len(sys.argv) > 1 else "/app/data/prices.db"
    sys.exit(run_enrichment(db))
