from tests import test_support  # noqa: F401  isolates DATA_DIR
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import scripts.migrate_to_k2 as mig


class MigrateToK2Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "test_prices.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("""
            CREATE TABLE title_canonical_cache (
                title_hash TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE title_fields (
                title_hash TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                fields_json TEXT NOT NULL,
                key_version TEXT NOT NULL,
                provider TEXT,
                model TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE TABLE title_articles (
                article TEXT NOT NULL,
                title_hash TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (article, title_hash)
            )
        """)
        self.conn.execute("""
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                shop TEXT NOT NULL,
                canonical_key TEXT
            )
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_find_titles_to_migrate_skips_k2(self):
        self.conn.execute("INSERT INTO title_canonical_cache VALUES ('h1', 'Item 1', 'old:key1', CURRENT_TIMESTAMP)")
        self.conn.execute("INSERT INTO title_canonical_cache VALUES ('h2', 'Item 2', 'k2:brand:mod2', CURRENT_TIMESTAMP)")
        self.conn.execute("INSERT INTO title_canonical_cache VALUES ('h3', 'Item 3', 'old:key3', CURRENT_TIMESTAMP)")
        self.conn.commit()

        items = mig.find_titles_to_migrate(self.conn)
        self.assertEqual(len(items), 2)
        self.assertEqual([t for t, _ in items], ["Item 1", "Item 3"])

        limited = mig.find_titles_to_migrate(self.conn, limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0][0], "Item 1")

    def test_check_pairs_comparison(self):
        # 3 товара: A и B склеивались в old_keys, но расходятся в new_keys; B и C склеиваются в new_keys
        old_keys = {"A": "gemini:iphone15", "B": "gemini:iphone15", "C": "gemini:other"}
        new_keys = {"A": "k2:apple:iphone15", "B": "k2:apple:iphone15plus", "C": "k2:apple:iphone15plus"}
        shops = {"A": ["shop1"], "B": ["shop2"], "C": ["shop3"]}

        comp = mig.check_pairs_comparison(old_keys, new_keys, shops)
        self.assertEqual(comp["old_pairs_count"], 1)  # (A, B)
        self.assertEqual(comp["new_pairs_count"], 1)  # (B, C)
        self.assertEqual(comp["retained_pairs_count"], 0)
        self.assertEqual(comp["dropped_pairs_count"], 1)
        self.assertEqual(comp["added_pairs_count"], 1)

    def test_run_migration_full_and_rescore(self):
        import hashlib
        t1 = "SSD 128GB Transcend TS128GMTE110S M2"
        t2 = "SSD 128GB Transcend MTE110S (TS128GMTE110S)"
        h1 = hashlib.sha256(t1.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(t2.encode("utf-8")).hexdigest()
        self.conn.execute("INSERT INTO title_canonical_cache VALUES (?, ?, 'transcend:mte110s:128gb', CURRENT_TIMESTAMP)", (h1, t1))
        self.conn.execute("INSERT INTO title_canonical_cache VALUES (?, ?, 'transcend:ts128gmte110s:128gb', CURRENT_TIMESTAMP)", (h2, t2))
        self.conn.execute("INSERT INTO products VALUES (1, ?, 'mechta', 'old1')", (t1,))
        self.conn.execute("INSERT INTO products VALUES (2, ?, 'sulpak', 'old2')", (t2,))
        self.conn.commit()

        mock_reply = {
            "items": [
                {"title": t1, "brand": "Transcend", "model": "TS128GMTE110S", "sku": None,
                 "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False},
                {"title": t2, "brand": "Transcend", "model": "MTE110S", "sku": "TS128GMTE110S",
                 "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False},
            ]
        }

        async def fake_call(prompt, base, model, key, timeout):
            return mock_reply

        with patch("ai_service._call_local_api", fake_call):
            res = asyncio.run(mig.run_migration(
                db_path=self.db_path,
                base="http://fake",
                model="qwen3-vl:8b-instruct",
                check_pairs=True,
            ))

        self.assertEqual(res["total_titles"], 2)
        self.assertEqual(res["with_k2_key"], 2)

        # Проверяем, что в БД обновились кэш и товары
        c_rows = dict(self.conn.execute("SELECT title, canonical_key FROM title_canonical_cache").fetchall())
        self.assertTrue(c_rows[t1].startswith("k2:transcend:"))
        # Благодаря link_by_article они должны получить одинаковый ключ!
        self.assertEqual(c_rows[t1], c_rows[t2])

        p_rows = dict(self.conn.execute("SELECT title, canonical_key FROM products").fetchall())
        self.assertEqual(p_rows[t1], c_rows[t1])
        self.assertEqual(p_rows[t2], c_rows[t2])

        # Проверяем, что в title_fields и title_articles сохранились записи
        tf_count = self.conn.execute("SELECT count(*) FROM title_fields").fetchone()[0]
        ta_count = self.conn.execute("SELECT count(*) FROM title_articles").fetchone()[0]
        self.assertEqual(tf_count, 2)
        self.assertGreaterEqual(ta_count, 1)

        # Проверяем rescore-only: не должен вызывать AI вообще
        with patch("ai_service._call_local_api", side_effect=RuntimeError("AI must not be called")):
            res2 = asyncio.run(mig.run_migration(
                db_path=self.db_path,
                base="http://fake",
                model="qwen3-vl:8b-instruct",
                rescore_only=True,
            ))
        self.assertEqual(res2["status"], "nothing_to_migrate")

    def test_dry_run_does_not_modify_db(self):
        import hashlib
        t = "SSD 128GB Transcend TS128GMTE110S"
        h = hashlib.sha256(t.encode("utf-8")).hexdigest()
        self.conn.execute("INSERT INTO title_canonical_cache VALUES (?, ?, 'old:key', CURRENT_TIMESTAMP)", (h, t))
        self.conn.commit()

        mock_reply = {
            "items": [
                {"title": t, "brand": "Transcend", "model": "TS128GMTE110S", "sku": None,
                 "variants": [{"value": 128, "unit": "ГБ"}], "unbranded": False}
            ]
        }

        with patch("ai_service._call_local_api", AsyncMock(return_value=mock_reply)):
            res = asyncio.run(mig.run_migration(
                db_path=self.db_path,
                base="http://fake",
                model="qwen3-vl:8b-instruct",
                dry_run=True,
            ))

        self.assertEqual(res["with_k2_key"], 1)
        # БД не должна измениться
        cur_key = self.conn.execute("SELECT canonical_key FROM title_canonical_cache WHERE title_hash = ?", (h,)).fetchone()[0]
        self.assertEqual(cur_key, "old:key")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM title_fields").fetchone()[0], 0)



if __name__ == "__main__":
    unittest.main()
