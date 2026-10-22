"""
Tests for Unified Product Index & Search Core 2.0 (KZ Price Hunter 2.0).
Verifies:
1. QueryParser 2.0 natural language parsing (city, condition, price range, attributes, brand, channel).
2. FTS5 index synchronization on canonical_products via SQLite triggers.
3. Multi-merchant product aggregation across independent sellers and channels.
4. City and Condition (NEW vs USED) filtering.
5. Search ranking and Search Outcome KPI (FOUND, WEAK_MATCH, NOT_FOUND).
"""

from tests import test_support  # must be first
import datetime
import json
import sqlite3
import unittest
from aiohttp.test_utils import make_mocked_request

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.query_parser import ParsedQuery, QueryParser
from domain.search_core import (
    AggregatedOffer,
    ProductSearchResult,
    SearchCore,
    SearchOutcome,
    SearchResponse2,
)
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
    rebuild_canonical_products_fts,
)


class TestQueryParserV2(unittest.TestCase):
    def test_city_extraction(self):
        p1 = QueryParser.parse("rtx 4070 астана")
        self.assertEqual(p1.detected_city, "Астана")
        self.assertEqual(p1.clean_query, "rtx 4070")

        p2 = QueryParser.parse("макбук air m3 в алматы")
        self.assertEqual(p2.detected_city, "Алматы")

        p3 = QueryParser.parse("смартфон шымкент")
        self.assertEqual(p3.detected_city, "Шымкент")

        p4 = QueryParser.parse("пылесос дайсон караганда")
        self.assertEqual(p4.detected_city, "Караганда")
        self.assertEqual(p4.detected_brand, "Dyson")

    def test_condition_extraction(self):
        p_used = QueryParser.parse("iphone 15 pro б/у")
        self.assertEqual(p_used.condition, Condition.USED)

        p_used2 = QueryParser.parse("ноутбук бу алматы")
        self.assertEqual(p_used2.condition, Condition.USED)
        self.assertEqual(p_used2.detected_city, "Алматы")

        p_new = QueryParser.parse("ноутбук asus rog новый")
        self.assertEqual(p_new.condition, Condition.NEW)
        self.assertEqual(p_new.detected_brand, "Asus")

        p_refurb = QueryParser.parse("samsung s23 refurbished")
        self.assertEqual(p_refurb.condition, Condition.REFURBISHED)
        self.assertEqual(p_refurb.detected_brand, "Samsung")

        p_openbox = QueryParser.parse("ps5 витринный образец")
        self.assertEqual(p_openbox.condition, Condition.OPEN_BOX)
        self.assertEqual(p_openbox.detected_brand, "Sony")

    def test_price_range_extraction(self):
        p_range = QueryParser.parse("айфон от 200000 до 450000 тг")
        self.assertEqual(p_range.min_price, 200000.0)
        self.assertEqual(p_range.max_price, 450000.0)

        p_to_k = QueryParser.parse("ноутбук до 300к астана")
        self.assertEqual(p_to_k.max_price, 300000.0)
        self.assertEqual(p_to_k.detected_city, "Астана")

        p_from_k = QueryParser.parse("часы от 50k")
        self.assertEqual(p_from_k.min_price, 50000.0)

    def test_attributes_extraction(self):
        p = QueryParser.parse("iphone 16 pro 256gb")
        self.assertEqual(p.attributes.get("storage"), "256gb")

        p_tb = QueryParser.parse("ipad pro 1tb")
        self.assertEqual(p_tb.attributes.get("storage"), "1tb")

    def test_channel_extraction(self):
        p_kaspi = QueryParser.parse("iphone 16 на kaspi")
        self.assertEqual(p_kaspi.channel_type, ChannelType.KASPI)

        p_tg = QueryParser.parse("ноутбук в телеграм")
        self.assertEqual(p_tg.channel_type, ChannelType.TELEGRAM)

    def test_combined_complex_query(self):
        p = QueryParser.parse("айфон 16 про 256gb бу в астане до 450000 тг")
        self.assertEqual(p.detected_city, "Астана")
        self.assertEqual(p.condition, Condition.USED)
        self.assertEqual(p.max_price, 450000.0)
        self.assertEqual(p.attributes.get("storage"), "256gb")
        self.assertEqual(p.detected_brand, "Apple")
        self.assertIn("16", p.clean_query)
        self.assertIn("про", p.clean_query)


