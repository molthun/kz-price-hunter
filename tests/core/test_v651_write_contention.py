from tests import test_support  # noqa: F401
"""Регрессии 6.5.1: блокировка записи под нагрузкой обхода (конвой GIL на проде 6.5.0).

Порция товаров писалась ~6 вызовами SQLite на товар; каждый отпускает GIL, и под потоками
разбора страниц писатель держал блокировку базы до минуты. Здесь закреплено: наборная запись
даёт то же состояние базы, что запись по одному товару; число операторов на порцию не растёт
с её размером; название канонического товара не переписывается чужим предложением; health
не стоит в очереди занятого пула записи; интервал переключения GIL настраивается.
"""
import asyncio
import concurrent.futures
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

import config
import database


NOW_1 = "2026-10-29T08:00:00+00:00"
NOW_2 = "2026-10-29T09:00:00+00:00"

TABLES = {
    "products": "SELECT id, shop, city, title, category, url, image_url, description, current_price, "
                "old_price_on_site, first_seen_price, min_price, max_price, canonical_key, is_active, "
                "created_at, updated_at, observed_seq FROM products ORDER BY id",
    "price_observations": "SELECT product_id, price, old_price_on_site, observed_at FROM price_observations "
                          "ORDER BY rowid",
    "sellers": "SELECT id, slug, name, domain, is_active, created_at, updated_at FROM sellers ORDER BY id",
    "seller_identities": "SELECT id, seller_id, identity_type, identity_value, source FROM seller_identities "
                         "ORDER BY id",
    "channels": "SELECT id, seller_id, channel_type, name FROM channels ORDER BY id",
    "canonical_products": "SELECT id, canonical_key, title, category, description, image_url, title_offer_id, "
                          "updated_at FROM canonical_products ORDER BY id",
    "offers": "SELECT id, product_id, seller_id, channel_id, external_sku, url, image_url, price, old_price, "
              "condition, availability, city, observed_at, is_active FROM offers ORDER BY id",
    "offer_price_history": "SELECT offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, "
                           "condition, city FROM offer_price_history ORDER BY offer_id, observed_at",
}


def product(pid, title, price, shop="Kaspi Магазин", **extra):
    item = {"id": pid, "shop": shop, "city": "Алматы", "title": title, "category": "Смартфоны",
            "url": f"https://example.kz/p/{pid}", "image_url": "", "price": price}
    item.update(extra)
    return item


class _DbCase(unittest.TestCase):
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

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def snapshot(self, path=None):
        conn = sqlite3.connect(path or self.db_path)
        try:
            # FTS обеих моделей обязан совпадать с таблицами-источниками
            conn.execute("INSERT INTO products_fts(products_fts) VALUES('integrity-check')")
            conn.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('integrity-check')")
            return {name: conn.execute(sql).fetchall() for name, sql in TABLES.items()}
        finally:
            conn.close()

    def write_chunk(self, chunk, now, seq, path=None):
        with patch.object(database, "DB_PATH", Path(path or self.db_path)):
            with database.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                count = database._save_products_chunk(conn, chunk, now, seq)
                conn.commit()
        return count

    def write_one_by_one(self, chunk, now, seq, path):
        with patch.object(database, "DB_PATH", Path(path)):
            with database.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                count = sum(database._save_products_chunk(conn, [p], now, seq) for p in chunk)
                conn.commit()
        return count


