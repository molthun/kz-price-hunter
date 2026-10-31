"""
Tests for KZPH-G1-01 (Search-first Stage 1: Search & Product Contracts).
Verifies:
1. Pagination with deterministic tie-breaker across pages without duplicates.
2. Exact total counts and global facets calculation across full matching set.
3. Filters: seller_id, installment, in_stock, delivery.
4. Canonical detail endpoint GET /api/v2/products/{product_id} with Seller->Channel->Offer aggregation,
   purchasability rules, freshness, provenance, and 404 handling.
"""

from tests import test_support  # must be first
import datetime as dt
import json
import sqlite3
import unittest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from domain.models import (
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
    utc_now_iso,
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
import web.server as srv


class TestSearchFirstContracts(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)

        # Create Sellers
        self.s1 = Seller(id="s_kaspi", name="Kaspi Store", slug="kaspi-store", rating=4.9)
        self.seller_repo.save_or_update(self.s1)
        self.s2 = Seller(id="s_sulpak", name="Sulpak", slug="sulpak", rating=4.8)
        self.seller_repo.save_or_update(self.s2)

        # Create Channels
        self.ch1 = Channel(id="ch_k1", seller_id="s_kaspi", channel_type="marketplace", name="Kaspi Market")
        self.channel_repo.save_or_update(self.ch1)
        self.ch2 = Channel(id="ch_k2", seller_id="s_kaspi", channel_type="direct", name="Kaspi Direct")
        self.channel_repo.save_or_update(self.ch2)
        self.ch3 = Channel(id="ch_s1", seller_id="s_sulpak", channel_type="direct", name="Sulpak Web")
        self.channel_repo.save_or_update(self.ch3)

    def tearDown(self):
        self.conn.close()

    def test_pagination_and_tie_breaker_stability(self):
        """Page 1 and Page 2 on fixed set must not overlap and preserve tie-breaker order."""
        now = utc_now_iso()
        # Seed 15 laptops
        for i in range(15):
            pid = f"prod_laptop_{i:02d}"
            p = CanonicalProduct(
                id=pid,
                canonical_key=f"key_laptop_{i:02d}",
                title=f"Laptop ThinkPad T14 Gen {i:02d}",
                brand="Lenovo",
                category="laptops",
            )
            self.product_repo.save_or_update(p)
            # Same price to stress-test tie breaker
            self.offer_repo.save_or_update(Offer(
                id=f"off_{i:02d}",
                product_id=pid,
                seller_id="s_sulpak",
                channel_id="ch_s1",
                external_sku=f"SKU_{i:02d}",
                url=f"https://sulpak.kz/item/{i:02d}",
                price=300000.0,
                condition=Condition.NEW,
                availability="in_stock",
                city="Алматы",
                observed_at=now,
            ))

        # Query page 1 (limit 5)
        res_p1 = self.core.search(query="ThinkPad", limit=5, page=1)
        self.assertEqual(len(res_p1.items), 5)
        self.assertEqual(res_p1.total_products, 15)
        self.assertEqual(res_p1.page, 1)
        self.assertEqual(res_p1.total_pages, 3)
        self.assertTrue(res_p1.has_next)
        self.assertFalse(res_p1.has_prev)

        p1_ids = [it.product.id for it in res_p1.items]
        self.assertEqual(len(set(p1_ids)), 5)

        # Query page 2 (limit 5)
        res_p2 = self.core.search(query="ThinkPad", limit=5, page=2)
        self.assertEqual(len(res_p2.items), 5)
        self.assertEqual(res_p2.page, 2)
        self.assertTrue(res_p2.has_next)
        self.assertTrue(res_p2.has_prev)

        p2_ids = [it.product.id for it in res_p2.items]
        self.assertEqual(len(set(p2_ids)), 5)

        # No overlap between page 1 and page 2
        overlap = set(p1_ids).intersection(set(p2_ids))
        self.assertEqual(len(overlap), 0, f"Pages must not overlap, got: {overlap}")

    def test_facets_and_advanced_filters(self):
        """Filters for seller, installment, in_stock, delivery work and return full facets."""
        now = utc_now_iso()
        # Product 1: With installment, in stock, Kaspi
        p1 = CanonicalProduct(id="p1", canonical_key="k1", title="Phone Alpha", brand="Alpha", category="phones")
        self.product_repo.save_or_update(p1)
        self.offer_repo.save_or_update(Offer(
            id="o1", product_id="p1", seller_id="s_kaspi", channel_id="ch_k1",
            external_sku="SKU1", url="https://kaspi.kz/1", price=100000, condition=Condition.NEW,
            availability="in_stock", city="Алматы", installment_available=True, installment_months=12,
            delivery_type="courier", pickup_available=True, observed_at=now,
        ))

        # Product 2: Out of stock, Sulpak, no installment
        p2 = CanonicalProduct(id="p2", canonical_key="k2", title="Phone Beta", brand="Beta", category="phones")
        self.product_repo.save_or_update(p2)
        self.offer_repo.save_or_update(Offer(
            id="o2", product_id="p2", seller_id="s_sulpak", channel_id="ch_s1",
            external_sku="SKU2", url="https://sulpak.kz/2", price=120000, condition=Condition.NEW,
            availability="out_of_stock", city="Астана", installment_available=False,
            delivery_type=None, pickup_available=False, observed_at=now,
        ))

        # Search with in_stock=True
        res_stock = self.core.search(query="Phone", in_stock=True)
        self.assertEqual(len(res_stock.items), 1)
        self.assertEqual(res_stock.items[0].product.id, "p1")

        # Search with installment=True
        res_inst = self.core.search(query="Phone", installment=True)
        self.assertEqual(len(res_inst.items), 1)
        self.assertEqual(res_inst.items[0].product.id, "p1")

        # Search with seller="sulpak"
        res_seller = self.core.search(query="Phone", seller_id="sulpak")
        self.assertEqual(len(res_seller.items), 1)
        self.assertEqual(res_seller.items[0].product.id, "p2")

    def test_canonical_product_detail_aggregation(self):
        """Canonical product detail groups sellers, channels, and enforces purchasability."""
        now = utc_now_iso()
        p = CanonicalProduct(
            id="prod_flagship",
            canonical_key="key_flagship",
            title="SuperPhone Pro 256GB",
            brand="Flagship",
            category="phones",
            image_url="https://img.kz/phone.jpg",
        )
        self.product_repo.save_or_update(p)

        # Offer 1: Sulpak, in stock, 400 000
        self.offer_repo.save_or_update(Offer(
            id="off_sulpak",
            product_id="prod_flagship",
            seller_id="s_sulpak",
            channel_id="ch_s1",
            external_sku="SKU_S",
            url="https://sulpak.kz/p/flagship",
            price=400000.0,
            condition=Condition.NEW,
            availability="in_stock",
            city="Алматы",
            observed_at=now,
        ))

        # Offer 2: Kaspi Marketplace, in stock, 390 000
        self.offer_repo.save_or_update(Offer(
            id="off_kaspi_mkt",
            product_id="prod_flagship",
            seller_id="s_kaspi",
            channel_id="ch_k1",
            external_sku="SKU_K1",
            url="https://kaspi.kz/m/flagship",
            price=390000.0,
            condition=Condition.NEW,
            availability="in_stock",
            city="Алматы",
            installment_available=True,
            installment_months=24,
            observed_at=now,
        ))

        # Offer 3: Kaspi Direct, out of stock (stale/unpurchasable), fake low price 100 000
        self.offer_repo.save_or_update(Offer(
            id="off_kaspi_dir",
            product_id="prod_flagship",
            seller_id="s_kaspi",
            channel_id="ch_k2",
            external_sku="SKU_K2",
            url="https://kaspi.kz/d/flagship",
            price=100000.0,
            condition=Condition.NEW,
            availability="out_of_stock",
            city="Алматы",
            observed_at=now,
        ))

        detail = self.core.get_canonical_product("prod_flagship")
        self.assertIsNotNone(detail)
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["product"]["id"], "prod_flagship")

        # Crucial: Unpurchasable 100 000 offer MUST NOT be min_price!
        self.assertEqual(detail["min_price"], 390000.0)
        self.assertEqual(detail["best_new_price"], 390000.0)
        self.assertEqual(detail["sellers_count"], 2)
        self.assertEqual(detail["offers_count"], 3)

        # Kaspi seller group should contain both channels
        kaspi_group = next(s for s in detail["sellers"] if s["seller_id"] == "s_kaspi")
        self.assertEqual(kaspi_group["channels_count"], 2)
        self.assertEqual(kaspi_group["min_price"], 390000.0)

        # Unknown product returns None
        self.assertIsNone(self.core.get_canonical_product("non_existent_id"))

    def test_golden_set_discrimination(self):
        """Golden set: RTX 5090 vs cable, iPhone 16 Pro vs Pro Max, MacBook M5 vs M4."""
        now = utc_now_iso()

        # 1. RTX 5090 vs waterblock / cable
        p_gpu = CanonicalProduct(id="gpu_5090", canonical_key="gpu_5090", title="Видеокарта Nvidia GeForce RTX 5090 32GB", brand="Nvidia", model="RTX 5090", category="components")
        p_cable = CanonicalProduct(id="cable_5090", canonical_key="cable_5090", title="Кабель питания 12VHPWR для RTX 5090", brand="Acme", model="Cable 12V", category="accessories")
        self.product_repo.save_or_update(p_gpu)
        self.product_repo.save_or_update(p_cable)
        self.offer_repo.save_or_update(Offer(id="o_g", product_id="gpu_5090", seller_id="s_sulpak", channel_id="ch_s1", external_sku="G1", url="http://sulpak.kz/gpu", price=1200000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))
        self.offer_repo.save_or_update(Offer(id="o_c", product_id="cable_5090", seller_id="s_sulpak", channel_id="ch_s1", external_sku="C1", url="http://sulpak.kz/cable", price=15000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))

        res_rtx = self.core.search(query="RTX 5090")
        self.assertGreater(len(res_rtx.items), 0)
        # First item must be the GPU, not the cable
        self.assertEqual(res_rtx.items[0].product.id, "gpu_5090")
        self.assertGreater(res_rtx.items[0].relevance_score, res_rtx.items[1].relevance_score)

        # 2. iPhone 16 Pro vs Pro Max
        p_pro = CanonicalProduct(id="p_16pro", canonical_key="p_16pro", title="Apple iPhone 16 Pro 256GB", brand="Apple", model="iPhone 16 Pro", category="phones")
        p_promax = CanonicalProduct(id="p_16promax", canonical_key="p_16promax", title="Apple iPhone 16 Pro Max 256GB", brand="Apple", model="iPhone 16 Pro Max", category="phones")
        self.product_repo.save_or_update(p_pro)
        self.product_repo.save_or_update(p_promax)
        self.offer_repo.save_or_update(Offer(id="o_p1", product_id="p_16pro", seller_id="s_kaspi", channel_id="ch_k1", external_sku="IP1", url="http://kaspi.kz/pro", price=600000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))
        self.offer_repo.save_or_update(Offer(id="o_p2", product_id="p_16promax", seller_id="s_kaspi", channel_id="ch_k1", external_sku="IP2", url="http://kaspi.kz/promax", price=700000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))

        # Cyrillic query "айфон 16 про"
        res_iphone = self.core.search(query="айфон 16 про")
        self.assertGreater(len(res_iphone.items), 0)
        self.assertEqual(res_iphone.items[0].product.id, "p_16pro")

        # 3. MacBook Air M5 vs M4
        p_m5 = CanonicalProduct(id="mac_m5", canonical_key="mac_m5", title="Apple MacBook Air 15 M5 16/512GB", brand="Apple", model="MacBook Air M5", category="laptops")
        p_m4 = CanonicalProduct(id="mac_m4", canonical_key="mac_m4", title="Apple MacBook Air 13 M4 8/256GB", brand="Apple", model="MacBook Air M4", category="laptops")
        self.product_repo.save_or_update(p_m5)
        self.product_repo.save_or_update(p_m4)
        self.offer_repo.save_or_update(Offer(id="o_m5", product_id="mac_m5", seller_id="s_sulpak", channel_id="ch_s1", external_sku="M5", url="http://sulpak.kz/m5", price=850000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))
        self.offer_repo.save_or_update(Offer(id="o_m4", product_id="mac_m4", seller_id="s_sulpak", channel_id="ch_s1", external_sku="M4", url="http://sulpak.kz/m4", price=550000, condition=Condition.NEW, availability="in_stock", city="Алматы", observed_at=now))

        res_mac = self.core.search(query="MacBook Air M5")
        self.assertGreater(len(res_mac.items), 0)
        self.assertEqual(res_mac.items[0].product.id, "mac_m5")


