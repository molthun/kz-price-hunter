"""
Tests for 4-Level Monitoring Center 2.0 endpoint (S2-E23, S2-E24) and Search Platform 2.0 UI integrations.
"""
from tests import test_support  # noqa: F401 isolates DATA_DIR
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

from domain.models import CanonicalProduct, Channel, ChannelType, Offer, Seller
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import init_schema_v2, rebuild_canonical_products_fts
from repositories.seller_repo import SellerRepository
import web.server as server


class MonitoringV2EndpointTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        conn = self._get_conn()
        init_schema_v2(conn)

        self.seller_repo = SellerRepository(conn)
        self.channel_repo = ChannelRepository(conn)
        self.product_repo = ProductRepository(conn)
        self.offer_repo = OfferRepository(conn)

        # Seed sellers: 2 independent sellers
        self.seller = Seller(
            id="seller_test",
            slug="test_seller",
            name="Test Seller KZ",
            is_active=True,
        )
        self.seller2 = Seller(
            id="seller_test2",
            slug="test_seller2",
            name="Second Seller KZ",
            is_active=True,
        )
        self.seller_repo.save_or_update(self.seller)
        self.seller_repo.save_or_update(self.seller2)

        # Seed channels: Direct + Kaspi
        self.chan_web = Channel(
            id="chan_web_1",
            seller_id=self.seller.id,
            channel_type=ChannelType.WEBSITE,
            name="Сайт Продавца",
        )
        self.chan_kaspi = Channel(
            id="chan_kaspi_1",
            seller_id=self.seller.id,
            channel_type=ChannelType.KASPI,
            name="Kaspi Магазин",
        )
        self.chan_web2 = Channel(
            id="chan_web_2",
            seller_id=self.seller2.id,
            channel_type=ChannelType.WEBSITE,
            name="Сайт Второго Продавца",
        )
        self.channel_repo.save_or_update(self.chan_web)
        self.channel_repo.save_or_update(self.chan_kaspi)
        self.channel_repo.save_or_update(self.chan_web2)

        # Seed product & offers with price discrepancy
        self.prod = CanonicalProduct(
            id="prod_1",
            canonical_key="apple_iphone_16",
            title="Apple iPhone 16 128GB",
            brand="Apple",
            model="iphone 16",
            category="smartphones",
        )
        self.product_repo.save_or_update(self.prod)

        self.off_web = Offer(
            id="off_web_1",
            product_id=self.prod.id,
            seller_id=self.seller.id,
            channel_id=self.chan_web.id,
            external_sku="IPHONE-WEB",
            url="https://test.kz/iphone",
            price=450000.0,
            is_active=True,
        )
        self.off_kaspi = Offer(
            id="off_kaspi_1",
            product_id=self.prod.id,
            seller_id=self.seller.id,
            channel_id=self.chan_kaspi.id,
            external_sku="IPHONE-KASPI",
            url="https://kaspi.kz/iphone",
            price=499000.0,
            is_active=True,
        )
        self.off_web2 = Offer(
            id="off_web_2",
            product_id=self.prod.id,
            seller_id=self.seller2.id,
            channel_id=self.chan_web2.id,
            external_sku="IPHONE-WEB-2",
            url="https://test2.kz/iphone",
            price=460000.0,
            is_active=True,
        )
        self.offer_repo.save_or_update(self.off_web)
        self.offer_repo.save_or_update(self.off_kaspi)
        self.offer_repo.save_or_update(self.off_web2)

        rebuild_canonical_products_fts(conn)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def test_monitoring_overview_v2_admin_success(self):
        """GET /api/v2/monitoring/overview returns 200 with 4 levels for admin."""
        req = make_mocked_request(
            "GET",
            "/api/v2/monitoring/overview",
            headers={"Host": "localhost:8080"}
        )
        req["user"] = {"id": 1, "is_admin": True}

        with patch("database.get_connection", side_effect=self._get_conn), \
             patch("auth.is_admin", return_value=True):
            resp = await server.monitoring_overview_v2_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)

            self.assertTrue(data.get("ok"))
            self.assertIn("seller_level", data)
            self.assertIn("source_level", data)
            self.assertIn("channel_level", data)
            self.assertIn("offer_level", data)

            # Check Seller Level
            sl = data["seller_level"]
            self.assertGreaterEqual(sl["total_sellers"], 1)
            self.assertGreaterEqual(sl["active_sellers"], 1)

            # Check Channel Level
            cl = data["channel_level"]
            self.assertIn("active_channels_by_type", cl)
            # Must detect cross-channel discrepancy between website (450k) and kaspi (499k)
            self.assertGreaterEqual(cl["cross_channel_discrepancies_count"], 1)
            disc = cl["cross_channel_discrepancies"][0]
            self.assertEqual(disc["seller_name"], "Test Seller KZ")
            self.assertEqual(disc["min_channel_price"], 450000.0)
            self.assertEqual(disc["max_channel_price"], 499000.0)
            self.assertEqual(disc["price_diff"], 49000.0)

            # Check Offer Level
            ol = data["offer_level"]
            self.assertGreaterEqual(ol["total_active_offers"], 2)

    async def test_monitoring_overview_v2_forbidden_for_non_admin(self):
        """GET /api/v2/monitoring/overview requires admin role."""
        req = make_mocked_request(
            "GET",
            "/api/v2/monitoring/overview",
            headers={"Host": "localhost:8080"}
        )
        req["user"] = {"id": 2, "is_admin": False}

        with patch("auth.is_admin", return_value=False):
            resp = await server.monitoring_overview_v2_handler(req)
            self.assertEqual(resp.status, 403)

    async def test_best_price_v2_applies_category_and_brand_filters(self):
        """GET /api/best-price?v2=1 passes category and brand to SearchCore."""
        req = make_mocked_request(
            "GET",
            "/api/best-price?q=iphone+16&v2=1&category=smartphones&brand=apple",
            headers={"Host": "localhost:8080"}
        )

        with patch("database.get_connection", side_effect=self._get_conn):
            resp = await server.best_price_handler(req)
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.text)
            self.assertTrue(data.get("v2"))
            self.assertEqual(data.get("outcome"), "FOUND")
            # Category should NOT be in unsupported_filters
            unsupported = data.get("unsupported_filters", [])
            self.assertNotIn("категория", unsupported)
            self.assertGreaterEqual(len(data.get("items", [])), 1)

    def test_monitoring_html_includes_platform_v2_section(self):
        """Verify monitoring.html includes the Platform 2.0 section tab and views."""
        html_path = os.path.join(os.path.dirname(__file__), "../../web/templates/monitoring.html")
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("platform_v2", content)
        self.assertIn("Платформа 2.0", content)
        self.assertIn("loadPlatformV2", content)
        self.assertIn("/api/v2/monitoring/overview", content)


if __name__ == "__main__":
    unittest.main()