class BulkChunkEquivalenceTest(_DbCase):
    """Наборная запись порции даёт то же, что запись тех же товаров по одному подряд."""

    def seed(self):
        self.write_chunk([
            product("p_same", "Apple iPhone 16 128GB Black", 400000, description="старое описание"),
            product("p_price", "Samsung Galaxy S25 256GB", 350000, old_price_on_site=390000),
            product("p_newer", "Xiaomi 15 Pro 512GB", 300000),
            product("p_shared_a", "Apple iPhone 16 Pro 256GB Natural Titanium", 600000),
        ], NOW_1, 1000.0)
        # Товар с более свежим наблюдением (F01): порция с меткой 2000.0 его не трогает
        with database.get_connection() as conn:
            conn.execute("UPDATE products SET observed_seq = 9000000000.5 WHERE id = 'p_newer'")
            conn.commit()

    def chunk(self):
        return [
            product("p_same", "Apple iPhone 16 128GB Black", 400000, description=""),
            product("p_price", "Samsung Galaxy S25 256GB", 340000),
            product("p_newer", "Xiaomi 15 Pro 512GB", 1),
            product("p_new", "Google Pixel 9 128GB", 250000, description="новый"),
            # Повтор нового товара в той же порции: вторая запись видит первую
            product("p_new", "Google Pixel 9 128GB Obsidian", 245000, old_price_on_site=260000),
            # Повтор существующего: описание и ключ — последние непустые
            product("p_price", "Samsung Galaxy S25 256GB Blue", 345000, description="синий"),
            product("p_bad", "Недопустимая цена", 0),
            # Тот же канонический товар у другого магазина
            product("p_shared_b", "Apple iPhone 16 Pro 256GB Natural Titanium", 590000, shop="Sulpak"),
            product("p_used", "Apple iPhone 15 128GB", 200000, shop="Sulpak", condition="USED"),
        ]

    def test_bulk_equals_one_by_one(self):
        self.seed()
        reference = self.data_dir / "reference.db"
        shutil.copy(self.db_path, reference)

        bulk = self.write_chunk(self.chunk(), NOW_2, 2000.0)
        single = self.write_one_by_one(self.chunk(), NOW_2, 2000.0, reference)

        self.assertEqual(bulk, single)
        self.assertEqual(bulk, 7)  # p_newer (F01) и p_bad (цена) пропущены
        got, want = self.snapshot(), self.snapshot(reference)
        for table in TABLES:
            self.assertEqual(got[table], want[table], table)

    def test_bulk_keeps_f01_and_exact_observed_seq(self):
        self.seed()
        seq = time.time()  # полная точность метки: через JSON она бы потеряла знаки
        self.write_chunk(self.chunk(), NOW_2, seq)
        with database.get_connection() as conn:
            rows = dict(conn.execute("SELECT id, observed_seq FROM products").fetchall())
            newer_price = conn.execute("SELECT current_price FROM products WHERE id = 'p_newer'").fetchone()[0]
        self.assertEqual(rows["p_newer"], 9000000000.5)
        self.assertEqual(newer_price, 300000)
        self.assertEqual(rows["p_new"], seq)
        self.assertEqual(rows["p_same"], seq)
        # Сверка вытеснения смотрит на точную метку: свои товары задачи не считаются вытесненными
        with database.get_connection() as conn:
            superseded = conn.execute("SELECT count(*) FROM products WHERE observed_seq > ? AND id != 'p_newer'",
                                      (seq,)).fetchone()[0]
        self.assertEqual(superseded, 0)

    def test_duplicate_new_product_collapses_like_sequential_writes(self):
        self.write_chunk(self.chunk(), NOW_2, 2000.0)
        with database.get_connection() as conn:
            row = conn.execute("SELECT title, current_price, first_seen_price, min_price, max_price, "
                               "old_price_on_site, description FROM products WHERE id = 'p_new'").fetchone()
            observations = conn.execute("SELECT price FROM price_observations WHERE product_id = 'p_new' "
                                        "ORDER BY rowid").fetchall()
        self.assertEqual(tuple(row), ("Google Pixel 9 128GB Obsidian", 245000, 250000, 245000, 250000,
                                      260000, "новый"))
        self.assertEqual([r[0] for r in observations], [250000, 245000])


class StatementCountTest(_DbCase):
    """Число вызовов SQLite на порцию не зависит от её размера: каждый вызов отпускает GIL."""

    def count_statements(self, chunk, now, seq):
        """Вызовы execute/executemany верхнего уровня; операторы триггеров идут внутри одного шага."""
        calls = []
        real = database.ClosingConnection._run_tracked

        def counting(conn, fn, sql, *args):
            calls.append(getattr(fn, "__name__", ""))
            return real(conn, fn, sql, *args)

        with patch.object(database.ClosingConnection, "_run_tracked", counting):
            with database.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                database._save_products_chunk(conn, chunk, now, seq)
                conn.commit()
        self.assertNotIn("executemany", calls, "executemany шагает по строке — GIL отпускается на каждой")
        return len(calls)

    def test_chunk_statements_do_not_grow_with_chunk_size(self):
        shops = ["Kaspi Магазин", "Sulpak", "Technodom", "Белый Ветер"]
        first = [product(f"p{i}", f"Смартфон Модель {i} 128GB", 100000 + i, shop=shops[i % 4])
                 for i in range(100)]
        new_count = self.count_statements(first, NOW_1, 1000.0)
        changed = [dict(p, price=p["price"] + 1) for p in first]
        update_count = self.count_statements(changed, NOW_2, 2000.0)
        # Было ~600 операторов на 100 товаров; теперь — единицы на порцию
        self.assertLessEqual(new_count, 15)
        self.assertLessEqual(update_count, 15)
        small = self.count_statements([dict(p, price=p["price"] + 2) for p in first[:5]], NOW_2, 3000.0)
        self.assertEqual(small, update_count)

    def test_spool_chunk_prepares_outside_write_lock(self):
        """Разбор названий (canonical_key, продавец) идёт до BEGIN IMMEDIATE."""
        import model_matching
        receipt_products = [product(f"s{i}", f"Ноутбук Модель {i}", 200000 + i) for i in range(3)]
        database.spool_admit("t1", "kaspi", "Kaspi", None, {"prods": receipt_products})
        item = database.spool_claim("test-worker")
        in_tx = []
        real = model_matching.extract_canonical_key
        holder = {}

        def spy(title):
            conn = holder.get("conn")
            in_tx.append(bool(conn and conn.in_transaction))
            return real(title)

        real_get_connection = database.get_connection

        def tracking_connection():
            conn = real_get_connection()
            holder["conn"] = conn
            return conn

        with patch.object(model_matching, "extract_canonical_key", side_effect=spy), \
                patch.object(database, "get_connection", side_effect=tracking_connection):
            self.assertTrue(database.spool_apply_chunk(item["task_id"], item["claim_token"], 0, receipt_products))
        self.assertTrue(in_tx)
        self.assertFalse(any(in_tx), "canonical_key считается под блокировкой записи")

    def test_lost_claim_with_malformed_chunk_still_reports_lost_claim(self):
        database.spool_admit("t2", "kaspi", "Kaspi", None, {"prods": [product("m1", "Товар", 1000)]})
        database.spool_claim("test-worker")
        self.assertFalse(database.spool_apply_chunk("t2", "чужой-токен", 0, [{"id": "m1"}]))


