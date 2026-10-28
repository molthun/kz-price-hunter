from tests import test_support  # noqa: F401
import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import database


class WriteTxContentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db_path = self.data_dir / "prices.db"
        self.patches = [
            patch.object(config, "DATA_DIR", self.data_dir),
            patch.object(config, "DB_PATH", self.db_path),
            patch.object(database, "DB_PATH", self.db_path),
        ]
        for p in self.patches:
            p.start()
        database.init_db()
        if hasattr(database, "reset_write_tx_stats"):
            database.reset_write_tx_stats()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_write_tx_instrumentation_records_stats_and_slow_tx(self):
        with patch.object(database, "SLOW_WRITE_TX_SECONDS", 0.01):
            with database.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES ('k1', 'v1')")
                time.sleep(0.02)
                conn.commit()

        stats = database.get_write_tx_stats()
        self.assertGreaterEqual(stats["max_hold_ms"], 15.0)
        self.assertEqual(stats["slow_count"], 1)
        self.assertEqual(len(stats["recent_slow"]), 1)
        self.assertEqual(
            stats["recent_slow"][0]["op"],
            "test_write_tx_instrumentation_records_stats_and_slow_tx",
        )

    def test_fts_triggers_skip_unchanged_price_updates_and_fire_on_title_change(self):
        item = {
            "id": "shopkz_100@astana",
            "shop": "Белый Ветер",
            "shop_key": "shopkz",
            "city": "Астана",
            "title": "Смартфон Apple iPhone 16 128GB Black",
            "category": "Смартфоны",
            "url": "https://shop.kz/offer/100",
            "price": 500000,
        }
        database.save_or_update_products_batch([item])

        with database.get_connection() as conn:
            fts_segments_before = conn.execute("SELECT COUNT(*) FROM products_fts_data").fetchone()[0]
            v2_fts_segments_before = conn.execute("SELECT COUNT(*) FROM canonical_products_fts_data").fetchone()[0]

            item_same_text = dict(item, price=480000)
            database._save_products_chunk(conn, [item_same_text], "2026-10-28T12:00:00+00:00", time.time())
            conn.commit()

            fts_segments_after_price = conn.execute("SELECT COUNT(*) FROM products_fts_data").fetchone()[0]
            v2_fts_segments_after_price = conn.execute("SELECT COUNT(*) FROM canonical_products_fts_data").fetchone()[0]
            self.assertEqual(
                fts_segments_after_price,
                fts_segments_before,
                "products_fts should not be rewritten when only price changes",
            )
            self.assertEqual(
                v2_fts_segments_after_price,
                v2_fts_segments_before,
                "canonical_products_fts should not be rewritten when only price changes",
            )

            # При изменении названия триггеры обязаны сработать и обновить FTS5
            item_new_title = dict(
                item,
                title="Смартфон Apple iPhone 16 Titanium Edition",
                canonical_key="apple:iphone 16:128gb",
                price=650000,
            )
            database._save_products_chunk(conn, [item_new_title], "2026-10-28T12:01:00+00:00", time.time() + 1)
            conn.commit()

            fts_segments_after_title = conn.execute("SELECT COUNT(*) FROM products_fts_data").fetchone()[0]
            v2_fts_segments_after_title = conn.execute("SELECT COUNT(*) FROM canonical_products_fts_data").fetchone()[0]
            self.assertGreater(fts_segments_after_title, fts_segments_after_price)
            self.assertGreater(v2_fts_segments_after_title, v2_fts_segments_after_price)
            found = conn.execute(
                "SELECT rowid FROM products_fts WHERE products_fts MATCH 'Titanium'"
            ).fetchall()
            self.assertEqual(len(found), 1)

    def test_reconcile_source_uses_index_and_avoids_redundant_rewrites(self):
        items = [
            {
                "id": f"shopkz_{i}@astana",
                "shop": "Белый Ветер",
                "shop_key": "shopkz",
                "city": "Астана",
                "title": f"Товар {i}",
                "category": "Тест",
                "url": f"https://shop.kz/p/{i}",
                "price": 10000 + i,
            }
            for i in range(50)
        ]
        database.save_or_update_products_batch(items)
        ids = [it["id"] for it in items]
        database.reconcile_source("shopkz", "https://shop.kz/cat", items, complete=True)

        with database.get_connection() as conn:
            plan = conn.execute(
                "EXPLAIN QUERY PLAN SELECT product_id FROM product_sources "
                "WHERE shop_key = ? AND source_url = ? AND active = 1",
                ("shopkz", "https://shop.kz/cat"),
            ).fetchall()
            plan_detail = " ".join(str(tuple(r)) for r in plan)
            self.assertIn("idx_product_sources_shop_url", plan_detail)

            before_changes = conn.total_changes
            retired = database._reconcile_source_conn(
                conn, "shopkz", "https://shop.kz/cat", ids, complete=True, observed_seq=time.time() + 10
            )
            conn.commit()
            delta_changes = conn.total_changes - before_changes
            self.assertEqual(retired, [])
            # При неизменном списке активных товаров обновляется только source_observed (1 строка), а не все 50x2
            self.assertLessEqual(delta_changes, 2)

            # При исчезновении одного товара снимается ровно он
            retired2 = database._reconcile_source_conn(
                conn, "shopkz", "https://shop.kz/cat", ids[:-1], complete=True, observed_seq=time.time() + 20
            )
            conn.commit()
            self.assertEqual(retired2, [ids[-1]])

    def test_apply_retire_transition_does_not_call_watched_offers_when_no_active_watches(self):
        with database.get_connection() as conn:
            database._queue_watch_refresh_conn(conn, ["shopkz_1@astana", "shopkz_2@astana"])
            conn.commit()

        with patch.object(database, "watched_offers", wraps=database.watched_offers) as spy:
            database.refresh_retired_watches(["shopkz_1@astana", "shopkz_2@astana"])
            # В _apply_retire_transition при 0 активных наблюдений watched_offers не вызывается (вызывается только в фазе current)
            self.assertEqual(spy.call_count, 1)

    def test_keep_queue_claim_skips_redundant_renew_after_chunk_and_renews_when_idle(self):
        from web import server

        class FakeQueue:
            claim_seconds = 3.0

            def __init__(self):
                self.renews = 0

            async def renew(self, item):
                self.renews += 1
                return True

        real_sleep = asyncio.sleep

        async def fast_sleep(_delay):
            await real_sleep(0.01)

        async def scenario():
            q = FakeQueue()
            with patch.object(server.asyncio, "sleep", side_effect=fast_sleep):
                # Свежее продление от spool_apply_chunk — _keep_queue_claim пропускает холостой вызов
                item = {"task_id": "t1", "_last_renewed_at": time.monotonic() + 10.0}
                task = asyncio.create_task(server._keep_queue_claim(q, item))
                await real_sleep(0.03)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                self.assertEqual(q.renews, 0)

                # Давнее продление (например, во время долгого derived-этапа) — _keep_queue_claim вызывает renew
                item_stale = {"task_id": "t2", "_last_renewed_at": time.monotonic() - 10.0}
                task2 = asyncio.create_task(server._keep_queue_claim(q, item_stale))
                await real_sleep(0.03)
                task2.cancel()
                try:
                    await task2
                except asyncio.CancelledError:
                    pass
                self.assertGreaterEqual(q.renews, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
