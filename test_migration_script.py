"""
Tests for backfill migration script scripts/migrate_v1_to_v2.py.
"""

import test_support  # must be first
import os
import sqlite3
import tempfile
import unittest

import database
from scripts.migrate_v1_to_v2 import create_snapshot_copy, migrate_products_and_history, verify_integrity


class TestMigrationScript(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.src_db = os.path.join(self.tmpdir, "test_src.db")
        self.dst_db = os.path.join(self.tmpdir, "test_dst.db")

        # Initialize legacy DB and populate data
        conn = sqlite3.connect(self.src_db)
        cursor = conn.cursor()
        database._create_schema(cursor)
        conn.commit()

        # Seed products and observations
        sample_products = [
            ("prod_1", "DNS Казахстан", "Астана", "Ноутбук Lenovo IdeaPad 3", "laptops", "https://dns.kz/p1", 250000, 270000, "lenovo:ideapad:3"),
            ("prod_2", "4mobile", "Алматы", "Смартфон Apple iPhone 15 Pro", "smartphones", "https://4m.kz/p2", 550000, 580000, "apple:iphone:15:pro"),
            ("prod_3", "Мечта", "Астана", "Телевизор Samsung 55 4K", "tvs", "https://mechta.kz/p3", 320000, 0, "samsung:tv:55:4k"),
        ]
        for pid, shop, city, title, cat, url, price, old_p, ckey in sample_products:
            cursor.execute(
                """
                INSERT INTO products (id, shop, city, title, category, url, current_price, old_price_on_site, canonical_key, first_seen_price, min_price, max_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '2026-09-20T10:00:00Z', '2026-09-20T10:00:00Z')
                """,
                (pid, shop, city, title, cat, url, price, old_p, ckey, price, price, price),
            )
            cursor.execute(
                """
                INSERT INTO price_observations (product_id, price, old_price_on_site, observed_at)
                VALUES (?, ?, ?, '2026-09-20T10:00:00Z')
                """,
                (pid, price, old_p),
            )

        conn.commit()
        conn.close()

    def test_backup_and_migration(self):
        # 1. Test snapshot creation via Backup API
        create_snapshot_copy(self.src_db, self.dst_db)
        self.assertTrue(os.path.exists(self.dst_db))
        self.assertTrue(verify_integrity(self.dst_db))

        # 2. Test dry-run mode
        dry_stats = migrate_products_and_history(self.dst_db, dry_run=True)
        self.assertEqual(dry_stats["products_migrated"], 3)
        self.assertEqual(dry_stats["observations_migrated"], 3)

        # 3. Test actual migration
        stats = migrate_products_and_history(self.dst_db, batch_size=2, dry_run=False)
        self.assertEqual(stats["products_migrated"], 3)
        self.assertEqual(stats["products_skipped"], 0)
        self.assertEqual(stats["observations_migrated"], 3)
        self.assertEqual(stats["offers_count"], 3)
        self.assertEqual(stats["canonical_products_count"], 3)
        self.assertTrue(stats["sellers_count"] >= 3)
        self.assertTrue(stats["channels_count"] >= 3)

        # 4. Verify contents in destination database
        conn = sqlite3.connect(self.dst_db)
        cur = conn.cursor()
        cur.execute("SELECT id, price, city FROM offers ORDER BY id")
        offers = cur.fetchall()
        self.assertEqual(len(offers), 3)
        self.assertEqual(offers[0][0], "off_prod_1")
        self.assertEqual(offers[0][1], 250000.0)

        cur.execute("SELECT COUNT(*) FROM offer_price_history")
        hist_count = cur.fetchone()[0]
        self.assertEqual(hist_count, 3)

        # 5. Idempotent re-run
        re_stats = migrate_products_and_history(self.dst_db, batch_size=2, dry_run=False)
        self.assertEqual(re_stats["offers_count"], 3)
        conn.close()

        self.assertTrue(verify_integrity(self.dst_db))


if __name__ == "__main__":
    unittest.main()
