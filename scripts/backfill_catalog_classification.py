#!/usr/bin/env python3
"""
Backfill Catalog Classification & Brand Extraction for KZ Price Hunter.

Classifies existing canonical products in SQLite prices.db into clean canonical categories
and extracts brands using CatalogClassifier (Rules + Local AI).
"""
import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_classifier import CatalogClassifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_catalog")


def backfill_classification(
    db_path: str,
    batch_size: int = 2000,
    force_all: bool = False,
    rebuild_fts: bool = True,
) -> None:
    logger.info(f"Opening database: {db_path}")
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    conn.execute("PRAGMA journal_mode = WAL")

    # Ensure columns exist
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(canonical_products)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "item_kind" not in existing_cols:
        logger.info("Adding column item_kind to canonical_products...")
        conn.execute("ALTER TABLE canonical_products ADD COLUMN item_kind TEXT DEFAULT 'device'")
        conn.commit()
    if "model" not in existing_cols:
        logger.info("Adding column model to canonical_products...")
        conn.execute("ALTER TABLE canonical_products ADD COLUMN model TEXT")
        conn.commit()

    conn.execute("CREATE INDEX IF NOT EXISTS idx_canonical_products_model ON canonical_products(model)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canonical_products_kind ON canonical_products(item_kind)")
    conn.commit()

    classifier = CatalogClassifier()

    extra_where = "" if force_all else "AND (model IS NULL OR model = '' OR brand IS NULL OR brand = '' OR category LIKE '%:%' OR category = '')"
    count_sql = f"SELECT count(*) FROM canonical_products WHERE 1=1 {extra_where}"
    total_candidates = conn.execute(count_sql).fetchone()[0]
    logger.info(f"Total products to classify: {total_candidates}")

    if total_candidates == 0:
        logger.info("Nothing to backfill. Exiting.")
        conn.close()
        return

    updated_count = 0
    t0 = time.time()
    last_rowid = 0
    category_stats = {}
    brand_stats = {}
    model_stats = {}
    kind_stats = {}

    while True:
        rows = conn.execute(
            f"SELECT rowid, id, title, category, brand, model FROM canonical_products WHERE rowid > ? {extra_where} ORDER BY rowid ASC LIMIT ?",
            (last_rowid, batch_size)
        ).fetchall()
        if not rows:
            break

        last_rowid = rows[-1]["rowid"]
        updates = []
        for r in rows:
            pid = r["id"]
            title = r["title"] or ""
            raw_cat = r["category"] or ""
            current_brand = r["brand"]

            res = classifier.classify_rules(title, raw_category=raw_cat)
            if res:
                new_cat = res.category_name
                new_brand = res.brand or current_brand
                # Если товар - аксессуар/расходник, и его собственный бренд не определен, не оставляем бренд целевого смартфона!
                if res.item_kind in ("accessory", "consumable") and (res.brand is None) and current_brand in ("Apple", "Samsung", "Xiaomi", "Huawei", "Honor"):
                    new_brand = None
                new_model = res.model
                new_kind = res.item_kind
            else:
                new_cat = raw_cat
                new_brand = current_brand
                new_model = None
                new_kind = "device"

            # Локальный ИИ для Apple устройств без определившейся модели
            if new_brand == "Apple" and new_kind == "device" and not new_model:
                try:
                    ai_res = classifier.classify_with_local_ai(title, raw_category=raw_cat)
                    if ai_res and ai_res.model:
                        new_model = ai_res.model
                except Exception:
                    pass

            updates.append((new_cat, new_brand, new_model, new_kind, pid))
            category_stats[new_cat] = category_stats.get(new_cat, 0) + 1
            if new_brand:
                brand_stats[new_brand] = brand_stats.get(new_brand, 0) + 1
            if new_model:
                model_stats[new_model] = model_stats.get(new_model, 0) + 1
            kind_stats[new_kind] = kind_stats.get(new_kind, 0) + 1

        conn.executemany(
            "UPDATE canonical_products SET category = ?, brand = ?, model = ?, item_kind = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        updated_count += len(updates)
        elapsed = time.time() - t0
        speed = updated_count / elapsed if elapsed > 0 else 0
        logger.info(f"Progress: {updated_count}/{total_candidates} ({updated_count*100.0/total_candidates:.1f}%) - {speed:.0f} items/sec")

    logger.info(f"Classification completed! Total updated: {updated_count} in {time.time() - t0:.2f}s")

    if rebuild_fts:
        logger.info("Rebuilding canonical_products_fts full-text index...")
        try:
            conn.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('rebuild')")
            conn.commit()
            logger.info("FTS index rebuild successful!")
        except Exception as e:
            logger.warning(f"FTS rebuild warning: {e}")

    # Print summary statistics
    print("\n" + "=" * 50)
    print("ITEM KINDS DISTRIBUTION:")
    print("=" * 50)
    for k, cnt in kind_stats.items():
        print(f"  {k:<30}: {cnt:>6} шт.")

    print("\n" + "=" * 50)
    print("TOP CATEGORIES AFTER CLASSIFICATION:")
    print("=" * 50)
    sorted_cats = sorted(category_stats.items(), key=lambda x: -x[1])[:15]
    for cat, cnt in sorted_cats:
        print(f"  {cat:<30}: {cnt:>6} шт.")

    print("\n" + "=" * 50)
    print("TOP BRANDS EXTRACTED:")
    print("=" * 50)
    sorted_brands = sorted(brand_stats.items(), key=lambda x: -x[1])[:15]
    for br, cnt in sorted_brands:
        print(f"  {br:<30}: {cnt:>6} шт.")

    print("\n" + "=" * 50)
    print("TOP CLEAN MODELS EXTRACTED:")
    print("=" * 50)
    sorted_models = sorted(model_stats.items(), key=lambda x: -x[1])[:20]
    for m, cnt in sorted_models:
        print(f"  {m:<30}: {cnt:>6} шт.")
    print("=" * 50 + "\n")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Catalog Classification and Brand Extraction")
    parser.add_argument("--db", default="/app/data/prices.db", help="Path to prices.db SQLite database")
    parser.add_argument("--batch-size", type=int, default=2500, help="Batch size for updates")
    parser.add_argument("--force-all", action="store_true", help="Reclassify all products, not just unclassified")
    parser.add_argument("--skip-fts", action="store_true", help="Skip rebuilding canonical_products_fts index")
    args = parser.parse_args()

    backfill_classification(
        db_path=args.db,
        batch_size=args.batch_size,
        force_all=args.force_all,
        rebuild_fts=not args.skip_fts,
    )