class DualWriteFallbackTest(_DbCase):
    def test_one_failing_v2_item_does_not_drop_others(self):
        from domain.adapter import prepare_legacy_offer_v2
        bad = product("f_bad", "Товар со сбоем", 1000, shop="Sulpak")
        meta = prepare_legacy_offer_v2(bad)
        # Чужой продавец уже занял slug: вставка продавца f_bad нарушает UNIQUE(slug)
        with database.get_connection() as conn:
            conn.execute("INSERT INTO sellers (id, slug, name, is_active, created_at, updated_at) "
                         "VALUES ('seller_other', ?, 'Другой', 1, ?, ?)", (meta["seller_slug"], NOW_1, NOW_1))
            conn.commit()
        database.reset_dual_write_stats()
        chunk = [product("f_ok1", "Товар раз", 1000), bad, product("f_ok2", "Товар два", 2000)]
        self.assertEqual(self.write_chunk(chunk, NOW_1, 1000.0), 3)
        stats = database.get_dual_write_stats()
        self.assertEqual((stats["total"], stats["failures"]), (3, 1))
        with database.get_connection() as conn:
            offers = {r[0] for r in conn.execute("SELECT id FROM offers")}
            legacy = {r[0] for r in conn.execute("SELECT id FROM products")}
        self.assertEqual(offers, {"off_f_ok1", "off_f_ok2"})
        self.assertEqual(legacy, {"f_ok1", "f_bad", "f_ok2"})


class CanonicalTitleOwnerTest(_DbCase):
    def canonical(self):
        with database.get_connection() as conn:
            return conn.execute("SELECT title, category, title_offer_id FROM canonical_products").fetchall()

    def test_foreign_offer_does_not_rewrite_title_but_owner_does(self):
        self.write_chunk([product("c_a", "Apple iPhone 16 128GB Black", 400000, shop="Kaspi Магазин")],
                         NOW_1, 1000.0)
        self.write_chunk([product("c_b", "Apple iPhone 16 128GB Black (2024) RU", 395000, shop="Sulpak",
                                  category="Телефоны")], NOW_2, 2000.0)
        rows = self.canonical()
        self.assertEqual(len(rows), 1, "оба предложения должны делить канонический товар")
        self.assertEqual(tuple(rows[0]), ("Apple iPhone 16 128GB Black", "Смартфоны", "off_c_a"))
        self.write_chunk([product("c_a", "Apple iPhone 16 128GB Black eSIM", 399000, shop="Kaspi Магазин")],
                         NOW_2, 3000.0)
        self.assertEqual(tuple(self.canonical()[0]), ("Apple iPhone 16 128GB Black eSIM", "Смартфоны", "off_c_a"))

    def test_legacy_row_without_owner_is_claimed_by_next_offer(self):
        self.write_chunk([product("c_a", "Apple iPhone 16 128GB Black", 400000)], NOW_1, 1000.0)
        with database.get_connection() as conn:
            conn.execute("UPDATE canonical_products SET title_offer_id = NULL")
            conn.commit()
        self.write_chunk([product("c_b", "Apple iPhone 16 128GB Black Sulpak", 395000, shop="Sulpak")],
                         NOW_2, 2000.0)
        self.assertEqual(tuple(self.canonical()[0]), ("Apple iPhone 16 128GB Black Sulpak", "Смартфоны", "off_c_b"))