class TestUnifiedProductIndexFTS(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_fts5_triggers_insert_update_delete(self):
        cur = self.conn.cursor()

        # 1. Insert into canonical_products
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, created_at, updated_at)
        VALUES ('p1', 'apple_iphone_16_pro_256', 'Apple iPhone 16 Pro 256GB Black Titanium', 'Смартфоны', 'Apple', 'iPhone 16 Pro', 't', 't')
        """)
        self.conn.commit()

        # Verify row exists in FTS
        row = cur.execute("SELECT * FROM canonical_products_fts WHERE canonical_products_fts MATCH 'iphone'").fetchone()
        self.assertNotNull = self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Apple iPhone 16 Pro 256GB Black Titanium")

        # 2. Update canonical_products
        cur.execute("""
        UPDATE canonical_products
        SET title = 'Apple iPhone 16 Pro 256GB Desert Titanium'
        WHERE id = 'p1'
        """)
        self.conn.commit()

        row_old = cur.execute("SELECT * FROM canonical_products_fts WHERE canonical_products_fts MATCH 'desert'").fetchone()
        self.assertIsNotNone(row_old)
        self.assertEqual(row_old["title"], "Apple iPhone 16 Pro 256GB Desert Titanium")

        # 3. Delete from canonical_products
        cur.execute("DELETE FROM canonical_products WHERE id = 'p1'")
        self.conn.commit()

        row_del = cur.execute("SELECT * FROM canonical_products_fts WHERE canonical_products_fts MATCH 'iphone'").fetchone()
        self.assertIsNone(row_del)

    def test_rebuild_fts_index(self):
        cur = self.conn.cursor()
        cur.execute("""
        INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, created_at, updated_at)
        VALUES ('p2', 'rtx_5090', 'GeForce RTX 5090 Gaming 32GB', 'Видеокарты', 'NVIDIA', 'RTX 5090', 't', 't')
        """)
        self.conn.commit()

        # Call rebuild helper
        rebuild_canonical_products_fts(self.conn)

        row = cur.execute("SELECT * FROM canonical_products_fts WHERE canonical_products_fts MATCH '5090'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["model"], "RTX 5090")


class TestSearchCoreV2MultiMerchant(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.search_core = SearchCore(self.search_repo)

        # 1. Seed Canonical Products
        self.prod_iphone = CanonicalProduct(
            id="prod_ip16p",
            canonical_key="apple_iphone_16_pro_256",
            title="Apple iPhone 16 Pro 256GB Black Titanium",
            category="Смартфоны",
            brand="Apple",
            model="iPhone 16 Pro",
            attributes={"storage": "256gb", "color": "black"},
        )
        self.product_repo.save_or_update(self.prod_iphone)

        self.prod_rtx = CanonicalProduct(
            id="prod_rtx5090",
            canonical_key="nvidia_geforce_rtx_5090",
            title="ASUS ROG Strix GeForce RTX 5090 32GB",
            category="Видеокарты",
            brand="Asus",
            model="RTX 5090",
            attributes={"memory": "32gb"},
        )
        self.product_repo.save_or_update(self.prod_rtx)

        # 2. Seed Sellers
        self.s_4mobile = self.seller_repo.save_or_update(
            Seller(id="s_4mobile", slug="4mobile", name="4mobile", rating=4.8)
        )
        self.s_mechta = self.seller_repo.save_or_update(
            Seller(id="s_mechta", slug="mechta", name="Мечта", rating=4.9)
        )
        self.s_ispace = self.seller_repo.save_or_update(
            Seller(id="s_ispace", slug="ispace", name="iSpace Apple Premium", rating=5.0)
        )
        self.s_resale = self.seller_repo.save_or_update(
            Seller(id="s_resale", slug="apple_resale", name="Apple Resale Almaty", rating=4.5)
        )

        # 3. Seed Channels
        self.ch_4mobile_web = self.channel_repo.save_or_update(
            Channel(id="ch_4m_w", seller_id=self.s_4mobile.id, channel_type=ChannelType.WEBSITE, name="4mobile.kz")
        )
        self.ch_mechta_web = self.channel_repo.save_or_update(
            Channel(id="ch_mechta_w", seller_id=self.s_mechta.id, channel_type=ChannelType.WEBSITE, name="mechta.kz")
        )
        self.ch_ispace_web = self.channel_repo.save_or_update(
            Channel(id="ch_ispace_w", seller_id=self.s_ispace.id, channel_type=ChannelType.WEBSITE, name="ispace.kz")
        )
        self.ch_resale_tg = self.channel_repo.save_or_update(
            Channel(id="ch_resale_tg", seller_id=self.s_resale.id, channel_type=ChannelType.TELEGRAM, name="Telegram Resale")
        )

        # 4. Seed Multi-Merchant Offers
        # Offer 1: 4mobile NEW in Almaty - 530,000 KZT
        self.offer_repo.save_or_update(
            Offer(
                id="off_1",
                product_id=self.prod_iphone.id,
                seller_id=self.s_4mobile.id,
                channel_id=self.ch_4mobile_web.id,
                external_sku="SKU-4M-IP16",
                url="https://4mobile.kz/ip16",
                price=530000.0,
                old_price=570000.0,
                condition=Condition.NEW,
                city="Алматы",
                observed_at=NOW_ISO,
            )
        )

        # Offer 2: Mechta NEW in Astana - 550,000 KZT
        self.offer_repo.save_or_update(
            Offer(
                id="off_2",
                product_id=self.prod_iphone.id,
                seller_id=self.s_mechta.id,
                channel_id=self.ch_mechta_web.id,
                external_sku="SKU-MECHTA-IP16",
                url="https://mechta.kz/ip16",
                price=550000.0,
                old_price=560000.0,
                condition=Condition.NEW,
                city="Астана",
                observed_at=NOW_ISO,
            )
        )

        # Offer 3: iSpace NEW in Все (Nationwide) - 560,000 KZT
        self.offer_repo.save_or_update(
            Offer(
                id="off_3",
                product_id=self.prod_iphone.id,
                seller_id=self.s_ispace.id,
                channel_id=self.ch_ispace_web.id,
                external_sku="SKU-ISPACE-IP16",
                url="https://ispace.kz/ip16",
                price=560000.0,
                condition=Condition.NEW,
                city="Все",
                observed_at=NOW_ISO,
            )
        )

        # Offer 4: Apple Resale USED in Astana - 470,000 KZT
        self.offer_repo.save_or_update(
            Offer(
                id="off_4",
                product_id=self.prod_iphone.id,
                seller_id=self.s_resale.id,
                channel_id=self.ch_resale_tg.id,
                external_sku="SKU-TG-IP16",
                url="https://t.me/resale/ip16",
                price=470000.0,
                condition=Condition.USED,
                city="Астана",
                observed_at=NOW_ISO,
            )
        )

        # Offer 5: Mechta RTX 5090 NEW in Astana - 1,200,000 KZT
        self.offer_repo.save_or_update(
            Offer(
                id="off_5",
                product_id=self.prod_rtx.id,
                seller_id=self.s_mechta.id,
                channel_id=self.ch_mechta_web.id,
                external_sku="SKU-MECHTA-5090",
                url="https://mechta.kz/rtx5090",
                price=1200000.0,
                condition=Condition.NEW,
                city="Астана",
                observed_at=NOW_ISO,
            )
        )

    def tearDown(self):
        self.conn.close()

    def test_multi_merchant_aggregation(self):
        """Searching 'iphone 16 pro' aggregates all 4 sellers into a single canonical product card."""
        res: SearchResponse2 = self.search_core.search("iphone 16 pro")

        self.assertEqual(res.outcome, SearchOutcome.FOUND)
        self.assertEqual(res.total_products, 1)
        self.assertEqual(res.total_offers, 4)

        card: ProductSearchResult = res.items[0]
        self.assertEqual(card.product.id, "prod_ip16p")
        self.assertEqual(card.min_price, 470000.0)
        self.assertEqual(card.max_price, 560000.0)
        self.assertEqual(card.sellers_count, 4)
        self.assertEqual(card.offers_count, 4)
        self.assertTrue(card.has_new)
        self.assertTrue(card.has_used)

        # Best offer is the lowest price (Apple Resale, 470k)
        self.assertEqual(card.best_offer.price, 470000.0)
        self.assertEqual(card.best_offer.seller_name, "Apple Resale Almaty")

    def test_city_filter(self):
        """Searching in Astana filters out offers strictly bound to Almaty."""
        res: SearchResponse2 = self.search_core.search("iphone 16 pro астана")

        self.assertEqual(res.total_products, 1)
        card = res.items[0]
        # Offers matching Astana: Mechta (Astana), iSpace (Все), Resale (Astana) -> 3 offers
        self.assertEqual(card.offers_count, 3)
        self.assertEqual(card.sellers_count, 3)
        # 4mobile from Almaty must be excluded
        seller_names = [o.seller_name for o in card.offers]
        self.assertNotIn("4mobile", seller_names)
        self.assertIn("Мечта", seller_names)
        self.assertIn("iSpace Apple Premium", seller_names)

    def test_condition_filter_used_vs_new(self):
        """Condition parsing filters down to either used or new offers."""
        # Search used
        res_used: SearchResponse2 = self.search_core.search("iphone 16 pro бу")
        self.assertEqual(res_used.total_products, 1)
        card_used = res_used.items[0]
        self.assertEqual(card_used.offers_count, 1)
        self.assertEqual(card_used.best_offer.condition, Condition.USED)
        self.assertEqual(card_used.min_price, 470000.0)

        # Search new
        res_new: SearchResponse2 = self.search_core.search("iphone 16 pro новый")
        card_new = res_new.items[0]
        self.assertEqual(card_new.offers_count, 3)
        self.assertEqual(card_new.min_price, 530000.0)
        for off in card_new.offers:
            self.assertEqual(off.condition, Condition.NEW)

    def test_price_range_filtering(self):
        """Search with 'до 535000' keeps only offers within budget."""
        res: SearchResponse2 = self.search_core.search("iphone 16 pro до 535000")
        card = res.items[0]
        # Only Apple Resale (470k) and 4mobile (530k) are <= 535,000
        self.assertEqual(card.offers_count, 2)
        prices = [o.price for o in card.offers]
        self.assertEqual(prices, [470000.0, 530000.0])

    def test_sorting_modes(self):
        """Verifies sorting modes across products."""
        # 1. Price ASC: iPhone (min 470k) before RTX 5090 (min 1.2M)
        res_asc = self.search_core.search("", sort_by="price_asc")
        self.assertEqual(res_asc.items[0].product.id, "prod_ip16p")
        self.assertEqual(res_asc.items[1].product.id, "prod_rtx5090")

        # 2. Price DESC: RTX 5090 first
        res_desc = self.search_core.search("", sort_by="price_desc")
        self.assertEqual(res_desc.items[0].product.id, "prod_rtx5090")
        self.assertEqual(res_desc.items[1].product.id, "prod_ip16p")

        # 3. Sellers count: iPhone (4 sellers) before RTX (1 seller)
        res_sellers = self.search_core.search("", sort_by="sellers_count")
        self.assertEqual(res_sellers.items[0].product.id, "prod_ip16p")
        self.assertEqual(res_sellers.items[0].sellers_count, 4)

    def test_search_not_found_kpi(self):
        """Unknown product returns NOT_FOUND with zero items."""
        res = self.search_core.search("космический корабль enterprise")
        self.assertEqual(res.outcome, SearchOutcome.NOT_FOUND)
        self.assertEqual(res.total_products, 0)
        self.assertEqual(len(res.items), 0)

    # -------------------------------------------------------------------------
    # Codex Audit Regression Tests (C-E11-01 .. C-E11-05)
    # -------------------------------------------------------------------------

    def test_audit_c_e11_01_canonical_query_russian_synonyms(self):
        """C-E11-01: 'айфон 16 про 256 бу астана' finds Apple iPhone 16 Pro 256GB Black Titanium."""
        res = self.search_core.search("айфон 16 про 256 бу астана")

        self.assertEqual(res.outcome, SearchOutcome.FOUND)
        self.assertEqual(res.parsed_query.detected_city, "Астана")
        self.assertEqual(res.parsed_query.condition, Condition.USED)
        self.assertEqual(res.parsed_query.attributes.get("storage"), "256gb")
        self.assertEqual(len(res.items), 1)

        card = res.items[0]
        self.assertEqual(card.product.id, "prod_ip16p")
        self.assertEqual(card.best_offer.condition, Condition.USED)
        self.assertEqual(card.best_offer.city, "Астана")
        self.assertEqual(card.best_offer.price, 470000.0)

    def test_audit_c_e11_02_fts_auto_rebuild_on_schema_init(self):
        """C-E11-02: FTS table automatically syncs existing rows when schema v2 is initialized."""
        raw_conn = sqlite3.connect(":memory:")
        raw_conn.row_factory = sqlite3.Row
        # Create canonical_products without FTS/triggers
        raw_conn.execute("""
            CREATE TABLE canonical_products (
                id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                brand TEXT,
                model TEXT,
                attributes_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        raw_conn.execute("""
            INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, created_at, updated_at)
            VALUES ('old_p1', 'apple_iphone_16_pro_256', 'Apple iPhone 16 Pro 256GB Black Titanium', 'Смартфоны', 'Apple', 'iPhone 16 Pro', '2026-01-01', '2026-01-01');
        """)
        raw_conn.commit()

        # Run init_schema_v2 on this connection
        init_schema_v2(raw_conn)

        # Without any manual rebuild call, FTS5 MATCH must find old_p1
        row = raw_conn.execute("SELECT * FROM canonical_products_fts WHERE canonical_products_fts MATCH 'iphone'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["canonical_key"], "apple_iphone_16_pro_256")
        self.assertEqual(row["title"], "Apple iPhone 16 Pro 256GB Black Titanium")
        raw_conn.close()

    def test_audit_c_e11_03_out_of_stock_and_stale_offers_excluded_from_best_deal(self):
        """C-E11-03: Out-of-stock and stale offers are excluded from best_offer and min_price."""
        p_fresh = CanonicalProduct(
            id="prod_fresh_test",
            canonical_key="test_freshness_prod",
            title="Fresh Test Gadget 100",
            category="Гаджеты",
            brand="TestBrand",
            model="Gadget 100",
        )
        self.product_repo.save_or_update(p_fresh)

        # Offer A: 100k, but OUT_OF_STOCK
        self.offer_repo.save_or_update(
            Offer(
                id="off_oos",
                product_id=p_fresh.id,
                seller_id=self.s_mechta.id,
                channel_id=self.ch_mechta_web.id,
                external_sku="SKU-OOS",
                url="https://mechta.kz/oos",
                price=100000.0,
                condition=Condition.NEW,
                availability=Availability.OUT_OF_STOCK,
                observed_at=NOW_ISO,
            )
        )
        # Offer B: 150k, IN_STOCK but STALE (observed in 2020)
        self.offer_repo.save_or_update(
            Offer(
                id="off_stale",
                product_id=p_fresh.id,
                seller_id=self.s_4mobile.id,
                channel_id=self.ch_4mobile_web.id,
                external_sku="SKU-STALE",
                url="https://4mobile.kz/stale",
                price=150000.0,
                condition=Condition.NEW,
                availability=Availability.IN_STOCK,
                observed_at="2020-01-01T00:00:00",
            )
        )
        # Offer C: 200k, IN_STOCK and FRESH
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.offer_repo.save_or_update(
            Offer(
                id="off_valid",
                product_id=p_fresh.id,
                seller_id=self.s_ispace.id,
                channel_id=self.ch_ispace_web.id,
                external_sku="SKU-VALID",
                url="https://ispace.kz/valid",
                price=200000.0,
                condition=Condition.NEW,
                availability=Availability.IN_STOCK,
                observed_at=now_iso,
            )
        )

        res = self.search_core.search("Fresh Test Gadget")
        self.assertEqual(res.total_products, 1)
        card = res.items[0]
        # Best offer must be the 200k valid offer, NOT the 100k (oos) or 150k (stale)
        self.assertIsNotNone(card.best_offer)
        self.assertEqual(card.best_offer.id, "off_valid")
        self.assertEqual(card.best_offer.price, 200000.0)
        self.assertEqual(card.min_price, 200000.0)
        self.assertTrue(card.has_purchasable_offers)

        # Now test product where ALL offers are out of stock
        p_all_oos = CanonicalProduct(
            id="prod_all_oos",
            canonical_key="test_all_oos_prod",
            title="All Out of Stock Device",
            category="Гаджеты",
            brand="TestBrand",
            model="Device OOS",
        )
        self.product_repo.save_or_update(p_all_oos)
        self.offer_repo.save_or_update(
            Offer(
                id="off_only_oos",
                product_id=p_all_oos.id,
                seller_id=self.s_mechta.id,
                channel_id=self.ch_mechta_web.id,
                external_sku="SKU-ONLY-OOS",
                url="https://mechta.kz/only_oos",
                price=50000.0,
                condition=Condition.NEW,
                availability=Availability.OUT_OF_STOCK,
                observed_at=now_iso,
            )
        )
        res_oos = self.search_core.search("All Out of Stock Device")
        card_oos = res_oos.items[0]
        self.assertIsNone(card_oos.best_offer)
        self.assertIsNone(card_oos.min_price)
        self.assertFalse(card_oos.has_purchasable_offers)

    def test_audit_c_e11_04_limit_with_other_city_offers(self):
        """C-E11-04: EXISTS clause filters at SQL query level before LIMIT, preventing zero results."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        # Create 15 products with offers exclusively in Shymkent
        for i in range(15):
            p_shym = CanonicalProduct(
                id=f"prod_shym_{i}",
                canonical_key=f"shymkent_item_{i}",
                title=f"Special Laptop Shymkent {i}",
                category="Ноутбуки",
                brand="ShymBrand",
                model=f"Model {i}",
            )
            self.product_repo.save_or_update(p_shym)
            self.offer_repo.save_or_update(
                Offer(
                    id=f"off_shym_{i}",
                    product_id=p_shym.id,
                    seller_id=self.s_mechta.id,
                    channel_id=self.ch_mechta_web.id,
                    external_sku=f"SKU-SHYM-{i}",
                    url=f"https://mechta.kz/shym/{i}",
                    price=100000.0 + i,
                    city="Шымкент",
                    condition=Condition.NEW,
                    observed_at=now_iso,
                )
            )

        # Create 1 product with offer in Astana
        p_ast = CanonicalProduct(
            id="prod_ast_target",
            canonical_key="astana_target_laptop",
            title="Special Laptop Astana Target",
            category="Ноутбуки",
            brand="TargetBrand",
            model="Model Target",
        )
        self.product_repo.save_or_update(p_ast)
        self.offer_repo.save_or_update(
            Offer(
                id="off_ast_target",
                product_id=p_ast.id,
                seller_id=self.s_mechta.id,
                channel_id=self.ch_mechta_web.id,
                external_sku="SKU-AST-TARGET",
                url="https://mechta.kz/ast_target",
                price=99000.0,
                city="Астана",
                condition=Condition.NEW,
                observed_at=now_iso,
            )
        )

        # Search for "Special Laptop" in Astana with small limit=5
        res = self.search_core.search("Special Laptop", city="Астана", limit=5)
        prod_ids = [item.product.id for item in res.items]
        self.assertIn("prod_ast_target", prod_ids)

    def test_audit_c_e11_05_kpi_exact_model_and_error_handling(self):
        """C-E11-05: Exact model match yields FOUND even with 1 seller, model mismatch penalizes, and DB error yields ERROR."""
        # 1. Exact model match RTX 5090 (only 1 seller seeded: Mechta)
        res_5090 = self.search_core.search("RTX 5090")
        self.assertEqual(res_5090.outcome, SearchOutcome.FOUND)
        self.assertEqual(res_5090.items[0].product.id, "prod_rtx5090")

        # 2. Search RTX 5070 (does not exist in DB, but 5090 exists)
        res_5070 = self.search_core.search("RTX 5070")
        self.assertNotEqual(res_5070.outcome, SearchOutcome.FOUND)

        # 3. DB Error handling
        class FailingRepo:
            def search_products(self, *args, **kwargs):
                raise sqlite3.OperationalError("database disk image is malformed")

        failing_core = SearchCore(FailingRepo())
        res_err = failing_core.search("iphone")
        self.assertEqual(res_err.outcome, SearchOutcome.ERROR)
        self.assertEqual(len(res_err.items), 0)
        self.assertGreaterEqual(res_err.latency_ms, 0.0)


class TestSearchCoreV2HttpEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database import init_db
        import web.server as server
        self.server = server
        init_db()

    async def test_audit_c_e11_06_search_v2_endpoint(self):
        """C-E11-06: GET /api/v2/search returns 200 with structured JSON response."""
        req = make_mocked_request(
            "GET",
            "/api/v2/search?q=iphone&city=Астана",
            headers={"Host": "localhost:8080"}
        )
        resp = await self.server.search_v2_handler(req)
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.text)
        self.assertIn("outcome", data)
        self.assertIn("parsed_query", data)
        self.assertIn("items", data)
        self.assertEqual(data["parsed_query"]["detected_city"], "Астана")

    async def test_audit_c_e11_06_best_price_v2_flag_endpoint(self):
        """C-E11-06: GET /api/best-price?q=iphone&v2=1 returns 200 with v2 response."""
        req = make_mocked_request(
            "GET",
            "/api/best-price?q=iphone&v2=1&city=Астана",
            headers={"Host": "localhost:8080"}
        )
        resp = await self.server.best_price_handler(req)
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.text)
        self.assertIn("outcome", data)
        self.assertIn("parsed_query", data)
        self.assertIn("items", data)


if __name__ == "__main__":
    unittest.main()
