"""Тесты для Search UI 2.0 (Этап 16).
Проверяют разметку витрины, переключатели Search Core 2.0, фильтры condition и API эндпоинты v2.
"""
from tests import test_support  # noqa: F401  изолирует DATA_DIR
import datetime
import unittest

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()
from aiohttp.test_utils import TestClient, TestServer
import web.server as server
from database import get_connection
from repositories.schema_v2 import init_schema_v2
from repositories import (
    ProductRepository,
    SellerRepository,
    ChannelRepository,
    OfferRepository,
)
from domain.models import (
    CanonicalProduct,
    Seller,
    Channel,
    ChannelType,
    Offer,
    Condition,
)


class TestSearchUIV2(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        conn = get_connection()
        try:
            init_schema_v2(conn)
            p_repo = ProductRepository(conn)
            s_repo = SellerRepository(conn)
            c_repo = ChannelRepository(conn)
            o_repo = OfferRepository(conn)

            # Product
            p = CanonicalProduct(
                id="p_iphone15pro",
                canonical_key="apple_iphone_15_pro_256",
                title="Apple iPhone 15 Pro 256GB Black Titanium",
                category="Смартфоны",
                brand="Apple",
                model="iPhone 15 Pro",
                attributes={"storage": "256gb"},
            )
            p_repo.save_or_update(p)

            # Sellers
            s_kaspi = s_repo.get_by_slug("kaspi_ui")
            if not s_kaspi:
                s_kaspi = s_repo.save_or_update(Seller(id="s_kaspi_ui", slug="kaspi_ui", name="Kaspi Магазин", rating=4.9))

            s_mechta = s_repo.get_by_slug("mechta_ui")
            if not s_mechta:
                s_mechta = s_repo.save_or_update(Seller(id="s_mechta_ui", slug="mechta_ui", name="Мечта", rating=4.8))

            # Channels
            ch_kaspi = c_repo.get_by_id("ch_kaspi_ui")
            if not ch_kaspi:
                ch_kaspi = c_repo.save_or_update(Channel(id="ch_kaspi_ui", seller_id=s_kaspi.id, channel_type=ChannelType.KASPI, name="Kaspi"))

            ch_mechta = c_repo.get_by_id("ch_mechta_ui")
            if not ch_mechta:
                ch_mechta = c_repo.save_or_update(Channel(id="ch_mechta_ui", seller_id=s_mechta.id, channel_type=ChannelType.WEBSITE, name="Мечта"))

            # Offers
            # 1. New in Mechta
            o_repo.save_or_update(Offer(
                id="off_m_new",
                product_id=p.id,
                seller_id=s_mechta.id,
                channel_id=ch_mechta.id,
                external_sku="SKU-M-1",
                url="https://mechta.kz/p/iphone15pro",
                price=540000.0,
                condition=Condition.NEW,
                city="Алматы",
                observed_at=NOW_ISO,
            ))
            # 2. New in Kaspi
            o_repo.save_or_update(Offer(
                id="off_k_new",
                product_id=p.id,
                seller_id=s_kaspi.id,
                channel_id=ch_kaspi.id,
                external_sku="SKU-K-1",
                url="https://kaspi.kz/p/iphone15pro",
                price=550000.0,
                condition=Condition.NEW,
                city="Алматы",
                observed_at=NOW_ISO,
            ))
            # 3. Used in Kaspi
            o_repo.save_or_update(Offer(
                id="off_k_used",
                product_id=p.id,
                seller_id=s_kaspi.id,
                channel_id=ch_kaspi.id,
                external_sku="SKU-K-USED",
                url="https://kaspi.kz/p/iphone15pro-used",
                price=430000.0,
                condition=Condition.USED,
                city="Алматы",
                observed_at=NOW_ISO,
            ))
            conn.commit()
        finally:
            conn.close()

        app = server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_index_page_contains_v2_elements(self):
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()

        # Search Core 2.0 toggle
        self.assertIn("checkSearchV2", html)
        self.assertIn("Search Core 2.0", html)
        self.assertIn("onSearchV2Toggle", html)
        self.assertIn("kz_search_v2_enabled", html)

        # Condition pill filter buttons
        self.assertIn("condition-pill", html)
        self.assertIn("setConditionSearchFilter", html)
        self.assertIn("btnCondAll", html)
        self.assertIn("btnCondNew", html)
        self.assertIn("btnCondUsed", html)

        # V2 Container and rendering functions
        self.assertIn("searchV2ProductsSection", html)
        self.assertIn("searchV2ProductsContainer", html)
        self.assertIn("searchV2ProductsCountBadge", html)
        self.assertIn("searchV2LatencyBadge", html)
        self.assertIn("renderSearchV2ProductCards", html)

    async def test_api_best_price_v2_all_conditions(self):
        resp = await self.client.get("/api/best-price?q=iphone+15+pro&v2=1")
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data.get("v2"))
        self.assertGreater(data.get("total_products", 0), 0)
        self.assertGreater(data.get("total_offers", 0), 0)
        self.assertIsNotNone(data.get("best_deal"))
        self.assertIsNotNone(data.get("price_stats"))
        self.assertTrue(len(data.get("store_comparison", [])) > 0)

        # Check item structure with top-level fields (C-E16-02)
        items = data.get("items", [])
        self.assertTrue(len(items) > 0)
        target_item = next((it for it in items if it["product"]["id"] == "p_iphone15pro"), None)
        self.assertIsNotNone(target_item)
        self.assertIn("product", target_item)
        self.assertIn("best_offer", target_item)
        self.assertIn("offers", target_item)
        self.assertIn("min_price", target_item)
        self.assertIn("max_price", target_item)
        self.assertIn("sellers_count", target_item)
        self.assertIn("offers_count", target_item)
        self.assertIn("conditions_available", target_item)
        self.assertEqual(target_item["min_price"], 430000.0)
        self.assertEqual(target_item["max_price"], 550000.0)
        self.assertEqual(target_item["sellers_count"], 2)
        self.assertEqual(target_item["offers_count"], 3)
        self.assertTrue(target_item["has_new"])
        self.assertTrue(target_item["has_used"])

    async def test_api_best_price_v2_filter_new_only(self):
        resp = await self.client.get("/api/best-price?q=iphone+15+pro&v2=1&condition=new")
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data.get("v2"))
        target_item = next((it for it in data.get("items", []) if it["product"]["id"] == "p_iphone15pro"), None)
        self.assertIsNotNone(target_item)
        for off in target_item.get("offers", []):
            self.assertEqual(off.get("condition").upper(), "NEW")

    async def test_api_best_price_v2_filter_used_only(self):
        resp = await self.client.get("/api/best-price?q=iphone+15+pro&v2=1&condition=used")
        self.assertEqual(resp.status, 200)
        data = await resp.json()

        self.assertTrue(data.get("v2"))
        target_item = next((it for it in data.get("items", []) if it["product"]["id"] == "p_iphone15pro"), None)
        self.assertIsNotNone(target_item)
        for off in target_item.get("offers", []):
            self.assertEqual(off.get("condition").upper(), "USED")

    async def test_api_best_price_v2_shop_filter(self):
        # C-E16-04: Test filtering by shop in V2
        resp = await self.client.get("/api/best-price?q=iphone+15+pro&v2=1&shop=Мечта")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data.get("v2"))
        target_item = next((it for it in data.get("items", []) if it["product"]["id"] == "p_iphone15pro"), None)
        self.assertIsNotNone(target_item)
        for off in target_item.get("offers", []):
            self.assertIn("мечта", (off.get("channel_name") or off.get("seller_name") or "").lower())

    async def test_api_best_price_v2_all_stale_returns_none(self):
        # C-E16-03 / C-E11-03: When only out_of_stock offers exist, best_deal must be None and all_stale True
        conn = get_connection()
        try:
            # Mark all offers as out_of_stock
            conn.execute("UPDATE offers SET availability = 'out_of_stock'")
            conn.commit()
        finally:
            conn.close()

        resp = await self.client.get("/api/best-price?q=iphone+15+pro&v2=1")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIsNone(data.get("best_deal"))
        self.assertIsNone(data.get("price_stats"))
        self.assertEqual(data.get("store_comparison"), [])
        self.assertTrue(data.get("all_stale"))


if __name__ == "__main__":
    unittest.main()