class TelemetryFlushStatementsTest(_DbCase):
    """Сброс телеметрии: события и выборка задержек пишутся оператором на пачку, а не шагом на строку."""

    def test_flush_uses_set_statements_and_keeps_samples(self):
        from telemetry import TelemetryService, SEVERITY_WARNING, COMPONENT_SYSTEM
        service = TelemetryService()
        calls = []
        real = database.ClosingConnection._run_tracked

        def counting(conn, fn, sql, *args):
            calls.append(getattr(fn, "__name__", ""))
            return real(conn, fn, sql, *args)

        for round_no in range(2):
            for i in range(60):
                service.record_event("audit_651", SEVERITY_WARNING, COMPONENT_SYSTEM, f"событие {round_no}-{i}",
                                     data={"i": i})
                service.record_http_metric("example.kz", "GET", 200, 10.0 + i / 7, shop="kaspi")
            calls.clear()
            with patch.object(database.ClosingConnection, "_run_tracked", counting):
                self.assertTrue(service.flush())
            self.assertNotIn("executemany", calls)
            self.assertLess(len(calls), 40)
        with database.get_connection() as conn:
            events = conn.execute("SELECT count(*) FROM telemetry_events WHERE type = 'audit_651'").fetchone()[0]
            samples = conn.execute("SELECT latency_ms FROM telemetry_http_samples WHERE host = 'example.kz' "
                                   "ORDER BY rowid LIMIT 3").fetchall()
            total = conn.execute("SELECT max(total_requests) FROM telemetry_http_aggregates "
                                 "WHERE host = 'example.kz'").fetchone()[0]
        self.assertEqual(events, 120)
        self.assertEqual(total, 120)
        self.assertTrue(samples)
        self.assertIsInstance(samples[0][0], float)


class GilSwitchIntervalTest(unittest.TestCase):
    def test_default_and_bounds(self):
        with patch.dict(os.environ, {"GIL_SWITCH_INTERVAL": ""}):
            self.assertEqual(config.gil_switch_interval(), config.GIL_SWITCH_INTERVAL_DEFAULT)
        for raw, want in (("0", config.GIL_SWITCH_INTERVAL_DEFAULT), ("мусор", config.GIL_SWITCH_INTERVAL_DEFAULT),
                          ("0,001", 0.001), ("1", config.GIL_SWITCH_INTERVAL_MAX),
                          ("0.000001", config.GIL_SWITCH_INTERVAL_MIN)):
            with patch.dict(os.environ, {"GIL_SWITCH_INTERVAL": raw}):
                self.assertEqual(config.gil_switch_interval(), want, raw)

    def test_apply_sets_interpreter_interval(self):
        before = sys.getswitchinterval()
        try:
            with patch.dict(os.environ, {"GIL_SWITCH_INTERVAL": "0.002"}):
                self.assertAlmostEqual(config.apply_gil_switch_interval(), 0.002, places=6)
            self.assertAlmostEqual(sys.getswitchinterval(), 0.002, places=6)
        finally:
            sys.setswitchinterval(before)

    def test_gui_entrypoint_applies_interval(self):
        import gui
        with patch("config.apply_gil_switch_interval", return_value=0.0005) as applied, \
                patch.object(gui, "create_app", side_effect=RuntimeError("stop")):
            with self.assertRaises(RuntimeError):
                gui.main()
        applied.assert_called_once()


class HealthOwnPoolTest(AioHTTPTestCase):
    async def get_application(self):
        from web import server
        app = server.create_app()
        app.cleanup_ctx.clear()
        return app

    async def test_health_answers_while_db_pool_is_saturated(self):
        from web import server
        from queue_service import product_queue
        release = threading.Event()
        loop = asyncio.get_running_loop()
        # Все 16 потоков kzph-db заняты, как при ожидании блокировки записи
        busy = [loop.run_in_executor(server._db_executor, release.wait, 30)
                for _ in range(server._db_executor._max_workers)]
        try:
            product_queue.is_worker_running = True
            started = time.monotonic()
            resp = await self.client.get("/api/health")
            body = await resp.json()
            self.assertEqual((resp.status, body["db"], body["queue"]), (200, "ok", "ok"))
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            product_queue.is_worker_running = False
            release.set()
            await asyncio.gather(*busy)


if __name__ == "__main__":
    unittest.main()
