"""
Tests for AI Search Assistant 2.0 (Search Platform, Stage 22).
Verifies:
1. Intent and plan extraction (SearchAssistantPlan): use-case, budget, installment intent, condition.
2. SearchAssistant workflow: querying SearchCore 2.0, installment detection, cross-channel comparisons.
3. Grounded explanation generation strictly from database items (no hallucinations).
4. HTTP API POST /api/v2/ai/assistant validation and responses.
"""

from tests import test_support  # must be first
import datetime
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock
from aiohttp.test_utils import make_mocked_request

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.search_assistant import (
    SearchAssistant,
    SearchAssistantPlan,
    SearchAssistantResponse,
)
from domain.search_core import SearchCore
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)
from web.server import ai_search_assistant_v2_handler


class TestAssistantPlanBuilding(unittest.TestCase):
    def setUp(self):
        conn = sqlite3.connect(":memory:")
        init_schema_v2(conn)
        self.assistant = SearchAssistant(SearchCore(SearchRepository(conn)))
        self.conn = conn

    def tearDown(self):
        self.conn.close()

    def test_plan_laptop_accountant(self):
        plan = self.assistant.build_plan("ноутбук для бухгалтера до 400 тысяч")
        self.assertEqual(plan.use_case, "office_accounting")
        self.assertEqual(plan.max_price, 400000.0)
        self.assertIn("ноутбук", plan.search_query.lower())
        self.assertFalse(plan.needs_installment)

    def test_plan_iphone_installment(self):
        plan = self.assistant.build_plan("где дешевле купить iPhone 16 Pro, если мне нужна рассрочка?")
        self.assertTrue(plan.needs_installment)
        self.assertIn("iphone 16 pro", plan.search_query.lower())

    def test_plan_used_tv_almaty(self):
        plan = self.assistant.build_plan("купить б/у телевизор Samsung до 150000 в Алматы")
        self.assertEqual(plan.condition, Condition.USED)
        self.assertEqual(plan.max_price, 150000.0)
        self.assertEqual(plan.city, "Алматы")


class TestSearchAssistantWorkflow(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)
        self.assistant = SearchAssistant(self.core)

        # Создаем продавца с 2 каналами: один прямой сайт, второй с рассрочкой
        s1 = Seller(id="s_kaspi_store", slug="kaspi-store", name="Kaspi Store", rating=4.9)
        self.seller_repo.save_or_update(s1)
        ch_direct = Channel(id="ch_dir", seller_id="s_kaspi_store", channel_type=ChannelType.DIRECT, name="Direct Web")
        ch_kaspi = Channel(id="ch_ksp", seller_id="s_kaspi_store", channel_type=ChannelType.KASPI, name="Kaspi Pay")
        self.channel_repo.save_or_update(ch_direct)
        self.channel_repo.save_or_update(ch_kaspi)

        # Канонический товар: iPhone 16 Pro
        self.prod_iphone = CanonicalProduct(
            id="p_ip16",
            canonical_key="key_ip16",
            title="Apple iPhone 16 Pro 256GB Natural Titanium",
            brand="Apple",
            category="phones"
        )
        self.product_repo.save_or_update(self.prod_iphone)

        # Предложение 1: Прямой сайт, дешевле, но без рассрочки
        self.offer_repo.save_or_update(Offer(
            id="off_dir", product_id="p_ip16", channel_id="ch_dir", seller_id="s_kaspi_store",
            price=570000, condition=Condition.NEW, availability=Availability.IN_STOCK,
            city="Алматы", external_sku="sk1", url="http://1", installment_available=False
        ))

        # Предложение 2: Kaspi, чуть дороже, но С РАССРОЧКОЙ 0-0-24
        self.offer_repo.save_or_update(Offer(
            id="off_ksp", product_id="p_ip16", channel_id="ch_ksp", seller_id="s_kaspi_store",
            price=589990, condition=Condition.NEW, availability=Availability.IN_STOCK,
            city="Алматы", external_sku="sk2", url="http://2", installment_available=True,
            installment_months=24
        ))

    def tearDown(self):
        self.conn.close()

    def test_ask_with_installment_and_cross_channel_comparison(self):
        resp: SearchAssistantResponse = self.assistant.ask(
            "где дешевле купить iPhone 16 Pro, если мне нужна рассрочка?"
        )
        self.assertEqual(resp.outcome, "FOUND")
        self.assertEqual(resp.total_products, 1)

        # Проверяем нахождение лучшего варианта в рассрочку
        self.assertIsNotNone(resp.best_installment_offer)
        self.assertEqual(resp.best_installment_offer["price"], 589990)
        self.assertEqual(resp.best_installment_offer["installment_months"], 24)

        # Проверяем лучшее предложение вообще
        self.assertIsNotNone(resp.best_overall_offer)
        self.assertEqual(resp.best_overall_offer["price"], 570000)

        # Проверяем сравнение между каналами (cross_channel_comparison)
        self.assertIsNotNone(resp.cross_channel_comparison)
        self.assertEqual(resp.cross_channel_comparison["price_difference"], 19990.0)

        # Проверяем обоснованный ответ
        self.assertIn("570 000 ₸", resp.explanation)
        self.assertIn("589 990 ₸", resp.explanation)
        self.assertIn("рассрочка 24 мес", resp.explanation)

    def test_ask_not_found(self):
        resp: SearchAssistantResponse = self.assistant.ask("несуществующий товар 999999")
        self.assertEqual(resp.outcome, "NOT_FOUND")
        self.assertEqual(resp.total_products, 0)
        self.assertIn("не найдено", resp.explanation.lower())


class TestSearchAssistantHttpEndpoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        import tempfile
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)

        conn = sqlite3.connect(self.db_path)
        init_schema_v2(conn)

        s_repo = SellerRepository(conn)
        s_repo.save_or_update(Seller(id="s1", slug="s1", name="Alser"))
        ch_repo = ChannelRepository(conn)
        ch_repo.save_or_update(Channel(id="ch1", seller_id="s1", channel_type=ChannelType.WEBSITE, name="Web"))

        p_repo = ProductRepository(conn)
        p_repo.save_or_update(CanonicalProduct(id="p_nb", canonical_key="k_nb", title="Ноутбук Lenovo ThinkPad E14", category="laptops"))

        o_repo = OfferRepository(conn)
        o_repo.save_or_update(Offer(id="o1", product_id="p_nb", channel_id="ch1", seller_id="s1", price=360000, external_sku="s1", url="http://1", city="Алматы"))

        conn.commit()
        conn.close()

        import database
        self._orig_get_connection = database.get_connection
        database.get_connection = lambda: sqlite3.connect(self.db_path)

    def tearDown(self):
        import os
        import database
        database.get_connection = self._orig_get_connection
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_endpoint_success(self):
        req = make_mocked_request("POST", "/api/v2/ai/assistant")
        req.json = AsyncMock(return_value={"message": "ноутбук ThinkPad до 400 тысяч"})
        resp = await ai_search_assistant_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertTrue(body["ok"])
        self.assertIn("explanation", body)
        self.assertEqual(body["total_products"], 1)
        self.assertEqual(body["items"][0]["product"]["id"], "p_nb")

    async def test_endpoint_empty_message(self):
        req = make_mocked_request("POST", "/api/v2/ai/assistant")
        req.json = AsyncMock(return_value={"message": ""})
        resp = await ai_search_assistant_v2_handler(req)
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
