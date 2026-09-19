"""Тесты для агрегатора акций и супер-скидок по магазинам Казахстана (get_store_deals и /api/deals)."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

import database
import web.server as server


class StoreDealsDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_deals.db"
        self.patcher = patch.object(database, "DB_PATH", self.db_path)
        self.patcher.start()
        database.init_db()

        # Заполняем тестовые данные
        with database.get_connection() as conn:
            cur = conn.cursor()
            # 1. Обычный товар без скидки
            cur.execute("""
                INSERT INTO products (id, shop, title, category, city, url, current_price, old_price_on_site, first_seen_price, min_price, max_price, is_active, canonical_key)
                VALUES ('p1', 'Kaspi', 'iPhone 15 128GB', 'smartphones', 'Алматы', 'http://k/1', 400000, 400000, 400000, 400000, 400000, 1, 'apple iphone 15 128gb')
            """)

            # 2. Товар со скидкой магазина (old_price_on_site > current_price)
            # 100 000 -> 70 000 (скидка 30 000, 30%)
            cur.execute("""
                INSERT INTO products (id, shop, title, category, city, url, current_price, old_price_on_site, first_seen_price, min_price, max_price, is_active, canonical_key)
                VALUES ('p2', 'Arbuz', 'Кофе зерновой Lavazza 1кг', 'grocery', 'Алматы', 'http://a/2', 70000, 100000, 100000, 70000, 100000, 1, 'lavazza 1kg')
            """)

            # 3. Товар в категории actions с first_seen_price > current_price
            # 50 000 -> 35 000 (скидка 15 000, 30%)
            cur.execute("""
                INSERT INTO products (id, shop, title, category, city, url, current_price, old_price_on_site, first_seen_price, min_price, max_price, is_active, canonical_key)
                VALUES ('p3', 'DNS', 'Наушники Sony WH-1000XM4', 'actions', 'Астана', 'http://d/3', 35000, NULL, 50000, 35000, 50000, 1, 'sony wh1000xm4')
            """)

            # 4. Неактивный товар со скидкой (не должен попадать)
            cur.execute("""
                INSERT INTO products (id, shop, title, category, city, url, current_price, old_price_on_site, first_seen_price, min_price, max_price, is_active, canonical_key)
                VALUES ('p4', 'MasterOK', 'Перфоратор Makita', 'diy', 'Алматы', 'http://m/4', 20000, 40000, 40000, 20000, 40000, 0, 'makita perf')
            """)

            # 5. Алерт арбитража для p1 (p1 дешевле в Технодоме)
            # В Технодоме 350 000, в Kaspi 400 000 (выгода 50 000, 13%)
            cur.execute("""
                INSERT INTO alerts (id, shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop, is_dismissed)
                VALUES (1, 'Технодом', 'Алматы', 'p1', 'MARKET_ARBITRAGE', 400000, 350000, 13, 50000, 'Kaspi', 0)
            """)

            # 6. Скрытый алерт (is_dismissed = 1, не должен попадать)
            cur.execute("""
                INSERT INTO alerts (id, shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop, is_dismissed)
                VALUES (2, 'Zeta', 'Алматы', 'p2', 'SUPER_DISCOUNT', 100000, 50000, 50, 50000, NULL, 1)
            """)
            conn.commit()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_get_store_deals_default(self):
        result = database.get_store_deals()
        deals = result["deals"]
        # Должны попасть: p2 (Arbuz), p3 (DNS), алерт 1 (Технодом) = 3 сделки
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(deals), 3)

        # Проверяем shops_summary
        summary = {s["shop"]: s["count"] for s in result["shops_summary"]}
        self.assertEqual(summary.get("Все"), 3)
        self.assertEqual(summary.get("Arbuz"), 1)
        self.assertEqual(summary.get("DNS"), 1)
        self.assertEqual(summary.get("Технодом"), 1)

    def test_get_store_deals_filter_by_shop(self):
        result = database.get_store_deals(shop="Arbuz")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["deals"][0]["shop"], "Arbuz")
        self.assertEqual(result["deals"][0]["title"], "Кофе зерновой Lavazza 1кг")

        # При фильтрации по Arbuz shops_summary всё равно показывает все магазины
        summary = {s["shop"]: s["count"] for s in result["shops_summary"]}
        self.assertEqual(summary.get("Все"), 3)
        self.assertEqual(summary.get("DNS"), 1)

    def test_get_store_deals_filter_by_city(self):
        result = database.get_store_deals(city="Астана")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["deals"][0]["shop"], "DNS")

    def test_get_store_deals_filter_by_search_and_category(self):
        result_cat = database.get_store_deals(category="grocery")
        self.assertEqual(result_cat["total"], 1)
        self.assertEqual(result_cat["deals"][0]["shop"], "Arbuz")

        result_search = database.get_store_deals(search="Sony")
        self.assertEqual(result_search["total"], 1)
        self.assertEqual(result_search["deals"][0]["shop"], "DNS")

    def test_get_store_deals_filter_by_type(self):
        # 'arbitrage' -> только алерт 1 (Технодом)
        res_arb = database.get_store_deals(deal_type="arbitrage")
        self.assertEqual(res_arb["total"], 1)
        self.assertEqual(res_arb["deals"][0]["alert_type"], "MARKET_ARBITRAGE")

        # 'super' -> p2 (30%) и p3 (30%)
        res_super = database.get_store_deals(deal_type="super")
        self.assertEqual(res_super["total"], 2)

    def test_get_store_deals_sort_by(self):
        # savings_desc: алерт 1 (50000) > p2 (30000) > p3 (15000)
        res_sav = database.get_store_deals(sort_by="savings_desc")
        self.assertEqual(res_sav["deals"][0]["savings_kzt"], 50000)
        self.assertEqual(res_sav["deals"][1]["savings_kzt"], 30000)
        self.assertEqual(res_sav["deals"][2]["savings_kzt"], 15000)

        # price_asc: p3 (35000) < p2 (70000) < алерт 1 (350000)
        res_price = database.get_store_deals(sort_by="price_asc")
        self.assertEqual(res_price["deals"][0]["new_price"], 35000)
        self.assertEqual(res_price["deals"][1]["new_price"], 70000)
        self.assertEqual(res_price["deals"][2]["new_price"], 350000)

    def test_get_store_deals_dedup(self):
        # Добавляем алерт на тот же товар в Arbuz с меньшей выгодой (например 10 000)
        # В products выгода 30 000, поэтому должен остаться вариант с 30 000
        with database.get_connection() as conn:
            conn.execute("""
                INSERT INTO alerts (id, shop, city, product_id, alert_type, old_price, new_price, discount_pct, savings_kzt, competitor_shop, is_dismissed)
                VALUES (3, 'Arbuz', 'Алматы', 'p2', 'SUPER_DISCOUNT', 80000, 70000, 12, 10000, NULL, 0)
            """)
            conn.commit()

        result = database.get_store_deals(shop="Arbuz")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["deals"][0]["savings_kzt"], 30000)


class StoreDealsApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_api_deals.db"
        self.patcher = patch.object(database, "DB_PATH", self.db_path)
        self.patcher.start()
        database.init_db()

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO products (id, shop, title, category, city, url, current_price, old_price_on_site, first_seen_price, min_price, max_price, is_active, canonical_key)
                VALUES ('p10', 'Kaspi', 'Galaxy S24', 'smartphones', 'Алматы', 'http://k/10', 300000, 400000, 400000, 300000, 400000, 1, 'samsung galaxy s24')
            """)
            conn.commit()

        app = server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patcher.stop()
        self.tmp.cleanup()

    async def test_api_deals_endpoint(self):
        resp = await self.client.get("/api/deals?shop=Kaspi")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("deals", data)
        self.assertIn("shops_summary", data)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["deals"][0]["shop"], "Kaspi")
        self.assertEqual(data["deals"][0]["discount_pct"], 25)


if __name__ == "__main__":
    unittest.main()
