"""
Tests for Search Results: Multi-Merchant Aggregation, Seller Grouping & Brand Authority (Stage 18).
Verifies:
1. Multi-channel offers from a single merchant are grouped into SellerOffersGroup (no duplicate shops).
2. ProductSearchResult tiers: new_sellers_count, used_sellers_count, best_new_price, best_used_price.
3. Brand authority and Brand facets calculation (brand_facets) in SearchResponse2.
4. HTTP API GET /api/v2/search with brand parameter and seller grouping payload.
"""

from tests import test_support  # must be first
import datetime
import json
import sqlite3
import unittest
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
from domain.search_core import (
    ProductSearchResult,
    SearchCore,
    SearchOutcome,
    SellerOffersGroup,
)
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)
from web.server import search_v2_handler


class TestSearchResultsSellerGrouping(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)

        # 1. Продавец 1: 4mobile с 3 каналами (Direct, Kaspi, Forte)
        s_4m = Seller(id="s_4mobile", slug="4mobile", name="4mobile", rating=4.9)
        self.seller_repo.save_or_update(s_4m)
        ch_direct = Channel(id="ch_4m_dir", seller_id="s_4mobile", channel_type=ChannelType.DIRECT, name="4mobile Direct")
        ch_kaspi = Channel(id="ch_4m_kaspi", seller_id="s_4mobile", channel_type=ChannelType.KASPI, name="4mobile Kaspi")
        ch_forte = Channel(id="ch_4m_forte", seller_id="s_4mobile", channel_type=ChannelType.FORTE, name="4mobile Forte")
        self.channel_repo.save_or_update(ch_direct)
        self.channel_repo.save_or_update(ch_kaspi)
        self.channel_repo.save_or_update(ch_forte)

        # 2. Продавец 2: Technodom с 1 каналом
        s_td = Seller(id="s_technodom", slug="technodom", name="Technodom", rating=4.8)
        self.seller_repo.save_or_update(s_td)
        ch_td = Channel(id="ch_td_site", seller_id="s_technodom", channel_type=ChannelType.WEBSITE, name="Technodom Web")
        self.channel_repo.save_or_update(ch_td)

        # 3. Продавец 3: Частный продавец (Б/у, Classifieds)
        s_priv = Seller(id="s_private_1", slug="priv-seller", name="Частное лицо (Азамат)", is_active=True)
        self.seller_repo.save_or_update(s_priv)
        ch_olx = Channel(id="ch_olx_1", seller_id="s_private_1", channel_type=ChannelType.CLASSIFIEDS, name="OLX Частное")
        self.channel_repo.save_or_update(ch_olx)

        # Создаем канонический товар
        self.prod_iphone = CanonicalProduct(
            id="p_ip16",
            canonical_key="key_iphone16pro",
            title="Apple iPhone 16 Pro 256GB Black",
            brand="Apple",
            model="iPhone 16 Pro",
            category="phones"
        )
        self.product_repo.save_or_update(self.prod_iphone)

        # Предложения 4mobile: 3 разных канала для одного товара
        self.offer_repo.save_or_update(Offer(
            id="off_4m_dir", product_id="p_ip16", channel_id="ch_4m_dir", seller_id="s_4mobile",
            external_sku="sku_4m_1", url="https://4mobile.kz/p1", price=579990,
            condition=Condition.NEW, availability=Availability.IN_STOCK, city="Алматы"
        ))
        self.offer_repo.save_or_update(Offer(
            id="off_4m_kaspi", product_id="p_ip16", channel_id="ch_4m_kaspi", seller_id="s_4mobile",
            external_sku="sku_4m_2", url="https://kaspi.kz/p2", price=595000,
            condition=Condition.NEW, availability=Availability.IN_STOCK, city="Алматы"
        ))
        self.offer_repo.save_or_update(Offer(
            id="off_4m_forte", product_id="p_ip16", channel_id="ch_4m_forte", seller_id="s_4mobile",
            external_sku="sku_4m_3", url="https://forte.kz/p3", price=605000,
            condition=Condition.NEW, availability=Availability.IN_STOCK, city="Алматы"
        ))

        # Предложение Technodom: Новый товар
        self.offer_repo.save_or_update(Offer(
            id="off_td", product_id="p_ip16", channel_id="ch_td_site", seller_id="s_technodom",
            external_sku="sku_td_1", url="https://technodom.kz/p4", price=589990,
            condition=Condition.NEW, availability=Availability.IN_STOCK, city="Алматы"
        ))

        # Предложение частника: Б/у товар
        self.offer_repo.save_or_update(Offer(
            id="off_priv", product_id="p_ip16", channel_id="ch_olx_1", seller_id="s_private_1",
            external_sku="sku_olx_1", url="https://olx.kz/p5", price=390000,
            condition=Condition.USED, availability=Availability.IN_STOCK, city="Алматы"
        ))

    def tearDown(self):
        self.conn.close()

    def test_seller_grouping_in_search_result(self):
        resp = self.core.search("iPhone 16 Pro")
        self.assertEqual(resp.total_products, 1)
        item: ProductSearchResult = resp.items[0]

        # Всего предложений 5, но уникальных продавцов ровно 3
        self.assertEqual(item.offers_count, 5)
        self.assertEqual(item.sellers_count, 3)

        # Проверяем структуру sellers
        self.assertEqual(len(item.sellers), 3)

        # Находим группу 4mobile
        group_4m = next((s for s in item.sellers if s.seller_id == "s_4mobile"), None)
        self.assertIsNotNone(group_4m)
        self.assertEqual(group_4m.seller_name, "4mobile")
        self.assertEqual(group_4m.offers_count, 3)
        self.assertEqual(group_4m.channels_count, 3)
        self.assertIn("direct", group_4m.channels_available)
        self.assertIn("kaspi", group_4m.channels_available)
        self.assertIn("forte", group_4m.channels_available)
        self.assertEqual(group_4m.min_price, 579990)
        self.assertEqual(group_4m.max_price, 605000)
        self.assertEqual(group_4m.best_offer.channel_type, "direct")

    def test_product_condition_tiers_and_sellers_count(self):
        resp = self.core.search("iPhone 16 Pro")
        item: ProductSearchResult = resp.items[0]

        # Ярусы цен
        self.assertTrue(item.has_new)
        self.assertTrue(item.has_used)
        self.assertEqual(item.best_new_price, 579990)
        self.assertEqual(item.best_used_price, 390000)

        # Количество предложений по ярусам
        self.assertEqual(item.new_offers_count, 4)  # 3 от 4mobile + 1 от Technodom
        self.assertEqual(item.used_offers_count, 1)  # 1 б/у от частника

        # Количество продавцов по ярусам (S2-E18)
        self.assertEqual(item.new_sellers_count, 2)  # 4mobile, Technodom
        self.assertEqual(item.used_sellers_count, 1)  # частник


