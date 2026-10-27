"""
Tests for Seller Page API endpoints (S2-E19):
- GET /api/v2/sellers
- GET /api/v2/seller/{id_or_slug}
"""
from tests import test_support  # noqa: F401 isolates DATA_DIR
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

from domain.models import CanonicalProduct, Channel, ChannelType, Condition, Offer, Seller
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import DDL_V2
from repositories.seller_repo import SellerRepository
import web.server as server


class SellerEndpointsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        conn = self._get_conn()
        conn.executescript(DDL_V2)

        self.seller_repo = SellerRepository(conn)
        self.channel_repo = ChannelRepository(conn)
        self.product_repo = ProductRepository(conn)
        self.offer_repo = OfferRepository(conn)

        # Seed seller
        self.seller = Seller(
            id="seller_technogroup",
            slug="technogroup",
            name="TechnoGroup KZ",
            legal_name="ТОО TechnoGroup",
            domain="technogroup.kz",
            phone="+7 777 123 4567",
            rating=4.9,
            is_active=True,
        )
        self.seller_repo.save_or_update(self.seller)

        # Seed channels
        self.chan_web = Channel(
            id="chan_tg_web",
            seller_id=self.seller.id,
            channel_type=ChannelType.WEBSITE,
            name="Сайт TechnoGroup",
            base_url="https://technogroup.kz",
        )
        self.chan_kaspi = Channel(
            id="chan_tg_kaspi",
            seller_id=self.seller.id,
            channel_type=ChannelType.KASPI,
            name="TechnoGroup Kaspi",
            external_store_id="tg_kaspi",
        )
        self.channel_repo.save_or_update(self.chan_web)
        self.channel_repo.save_or_update(self.chan_kaspi)

        # Seed product & offers
        self.product = CanonicalProduct(
            id="prod_macbook_m3",
            canonical_key="apple-macbook-pro-14-m3",
            title="Apple MacBook Pro 14 M3 512GB Space Gray",
            category="laptops",
            brand="Apple",
        )
        self.product_repo.save_or_update(self.product)

        self.off_web = Offer(
            id="off_mac_web",
            product_id=self.product.id,
            seller_id=self.seller.id,
            channel_id=self.chan_web.id,
            external_sku="SKU-MBP-W",
            url="https://technogroup.kz/macbook",
            price=820000.0,
            old_price=850000.0,
            condition=Condition.NEW,
            city="Алматы",
        )
        self.off_kaspi = Offer(
            id="off_mac_kaspi",
            product_id=self.product.id,
            seller_id=self.seller.id,
            channel_id=self.chan_kaspi.id,
            external_sku="SKU-MBP-K",
            url="https://kaspi.kz/p/macbook",
            price=870000.0,
            condition=Condition.NEW,
            city="Алматы",
        )
        self.offer_repo.save_or_update(self.off_web)
        self.offer_repo.save_or_update(self.off_kaspi)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    async def test_list_sellers_endpoint(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            req = make_mocked_request("GET", "/api/v2/sellers?q=Techno")
            resp = await server.list_sellers_v2_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)
            self.assertTrue(data["ok"])
            self.assertEqual(data["total"], 1)
            self.assertEqual(len(data["sellers"]), 1)
            seller_item = data["sellers"][0]
            self.assertEqual(seller_item["slug"], "technogroup")
            self.assertEqual(seller_item["active_offers_count"], 2)
            self.assertEqual(len(seller_item["channels"]), 2)

    async def test_list_sellers_filtering(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            # Non-existent search
            req = make_mocked_request("GET", "/api/v2/sellers?q=NonExistent")
            resp = await server.list_sellers_v2_handler(req)
            data = json.loads(resp.text)
            self.assertEqual(data["total"], 0)
            self.assertEqual(len(data["sellers"]), 0)

            # Filter by channel type
            req_kaspi = make_mocked_request("GET", "/api/v2/sellers?channel_type=kaspi")
            resp_kaspi = await server.list_sellers_v2_handler(req_kaspi)
            data_kaspi = json.loads(resp_kaspi.text)
            self.assertEqual(data_kaspi["total"], 1)

            req_ig = make_mocked_request("GET", "/api/v2/sellers?channel_type=instagram")
            resp_ig = await server.list_sellers_v2_handler(req_ig)
            data_ig = json.loads(resp_ig.text)
            self.assertEqual(data_ig["total"], 0)

    async def test_get_seller_by_slug(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            req = make_mocked_request("GET", "/api/v2/seller/technogroup", match_info={"id_or_slug": "technogroup"})
            resp = await server.get_seller_v2_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)
            self.assertTrue(data["ok"])
            self.assertEqual(data["seller"]["name"], "TechnoGroup KZ")
            self.assertEqual(data["stats"]["total_offers"], 2)
            self.assertEqual(data["stats"]["products_count"], 1)
            self.assertEqual(data["stats"]["min_price"], 820000.0)
            self.assertEqual(data["stats"]["max_price"], 870000.0)

            # Product multi-channel grouping check
            self.assertEqual(len(data["products"]), 1)
            prod = data["products"][0]
            self.assertEqual(prod["product_id"], self.product.id)
            self.assertEqual(len(prod["channels"]), 2)
            self.assertEqual(prod["channels"][0]["price"], 820000.0)
            self.assertEqual(prod["channels"][1]["price"], 870000.0)

    async def test_get_seller_by_id(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            req = make_mocked_request("GET", "/api/v2/seller/seller_technogroup", match_info={"id_or_slug": "seller_technogroup"})
            resp = await server.get_seller_v2_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)
            self.assertTrue(data["ok"])
            self.assertEqual(data["seller"]["id"], "seller_technogroup")

    async def test_get_seller_not_found(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            req = make_mocked_request("GET", "/api/v2/seller/unknown_seller", match_info={"id_or_slug": "unknown_seller"})
            resp = await server.get_seller_v2_handler(req)
            self.assertEqual(resp.status, 404)
            data = json.loads(resp.text)
            self.assertFalse(data["ok"])
            self.assertIn("не найден", data["error"])

    async def test_get_seller_condition_filter(self):
        with patch("database.get_connection", side_effect=self._get_conn):
            # Used filter -> 0 offers
            req = make_mocked_request("GET", "/api/v2/seller/technogroup?condition=used", match_info={"id_or_slug": "technogroup"})
            resp = await server.get_seller_v2_handler(req)
            data = json.loads(resp.text)
            self.assertEqual(data["pagination"]["total_offers"], 0)
            self.assertEqual(len(data["products"]), 0)


if __name__ == "__main__":
    unittest.main()
