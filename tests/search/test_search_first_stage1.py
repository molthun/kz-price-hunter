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
import unittest.mock
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
        self.assertIn("breadcrumbs", detail)
        self.assertTrue(len(detail["breadcrumbs"]) >= 1)
        self.assertEqual(detail["breadcrumbs"][-1]["id"], "phones")

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

    def _seed_facet_catalog(self):
        """>100 товаров: две категории, два бренда, два продавца, два города."""
        now = utc_now_iso()
        for i in range(130):
            pid = f"lap{i:03d}"
            self.product_repo.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"key_{pid}", title=f"Laptop {pid}",
                brand="Acer" if i < 100 else "Dell",
                category="laptops" if i < 110 else "Редкие Штуки",
            ))
            sulpak = i % 2 == 0
            self.offer_repo.save_or_update(Offer(
                id=f"o_{pid}", product_id=pid,
                seller_id="s_sulpak" if sulpak else "s_kaspi",
                channel_id="ch_s1" if sulpak else "ch_k1",
                external_sku=pid, url=f"https://example.invalid/{pid}", price=1000 + i,
                condition=Condition.NEW if i < 120 else Condition.USED, availability="in_stock",
                city="Алматы" if i < 90 else "Астана", observed_at=now,
            ))
        # Посторонний товар: не должен попасть ни в один фасет по запросу Laptop
        self.product_repo.save_or_update(CanonicalProduct(
            id="chair", canonical_key="key_chair", title="Chair", brand="Ikea", category="home"))
        self.seller_repo.save_or_update(Seller(id="s_other", name="Other", slug="other"))
        self.channel_repo.save_or_update(Channel(id="ch_o", seller_id="s_other", channel_type="direct", name="Other"))
        self.offer_repo.save_or_update(Offer(
            id="o_chair", product_id="chair", seller_id="s_other", channel_id="ch_o", external_sku="chair",
            url="https://example.invalid/chair", price=5, condition=Condition.NEW,
            availability="in_stock", city="Шымкент", observed_at=now))

    def test_facets_describe_full_selection_not_page(self):
        self._seed_facet_catalog()
        pages = [self.core.search("Laptop", limit=20, page=p) for p in (1, 7)]
        for res in pages:
            self.assertTrue(res.facets_available)
            self.assertEqual(res.total_products, 130)
            cats = {f["name"]: f["count"] for f in res.category_facets}
            self.assertEqual(sum(cats.values()), 130)
            self.assertEqual(cats["Редкие Штуки"], 20)
            self.assertEqual({f["brand"]: f["count"] for f in res.brand_facets}, {"Acer": 100, "Dell": 30})
            self.assertEqual({f["slug"]: f["count"] for f in res.seller_facets}, {"sulpak": 65, "kaspi-store": 65})
            self.assertEqual({f["city"]: f["count"] for f in res.city_facets}, {"Алматы": 90, "Астана": 40})
        self.assertEqual(pages[0].category_facets, pages[1].category_facets)

    def test_facets_apply_other_filters_but_not_their_own_group(self):
        self._seed_facet_catalog()
        res = self.core.search("Laptop", city="Астана", seller_id="sulpak", condition="USED", limit=5)
        # Выборка: i 120..129, чётные — 5 товаров Астаны у Sulpak
        self.assertEqual(res.total_products, 5)
        self.assertEqual(sum(f["count"] for f in res.category_facets), 5)
        # Группа «продавец» не сужена своим выбором, но сужена остальными фильтрами
        self.assertEqual({f["slug"]: f["count"] for f in res.seller_facets}, {"sulpak": 5, "kaspi-store": 5})
        # Группа «город»: USED есть только в Астане, посторонний город не появляется
        self.assertEqual({f["city"]: f["count"] for f in res.city_facets}, {"Астана": 5})

        by_price = self.core.search("Laptop", min_price=1125, limit=5)
        self.assertEqual(by_price.total_products, 5)
        self.assertEqual({f["brand"]: f["count"] for f in by_price.brand_facets}, {"Dell": 5})

        by_cat = self.core.search("Laptop", category="Редкие Штуки", limit=5)
        self.assertEqual(by_cat.total_products, 20)
        self.assertEqual({f["brand"]: f["count"] for f in by_cat.brand_facets}, {"Dell": 20})
        self.assertEqual(sum(f["count"] for f in by_cat.category_facets), 130, "своя группа не сужается")

    def test_unmapped_category_facet_id_is_accepted_by_filter(self):
        self._seed_facet_catalog()
        res = self.core.search("Laptop", limit=5)
        facet = next(f for f in res.category_facets if f["name"] == "Редкие Штуки")
        self.assertEqual(self.core.search("Laptop", category=facet["id"], limit=5).total_products, 20)

    def test_facets_empty_query_covers_whole_catalog(self):
        self._seed_facet_catalog()
        res = self.core.search("", limit=5)
        self.assertEqual(res.total_products, 131)
        self.assertEqual(sum(f["count"] for f in res.category_facets), 131)
        self.assertIn("Шымкент", {f["city"] for f in res.city_facets})

    def test_taxonomy_category_facet_merges_raw_store_categories(self):
        now = utc_now_iso()
        for pid, cat in (("n1", "laptops"), ("n2", "Ноутбуки")):
            self.product_repo.save_or_update(CanonicalProduct(id=pid, canonical_key=pid, title=f"Laptop {pid}", category=cat))
            self.offer_repo.save_or_update(Offer(
                id=f"o_{pid}", product_id=pid, seller_id="s_sulpak", channel_id="ch_s1", external_sku=pid,
                url="https://example.invalid/x", price=10, availability="in_stock", city="Алматы", observed_at=now))
        res = self.core.search("Laptop", limit=5)
        ids = [f["id"] for f in res.category_facets]
        self.assertEqual(len(ids), len(set(ids)), "значения фасета не дублируются")
        self.assertEqual(sum(f["count"] for f in res.category_facets), 2)

    def test_facet_failure_is_reported_not_replaced_by_page(self):
        self._seed_facet_catalog()
        with unittest.mock.patch.object(self.search_repo, "get_search_facets", side_effect=sqlite3.OperationalError("boom")):
            res = self.core.search("Laptop", limit=5)
        self.assertEqual(len(res.items), 5)
        self.assertFalse(res.facets_available)
        self.assertEqual(res.category_facets, [])
        self.assertEqual(res.brand_facets, [])
        self.assertFalse(res.to_dict()["facets_available"] is True)

    def test_detail_and_search_carry_saved_delivery_terms(self):
        now = utc_now_iso()
        self.product_repo.save_or_update(CanonicalProduct(id="t1", canonical_key="t1", title="Terms item", category="food"))
        self.offer_repo.save_or_update(Offer(
            id="o_known", product_id="t1", seller_id="s_sulpak", channel_id="ch_s1", external_sku="known",
            url="https://example.invalid/k", price=6000, condition=Condition.OPEN_BOX, availability="in_stock",
            city="Алматы", delivery_type="courier", pickup_available=True, warranty="Гарантия производителя",
            observed_at=now))
        self.offer_repo.save_or_update(Offer(
            id="o_none", product_id="t1", seller_id="s_kaspi", channel_id="ch_k1", external_sku="none",
            url="https://example.invalid/n", price=7000, availability="in_stock", city="Алматы", observed_at=now))
        detail_offers = {o["id"]: o for o in self.core.get_canonical_product("t1")["offers"]}
        search_offers = {o.id: o.to_dict() for o in self.core.search("Terms").items[0].offers}
        for offers in (detail_offers, search_offers):
            self.assertEqual(offers["o_known"]["delivery_type"], "courier")
            self.assertTrue(offers["o_known"]["pickup_available"])
            self.assertEqual(offers["o_known"]["warranty"], "Гарантия производителя")
            self.assertEqual(offers["o_known"]["condition"], Condition.OPEN_BOX)
            # Отсутствующие условия не домысливаются
            self.assertIsNone(offers["o_none"]["delivery_type"])
            self.assertFalse(offers["o_none"]["pickup_available"])
            self.assertIsNone(offers["o_none"]["warranty"])