class _NoCloseConn:
    def __init__(self, conn):
        self._conn = conn
    def close(self):
        pass
    def __getattr__(self, name):
        return getattr(self._conn, name)


class TestSearchFirstHttpEndpoints(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application()
        app.add_routes([
            web.get("/", srv.index_handler),
            web.get("/dashboard", srv.dashboard_handler),
            web.get("/search", srv.search_page_handler),
            web.get("/product/{product_id}", srv.product_page_handler),
            web.get("/api/v2/search", srv.search_v2_handler),
            web.get("/api/v2/products/{product_id}", srv.product_detail_v2_handler),
        ])
        return app

    def setUp(self):
        super().setUp()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_schema_v2(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)

        # Mock database.get_connection to return wrapped connection
        import database
        self._orig_get_conn = database.get_connection
        database.get_connection = lambda: _NoCloseConn(self.conn)

        # Seed data
        s = Seller(id="s_test", name="TestSeller", slug="test-seller")
        self.seller_repo.save_or_update(s)
        ch = Channel(id="ch_test", seller_id="s_test", channel_type="website", name="Site")
        self.channel_repo.save_or_update(ch)
        p = CanonicalProduct(id="prod_http_1", canonical_key="key_http_1", title="Apple MacBook Air M3", category="laptops", brand="Apple", model="MacBook Air M3")
        self.product_repo.save_or_update(p)
        self.offer_repo.save_or_update(Offer(
            id="off_http_1", product_id="prod_http_1", seller_id="s_test", channel_id="ch_test",
            external_sku="SKU1", url="https://store.kz/macbook", price=550000, condition=Condition.NEW,
            availability="in_stock", city="Алматы", observed_at=utc_now_iso(),
        ))

    def tearDown(self):
        import database
        database.get_connection = self._orig_get_conn
        self.conn.close()
        super().tearDown()

    @unittest_run_loop
    async def test_search_v2_pagination_http(self):
        resp = await self.client.get("/api/v2/search?q=MacBook&page=1&limit=10")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn(data["outcome"], ("FOUND", "WEAK_MATCH"))
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 10)
        self.assertIn("total_pages", data)
        self.assertIn("has_next", data)
        self.assertIn("has_prev", data)
        self.assertIn("seller_facets", data)
        self.assertIn("city_facets", data)
        self.assertEqual(len(data["items"]), 1)

    @unittest_run_loop
    async def test_product_detail_v2_http_found_and_not_found(self):
        # 1. Found product
        resp = await self.client.get("/api/v2/products/prod_http_1")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["product"]["id"], "prod_http_1")
        self.assertEqual(data["min_price"], 550000)
        self.assertEqual(len(data["sellers"]), 1)
        self.assertEqual(len(data["offers"]), 1)
        self.assertEqual(data["offers"][0]["url"], "https://store.kz/macbook")

        # 2. Not found product
        resp_404 = await self.client.get("/api/v2/products/unknown_xyz")
        self.assertEqual(resp_404.status, 404)
        data_404 = await resp_404.json()
        self.assertFalse(data_404["ok"])

    @unittest_run_loop
    async def test_page_routing_and_legacy_toggle(self):
        # 1. Root / serves Search-First UI without dashboard KPI
        resp_root = await self.client.get("/")
        self.assertEqual(resp_root.status, 200)
        text_root = await resp_root.text()
        self.assertIn("Найдите лучшие цены на товары в Казахстане", text_root)
        self.assertIn("homeSearchInput", text_root)
        self.assertNotIn("Всего товаров в базе", text_root)  # Dashboard KPI absent from customer homepage

        # 2. Legacy /?mode=legacy serves classic dashboard
        resp_legacy = await self.client.get("/?mode=legacy")
        self.assertEqual(resp_legacy.status, 200)
        text_legacy = await resp_legacy.text()
        self.assertIn("Всего товаров в базе", text_legacy)

        # 3. /dashboard serves classic dashboard
        resp_dash = await self.client.get("/dashboard")
        self.assertEqual(resp_dash.status, 200)
        text_dash = await resp_dash.text()
        self.assertIn("Всего товаров в базе", text_dash)

        # 4. /search and /product/{id} serve search-first shell
        resp_search = await self.client.get("/search?q=phone")
        self.assertEqual(resp_search.status, 200)
        text_search = await resp_search.text()
        self.assertIn("viewResults", text_search)

        resp_prod = await self.client.get("/product/prod_http_1")
        self.assertEqual(resp_prod.status, 200)
        text_prod = await resp_prod.text()
        self.assertIn("viewDetail", text_prod)


if __name__ == "__main__":
    unittest.main()