class TestBrandAuthorityAndFacets(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.core = SearchCore(self.search_repo)

        s = Seller(id="s_main", slug="main-store", name="Main Store")
        self.seller_repo.save_or_update(s)
        ch = Channel(id="ch_main", seller_id="s_main", channel_type=ChannelType.WEBSITE, name="Main Web")
        self.channel_repo.save_or_update(ch)

        # Создаем товары разных брендов
        p_apple = CanonicalProduct(id="p_app", canonical_key="k_app", title="Apple MacBook Pro 14", brand="Apple", category="laptops")
        p_samsung = CanonicalProduct(id="p_sam", canonical_key="k_sam", title="Samsung Galaxy Book 3", brand="Samsung", category="laptops")
        p_asus = CanonicalProduct(id="p_asu", canonical_key="k_asu", title="ASUS Zenbook 14", brand="ASUS", category="laptops")

        self.product_repo.save_or_update(p_apple)
        self.product_repo.save_or_update(p_samsung)
        self.product_repo.save_or_update(p_asus)

        self.offer_repo.save_or_update(Offer(id="o1", product_id="p_app", channel_id="ch_main", seller_id="s_main", price=900000, external_sku="sk1", url="http://1"))
        self.offer_repo.save_or_update(Offer(id="o2", product_id="p_sam", channel_id="ch_main", seller_id="s_main", price=750000, external_sku="sk2", url="http://2"))
        self.offer_repo.save_or_update(Offer(id="o3", product_id="p_asu", channel_id="ch_main", seller_id="s_main", price=620000, external_sku="sk3", url="http://3"))

    def tearDown(self):
        self.conn.close()

    def test_brand_facets_in_response(self):
        resp = self.core.search("")
        self.assertEqual(resp.total_products, 3)
        self.assertTrue(len(resp.brand_facets) >= 3)

        b_map = {f["brand"]: f["count"] for f in resp.brand_facets}
        self.assertIn("Apple", b_map)
        self.assertIn("Samsung", b_map)
        self.assertIn("Asus", b_map)

    def test_search_filtering_by_brand(self):
        # Строгий фильтр по бренду Apple
        resp_apple = self.core.search("", brand="Apple")
        self.assertEqual(resp_apple.total_products, 1)
        self.assertEqual(resp_apple.items[0].product.brand, "Apple")

        # Фильтр по алиасу бренда на русском: "самсунг" -> Samsung
        resp_sam = self.core.search("", brand="самсунг")
        self.assertEqual(resp_sam.total_products, 1)
        self.assertEqual(resp_sam.items[0].product.brand, "Samsung")


class TestSearchHttpApiStage18(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import os
        import tempfile
        self.tmp_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.tmp_fd)

        conn = sqlite3.connect(self.db_path)
        init_schema_v2(conn)

        s_repo = SellerRepository(conn)
        s_repo.save_or_update(Seller(id="s1", slug="seller1", name="Kaspi & Web Seller"))
        ch_repo = ChannelRepository(conn)
        ch_repo.save_or_update(Channel(id="ch_dir", seller_id="s1", channel_type=ChannelType.DIRECT, name="Web"))
        ch_repo.save_or_update(Channel(id="ch_ksp", seller_id="s1", channel_type=ChannelType.KASPI, name="Kaspi"))

        p_repo = ProductRepository(conn)
        p_repo.save_or_update(CanonicalProduct(id="p1", canonical_key="k1", title="Sony PlayStation 5", brand="Sony", category="consoles"))

        o_repo = OfferRepository(conn)
        o_repo.save_or_update(Offer(id="o_dir", product_id="p1", channel_id="ch_dir", seller_id="s1", price=250000, external_sku="s1", url="http://1", city="Алматы"))
        o_repo.save_or_update(Offer(id="o_ksp", product_id="p1", channel_id="ch_ksp", seller_id="s1", price=260000, external_sku="s2", url="http://2", city="Алматы"))

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

    async def test_search_api_with_brand_and_sellers_grouping(self):
        req = make_mocked_request("GET", "/api/v2/search?q=PlayStation&brand=Sony")
        resp = await search_v2_handler(req)
        self.assertEqual(resp.status, 200)
        body = json.loads(resp.text)
        self.assertEqual(body["total_products"], 1)

        # Проверяем наличие brand_facets
        self.assertIn("brand_facets", body)
        self.assertTrue(len(body["brand_facets"]) > 0)
        self.assertEqual(body["brand_facets"][0]["brand"], "Sony")

        # Проверяем seller grouping в ответе
        item = body["items"][0]
        self.assertEqual(item["sellers_count"], 1)
        self.assertIn("sellers", item)
        self.assertEqual(len(item["sellers"]), 1)
        s_group = item["sellers"][0]
        self.assertEqual(s_group["seller_name"], "Kaspi & Web Seller")
        self.assertEqual(s_group["channels_count"], 2)
        self.assertEqual(s_group["min_price"], 250000)


if __name__ == "__main__":
    unittest.main()