class _NoCloseConn:
    def __init__(self, conn):
        self._conn = conn
    def close(self):
        pass
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, exc_tb):
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

    @unittest_run_loop
    async def test_light_and_dark_theme_support(self):
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()

        # 1. Early theme detection in <head> to prevent FOUC
        self.assertIn("localStorage.getItem('kz_theme')", html)
        self.assertIn("data-theme", html)

        # 2. Both theme CSS variable definitions
        self.assertIn('[data-theme="light"]', html)
        self.assertIn('[data-theme="dark"]', html)
        self.assertIn("--bg-page: #0b0f19", html)
        self.assertIn("--bg-card: #131b2e", html)

        # 3. Theme selection options in Profile (Light, Dark, Auto System)
        self.assertIn('id="themeOptLight"', html)
        self.assertIn('id="themeOptDark"', html)
        self.assertIn('id="themeOptAuto"', html)
        self.assertIn("selectTheme('light')", html)
        self.assertIn("selectTheme('auto')", html)

        # 4. JS theme selection and persistence logic
        self.assertIn("function selectTheme(", html)
        self.assertIn("localStorage.setItem('kz_theme_mode'", html)
        self.assertIn("prefers-color-scheme: dark", html)

    @unittest_run_loop
    async def test_header_and_profile_admin_users(self):
        resp = await self.client.get("/")
        self.assertEqual(resp.status, 200)
        html = await resp.text()

        # Mobile header hides profile button when bottom navigation appears
        self.assertIn("#headerAuthArea,", html)
        self.assertIn(".header-user-btn", html)
        self.assertIn("display: none !important", html)

        # Bottom nav center highlighted search FAB and profile avatar
        self.assertIn('id="bottomNavSearch"', html)
        self.assertIn('class="bottom-nav-fab"', html)
        self.assertIn('id="bottomNavProfileAvatar"', html)
        self.assertIn('class="bottom-nav-profile-img"', html)
        self.assertIn('id="bottomNavCatalog"', html)

        # Profile admin users management card
        self.assertIn('id="profileAdminUsersCard"', html)
        self.assertIn("loadAdminUsersList()", html)
        self.assertIn("adminSetApproved(", html)
        self.assertIn("adminSetBlocked(", html)

        # Admin users API response helper includes approval and confirmation status
        from unittest.mock import patch
        mock_users = [{
            "id": 123456,
            "username": "hunter_user",
            "first_name": "Test",
            "last_name": "Hunter",
            "photo_url": "",
            "settings": {"is_approved": True},
            "is_blocked": 0,
            "created_at": "2026-11-01T00:00:00Z",
            "last_login_at": "2026-11-01T00:00:00Z",
        }]
        with patch("web.server.list_users", return_value=mock_users):
            import auth
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {1}):
                from aiohttp.test_utils import make_mocked_request
                req = make_mocked_request("GET", "/api/admin/users")
                req["user"] = {"id": 1}
                resp_api = await srv.admin_users_handler(req)
                self.assertEqual(resp_api.status, 200)
                import json
                data = json.loads(resp_api.text)
                self.assertEqual(len(data), 1)
                self.assertTrue(data[0]["is_approved"])
                self.assertTrue(data[0]["is_confirmed"])


if __name__ == "__main__":
    unittest.main()
