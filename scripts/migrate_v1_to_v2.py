#!/usr/bin/env python3
"""
Backfill / Migration script for KZ Price Hunter 2.0 (Search Platform).
Migrates legacy 'products' and 'price_observations' to:
- sellers
- channels
- canonical_products
- offers
- offer_price_history

Safe and idempotent: uses INSERT ... ON CONFLICT / REPLACE.
Designed to run on a database snapshot created via SQLite Backup API.
"""

import argparse
import logging
import os
import sqlite3
import sys
import time
from typing import Dict, Tuple

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.adapter import sync_legacy_product_to_v2
from repositories.schema_v2 import init_schema_v2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
# Тот же порог, что и в domain/adapter.py: пробный прогон обязан судить по тем же правилам
MAX_REASONABLE_PRICE = 10_000_000

logger = logging.getLogger("migrate_v1_to_v2")


def create_snapshot_copy(source_db_path: str, target_db_path: str) -> None:
    """
    Creates a consistent SQLite snapshot using the online backup API.
    Guarantees consistent state without locking active WAL writers.
    """
    logger.info("Creating consistent snapshot from %s to %s...", source_db_path, target_db_path)
    t0 = time.perf_counter()
    src = sqlite3.connect(source_db_path)
    dst = sqlite3.connect(target_db_path)
    try:
        src.backup(dst, pages=1000)
    finally:
        dst.close()
        src.close()
    dt = time.perf_counter() - t0
    logger.info("Snapshot created in %.2f s (size: %.2f MB)", dt, os.path.getsize(target_db_path) / (1024 * 1024))


def migrate_products_and_history(
    db_path: str,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> Dict[str, int]:
    """
    Executes idempotent migration from legacy tables to 2.0 domain tables.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    stats = {
        "products_migrated": 0,
        "products_skipped": 0,
        "observations_migrated": 0,
        "sellers_count": 0,
        "channels_count": 0,
        "canonical_products_count": 0,
        "offers_count": 0,
    }

    try:
        if not dry_run:
            logger.info("Ensuring Schema 2.0 DDL is present...")
            init_schema_v2(conn)
            conn.commit()

        # 1. Count products to migrate
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM products")
        total_products = cur.fetchone()[0]
        logger.info("Total products to migrate: %d", total_products)

        # 2. Batch migration of products
        offset = 0
        t0 = time.perf_counter()
        while offset < total_products:
            cur.execute(
                """
                SELECT id, shop, city, title, category, url, image_url, description,
                       current_price, old_price_on_site, canonical_key, updated_at
                FROM products
                ORDER BY rowid ASC
                LIMIT ? OFFSET ?
                """,
                (batch_size, offset),
            )
            rows = cur.fetchall()
            if not rows:
                break

            if not dry_run:
                conn.execute("BEGIN TRANSACTION")
                for r in rows:
                    p_dict = {
                        "id": r["id"],
                        "shop": r["shop"],
                        "city": r["city"],
                        "title": r["title"],
                        "category": r["category"],
                        "url": r["url"],
                        "image_url": r["image_url"],
                        "description": r["description"],
                        "price": r["current_price"],
                        "old_price_on_site": r["old_price_on_site"],
                        "canonical_key": r["canonical_key"],
                    }
                    success = sync_legacy_product_to_v2(conn, p_dict, now=r["updated_at"])
                    if success:
                        stats["products_migrated"] += 1
                    else:
                        stats["products_skipped"] += 1
                conn.commit()
            else:
                # Пробный прогон тоже должен показывать пропуски, иначе он обещает больше,
                # чем сделает настоящий (B5): решение о пропуске не зависит от записи в базу
                for r in rows:
                    price = r["current_price"]
                    if price is None or price <= 0 or price > MAX_REASONABLE_PRICE:
                        stats["products_skipped"] += 1
                    else:
                        stats["products_migrated"] += 1

            offset += len(rows)
            if offset % 5000 == 0 or offset >= total_products:
                logger.info(
                    "Migrated %d / %d products (%.1f%%)...",
                    offset,
                    total_products,
                    (offset / total_products) * 100 if total_products else 100,
                )

        # 3. Batch migration of historical price observations
        cur.execute("SELECT COUNT(*) FROM price_observations")
        total_obs = cur.fetchone()[0]
        logger.info("Total price observations to backfill: %d", total_obs)

        obs_offset = 0
        while obs_offset < total_obs:
            cur.execute(
                """
                SELECT product_id, price, old_price_on_site, observed_at
                FROM price_observations
                ORDER BY rowid ASC
                LIMIT ? OFFSET ?
                """,
                (batch_size * 5, obs_offset),
            )
            obs_rows = cur.fetchall()
            if not obs_rows:
                break

            if not dry_run:
                conn.execute("BEGIN TRANSACTION")
                obs_tuples = [
                    (f"off_{r['product_id']}", float(r["price"]), float(r["old_price_on_site"]) if r["old_price_on_site"] else None, r["observed_at"])
                    for r in obs_rows
                ]
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    obs_tuples,
                )
                conn.commit()

            stats["observations_migrated"] += len(obs_rows)
            obs_offset += len(obs_rows)
            if obs_offset % 25000 == 0 or obs_offset >= total_obs:
                logger.info(
                    "Migrated %d / %d price observations (%.1f%%)...",
                    obs_offset,
                    total_obs,
                    (obs_offset / total_obs) * 100 if total_obs else 100,
                )

        dt = time.perf_counter() - t0
        logger.info("Migration loop completed in %.2f s", dt)

        # Count final entities
        if not dry_run:
            cur.execute("SELECT COUNT(*) FROM sellers")
            stats["sellers_count"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM channels")
            stats["channels_count"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM canonical_products")
            stats["canonical_products_count"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM offers")
            stats["offers_count"] = cur.fetchone()[0]

    finally:
        conn.close()

    return stats


def verify_integrity(db_path: str) -> bool:
    """Run SQLite integrity check."""
    logger.info("Verifying database integrity on %s...", db_path)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA integrity_check")
        res = cur.fetchone()[0]
        logger.info("Integrity check result: %s", res)
        return res == "ok"
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="KZ Price Hunter 2.0 Backfill Migration")
    parser.add_argument("--db-path", default="prices.db", help="Path to database to migrate")
    parser.add_argument("--batch-size", type=int, default=1000, help="Batch size for product migration")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing")
    parser.add_argument("--create-snapshot-to", help="If specified, snapshot source DB to this path and migrate snapshot")
    args = parser.parse_args()

    target_path = args.db_path
    if args.create_snapshot_to:
        create_snapshot_copy(args.db_path, args.create_snapshot_to)
        target_path = args.create_snapshot_to

    stats = migrate_products_and_history(target_path, batch_size=args.batch_size, dry_run=args.dry_run)
    logger.info("Migration Summary: %s", stats)

    if not args.dry_run:
        ok = verify_integrity(target_path)
        if not ok:
            logger.error("Database integrity check failed!")
            sys.exit(1)
        logger.info("Migration and integrity check completed successfully!")


if __name__ == "__main__":
    main()
