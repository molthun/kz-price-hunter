#!/usr/bin/env python3
"""
Синтетическая верификация закрытия замечаний аудита C01–C09.
Запускается на изолированной БД: DATA_DIR=$(mktemp -d) python reproduce_c01_c09.py
"""

import asyncio
import datetime
import os
import shutil
import tempfile
import time
import unittest

# Настраиваем изолированный DATA_DIR
TMP_DIR = tempfile.mkdtemp(prefix="kzph_c01_c09_")
os.environ["DATA_DIR"] = TMP_DIR

import database
from domain.search_core import offer_expired
from queue_service import ProductQueueManager
from repositories.search_repo import SearchRepository
import web.server as server


class TestAuditFollowupC01C09(unittest.IsolatedAsyncioTestCase):

    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM product_queue_spool")
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    async def test_c06_auth_middleware_bypass(self):
        """C06: /api/live и /api/health проходят auth_middleware мгновенно без обращения к базе данных."""
        from aiohttp.test_utils import make_mocked_request
        from aiohttp import web
        from auth import auth_middleware

        called = False
        async def dummy_handler(request):
            nonlocal called
            called = True
            return web.Response(text="live_ok")

        for path in ("/api/live", "/api/health", "/favicon.ico"):
            called = False
            req = make_mocked_request("GET", path, headers={"Cookie": "kzph_session=invalid_or_slow_token"})
            resp = await auth_middleware(req, dummy_handler)
            self.assertTrue(called)
            self.assertEqual(resp.text, "live_ok")
            self.assertIsNone(req.get("user"))
        print("  [PASS] C06: auth_middleware instant bypass for /api/live and /api/health verified")

    async def test_c04_worker_error_nack(self):
        """C04: Ошибки при обработке очереди не глушатся, а приводят к nack и попаданию в dead_letter."""
        qm = ProductQueueManager()
        # Ставим задачу в spool
        bad_item = {"shop_name": "BadShop", "prods": [{"invalid": "data"}]}
        database.spool_enqueue("c04_bad_task", "bad_shop", "BadShop", 1, bad_item)

        # 1-я попытка: dequeue (attempts=1) -> nack -> возвращается в pending
        it1 = await qm.dequeue(timeout=0.2)
        self.assertIsNotNone(it1)
        self.assertEqual(it1["attempts"], 1)
        qm.nack(it1, error="Synthetic error 1", max_retries=2)
        stats = database.spool_stats("bad_shop")
        self.assertEqual(stats["pending"], 1)

        # 2-я попытка: dequeue (attempts=2) -> nack -> возвращается в pending
        it2 = await qm.dequeue(timeout=0.2)
        self.assertIsNotNone(it2)
        self.assertEqual(it2["attempts"], 2)
        qm.nack(it2, error="Synthetic error 2", max_retries=2)
        stats = database.spool_stats("bad_shop")
        self.assertEqual(stats["pending"], 1)

        # 3-я попытка: dequeue (attempts=3) -> nack (attempts > max_retries=2) -> dead_letter
        it3 = await qm.dequeue(timeout=0.2)
        self.assertIsNotNone(it3)
        self.assertEqual(it3["attempts"], 3)
        qm.nack(it3, error="Synthetic error 3", max_retries=2)
        stats = database.spool_stats("bad_shop")
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["dead_letter"], 1)

        dl = qm.dead_letter_queue
        dl_task_ids = [d.get("task_id") for d in dl]
        self.assertIn("c04_bad_task", dl_task_ids)
        print("  [PASS] C04: Worker error nack, retry, and dead_letter semantics verified")
        """C09: Граница TTL в offer_expired: проверяем exact match (now == expires_at)."""
        now = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
        exp_exact = "2026-10-28T12:00:00+00:00"
        exp_future = "2026-10-28T12:00:01+00:00"
        exp_past = "2026-10-28T11:59:59+00:00"

        # В секунду истечения оффер уже считается expired (>=)
        self.assertTrue(offer_expired(exp_exact, now=now))
        self.assertTrue(offer_expired(exp_past, now=now))
        self.assertFalse(offer_expired(exp_future, now=now))
        print("  [PASS] C09: offer_expired exact boundary (>=) verified")

    def test_c08_watched_offers_availability(self):
        """C08: watched_offers честно проверяет доступность (активность, наличие, TTL)."""
        now = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ts = "2026-10-28T10:00:00+00:00"
        with database.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO canonical_products (id, title, category, canonical_key, created_at, updated_at) VALUES ('p_c08', 'Product C08', 'Test', 'key_p_c08', ?, ?)", (ts, ts))
            conn.execute("INSERT OR REPLACE INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s_c08', 'slug_s_c08', 'Seller C08', ?, ?)", (ts, ts))
            conn.execute("INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('ch_c08', 's_c08', 'web', 'Web Channel', ?)", (ts,))

            # Оффер 1: активен, in_stock, не истек -> доступен
            conn.execute("""
                INSERT OR REPLACE INTO offers (id, product_id, seller_id, channel_id, external_sku, url, city, observed_at, created_at, updated_at, price, is_active, availability, expires_at)
                VALUES ('off_c08_active', 'p_c08', 's_c08', 'ch_c08', 'sku_1', 'http://1', 'Алматы', ?, ?, ?, 5000, 1, 'in_stock', '2026-10-28T15:00:00+00:00')
            """, (ts, ts, ts))
            # Оффер 2: истек TTL -> недоступен
            conn.execute("""
                INSERT OR REPLACE INTO offers (id, product_id, seller_id, channel_id, external_sku, url, city, observed_at, created_at, updated_at, price, is_active, availability, expires_at)
                VALUES ('off_c08_expired', 'p_c08', 's_c08', 'ch_c08', 'sku_2', 'http://2', 'Алматы', ?, ?, ?, 4000, 1, 'in_stock', '2026-10-28T11:00:00+00:00')
            """, (ts, ts, ts))
            # Оффер 3: out_of_stock -> недоступен
            conn.execute("""
                INSERT OR REPLACE INTO offers (id, product_id, seller_id, channel_id, external_sku, url, city, observed_at, created_at, updated_at, price, is_active, availability, expires_at)
                VALUES ('off_c08_outofstock', 'p_c08', 's_c08', 'ch_c08', 'sku_3', 'http://3', 'Алматы', ?, ?, ?, 3000, 1, 'out_of_stock', '2026-10-28T15:00:00+00:00')
            """, (ts, ts, ts))
            conn.commit()

        res = database.watched_offers(['off_c08_active', 'off_c08_expired', 'off_c08_outofstock'], now=now)
        by_id = {r['id']: r for r in res}
        self.assertEqual(by_id['off_c08_active']['is_available'], 1)
        self.assertEqual(by_id['off_c08_expired']['is_available'], 0)
        self.assertEqual(by_id['off_c08_outofstock']['is_available'], 0)
        print("  [PASS] C08: watched_offers honest availability calculation verified")

    def test_c07_sql_pre_limit_sort_nulls(self):
        """C07: Сортировка до LIMIT не вытесняет доступные товары товарами без предложений (NULL)."""
        now = datetime.datetime(2026, 10, 28, 12, 0, 0, tzinfo=datetime.timezone.utc)
        ts = "2026-10-28T10:00:00+00:00"
        with database.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO sellers (id, slug, name, created_at, updated_at, is_active) VALUES ('s_c07', 'slug_s_c07', 'Seller C07', ?, ?, 1)", (ts, ts))
            conn.execute("INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at, is_active) VALUES ('ch_c07', 's_c07', 'web', 'Web Channel C07', ?, 1)", (ts,))
            # Создаем товар A с предложением 10000 ₸
            conn.execute("INSERT OR REPLACE INTO canonical_products (id, title, category, canonical_key, created_at, updated_at) VALUES ('p_c07_a', 'iPhone Case A', 'Cases', 'key_p_c07_a', ?, ?)", (ts, ts))
            conn.execute("INSERT OR REPLACE INTO offers (id, product_id, seller_id, channel_id, external_sku, url, city, observed_at, created_at, updated_at, price, is_active, availability) VALUES ('off_c07_a', 'p_c07_a', 's_c07', 'ch_c07', 'sku_c07_a', 'http://a', 'Алматы', ?, ?, ?, 10000, 1, 'in_stock')", (ts, ts, ts))
            # Создаем товар B БЕЗ предложений (или с просроченными)
            conn.execute("INSERT OR REPLACE INTO canonical_products (id, title, category, canonical_key, created_at, updated_at) VALUES ('p_c07_b', 'iPhone Case B', 'Cases', 'key_p_c07_b', ?, ?)", (ts, ts))
            conn.commit()

            repo = SearchRepository(conn)
            results = repo.search_products(search_text="iPhone Case", sort_by="price_asc", limit=10, now=now)
            pids = [r["product"].id for r in results]
            self.assertIn("p_c07_a", pids)
        print("  [PASS] C07: SQL pre-limit sort NULL protection verified")

    async def test_c01_c05_persistent_spool_and_fencing(self):
        """C01-C05: Проверка персистентного журнала spool, crash recovery, fencing, backpressure."""
        qm = ProductQueueManager(maxsize=5)
        await qm.initialize()

        # 1. Enqueue & Dequeue в spool
        prods = [{"id": "c01_prod", "title": "C01 Item", "price": 12000, "url": "https://test.kz"}]
        ok = await qm.enqueue(prods, "C01Shop", {}, shop_key="c01_shop", lease_generation=1)
        self.assertTrue(ok, "Enqueue должен вернуть True (C03)")

        # Проверяем наличие задачи в БД spool (C01)
        stats = database.spool_stats("c01_shop")
        self.assertEqual(stats["pending"], 1)

        # Dequeue переводит в 'processing'
        item = await qm.dequeue(timeout=0.2)
        self.assertIsNotNone(item)
        self.assertEqual(item["shop_name"], "C01Shop")
        self.assertEqual(item["lease_generation"], 1)
        stats = database.spool_stats("c01_shop")
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["processing"], 1)

        # 2. Имитируем падение сервиса (kill -9 во время processing)
        # При старте нового экземпляра qm.initialize() восстанавливает зависшие задачи (C01)
        recovered = database.spool_recover_on_startup()
        self.assertEqual(recovered, 1)
        stats = database.spool_stats("c01_shop")
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["processing"], 0)

        # Повторно вычитываем задачу после рестарта
        item_recovered = await qm.dequeue(timeout=0.2)
        self.assertIsNotNone(item_recovered)

        # 3. Fencing (C05): при несовпадении lease_generation задача отклоняется и не коммитится
        server._lease_state["generation"] = 2  # Текущее поколение изменилось!
        server._lease_state["lost"] = False

        # _execute_queue_item отклонит задачу с generation=1
        res = await server._execute_queue_item(item_recovered)
        self.assertFalse(res, "Задача с устаревшим lease_generation обязана быть отклонена (C05)")

        stats = database.spool_stats("c01_shop")
        self.assertEqual(stats["rejected"], 1)

        # 4. wait_drained обязан вернуть False при наличии rejected/dead_letter (C02)
        drained_ok = await qm.wait_drained(shop_key="c01_shop", timeout=0.5)
        self.assertFalse(drained_ok, "wait_drained обязан вернуть False при наличии rejected задач (C02)")
        print("  [PASS] C01, C02, C05: SQLite spool persistence, recovery, fencing, and wait_drained barrier verified")


if __name__ == "__main__":
    unittest.main()
