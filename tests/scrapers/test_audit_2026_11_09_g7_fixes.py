"""Regression tests for G7-1 code audit findings (G7-A01 .. G7-A05).

Audit reference: docs/AUDIT_2026-11-09_G7_CODEX.md.
"""
from tests import test_support  # noqa: F401 isolates DATA_DIR
import asyncio
import json
import sqlite3
import unittest
from unittest.mock import AsyncMock, Mock, patch

from domain.adapter import prepare_legacy_offer_v2, write_legacy_offers_v2
from domain.channel_pricing import build_channel_matrix
from domain.channel_sync import match_or_create_canonical_product, sync_channel_showcase, upsert_showcase_offer
from domain.models import ChannelType, Offer, Seller
from scrapers.base import UnconfirmedEnd
from scrapers.ozon import OzonScraper
from scrapers.showcase_scanners import OzonShowcaseScanner, WildberriesShowcaseScanner
from scrapers.wildberries import WildberriesScraper
from tests.scrapers.test_showcase_scanners import create_test_db


def make_wb_item(sku=123, seller=456):
    return {
        "id": sku,
        "name": f"Samsung Galaxy S24 {sku}",
        "supplierId": seller,
        "supplier": f"Synthetic seller {seller}",
        "salePriceU": 10000000,
    }


def make_ozon_item(sku=123, seller=456):
    return {
        "id": sku,
        "title": f"Samsung Galaxy S24 {sku}",
        "price": 100000,
        "sellerId": seller,
        "sellerName": f"Synthetic seller {seller}",
    }


class AuditG7FixesTest(unittest.IsolatedAsyncioTestCase):
    def test_g7_a01_marketplace_seller_identity_preserved(self):
        """G7-A01: Ensure distinct suppliers are not merged into a generic platform seller."""
        wb_payload = {"data": {"products": [make_wb_item(101, 456), make_wb_item(102, 789)]}}
        ozon_payload = {"widgetStates": {"searchResults": {"items": [make_ozon_item(201, 456), make_ozon_item(202, 789)]}}}

        wb_items = WildberriesScraper.parse_payload(wb_payload, "Смартфоны", "https://example.com", 1)
        ozon_items = OzonScraper.parse_payload(ozon_payload, "Смартфоны", "https://example.com", 1)

        wb_prep = [prepare_legacy_offer_v2(it) for it in wb_items]
        oz_prep = [prepare_legacy_offer_v2(it) for it in ozon_items]

        # Distinct seller IDs and names
        self.assertNotEqual(wb_prep[0]["seller_id"], wb_prep[1]["seller_id"])
        self.assertEqual(wb_prep[0]["seller_id"], "seller_mp_wildberries_456")
        self.assertEqual(wb_prep[1]["seller_id"], "seller_mp_wildberries_789")
        self.assertEqual(wb_prep[0]["seller_name"], "Synthetic seller 456")
        self.assertEqual(wb_prep[1]["seller_name"], "Synthetic seller 789")

        self.assertNotEqual(oz_prep[0]["seller_id"], oz_prep[1]["seller_id"])
        self.assertEqual(oz_prep[0]["seller_id"], "seller_mp_ozon_456")
        self.assertEqual(oz_prep[1]["seller_id"], "seller_mp_ozon_789")

        # In-memory persistence verification
        conn = create_test_db()
        write_legacy_offers_v2(conn, wb_prep, "2026-11-09T00:00:00+00:00")
        sellers = conn.execute("SELECT id, name FROM sellers WHERE id LIKE 'seller_mp_wildberries_%' ORDER BY id").fetchall()
        self.assertEqual(len(sellers), 2)
        self.assertEqual(sellers[0][0], "seller_mp_wildberries_456")
        self.assertEqual(sellers[1][0], "seller_mp_wildberries_789")

        identities = conn.execute(
            "SELECT seller_id, identity_type, identity_value FROM seller_identities WHERE identity_type='marketplace_seller_id' ORDER BY seller_id"
        ).fetchall()
        self.assertEqual(len(identities), 2)
        self.assertEqual(tuple(identities[0]), ("seller_mp_wildberries_456", "marketplace_seller_id", "wildberries:456"))
        self.assertEqual(tuple(identities[1]), ("seller_mp_wildberries_789", "marketplace_seller_id", "wildberries:789"))
        conn.close()

    def test_g7_a01_confirmed_seller_channel_matrix_savings(self):
        """G7-A01: Existing confirmed seller with direct channel matched by marketplace identity."""
        conn = create_test_db()
        # Seed confirmed merchant with website
        conn.execute("INSERT INTO sellers (id, slug, name, domain, created_at, updated_at) VALUES ('seller_alser', 'alser', 'Alser Computers', 'alser.kz', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('chan_alser_web', 'seller_alser', 'website', 'Alser Computers', '2026-01-01')")
        # Link marketplace identity
        conn.execute(
            "INSERT INTO seller_identities (id, seller_id, identity_type, identity_value, confidence, source, created_at) "
            "VALUES ('ident_wb_alser', 'seller_alser', 'marketplace_seller_id', 'wildberries:99999', 1.0, 'marketplace', '2026-01-01')"
        )

        # Scrape item from WB for supplier 99999
        wb_item = make_wb_item(555, 99999)
        wb_items = WildberriesScraper.parse_payload({"data": {"products": [wb_item]}}, "Смартфоны", "https://example.com", 1)
        wb_prep = [prepare_legacy_offer_v2(it) for it in wb_items]

        # Write to DB -> should resolve to seller_alser
        write_legacy_offers_v2(conn, wb_prep, "2026-11-09T00:00:00+00:00")
        offer = conn.execute("SELECT seller_id, channel_id FROM offers WHERE external_sku='wb_555'").fetchone()
        self.assertIsNotNone(offer)
        self.assertEqual(offer[0], "seller_alser")
        self.assertEqual(offer[1], "chan_seller_alser_wildberries")
        conn.close()

    async def test_g7_a02_numeric_id_routing_uses_declared_channel_type(self):
        """G7-A02: Numeric store ID without base_url must respect declared channel_type."""
        for platform in ("wildberries", "ozon"):
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch', 's1', ?, ?, '123456', '2026-01-01')", (platform, platform))

            with patch("domain.channel_sync.KaspiShowcaseScanner.scan_merchant_showcase", new_callable=AsyncMock) as kaspi_mock:
                with patch("scrapers.showcase_scanners.requests.get", return_value=Mock(status_code=200, json=Mock(return_value={"data": {"products": []}} if platform == "wildberries" else {"widgetStates": {}}))):
                    res = await sync_channel_showcase(conn, "ch")

            self.assertFalse(kaspi_mock.called)
            self.assertEqual(res["marketplace"], platform)
            self.assertTrue(res["ok"])
            conn.close()

    async def test_g7_a02_conflicting_configuration_rejected(self):
        """G7-A02: Conflicting channel_type and base_url must be rejected with clear error."""
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, created_at) VALUES ('ch', 's1', 'wildberries', 'WB', 'https://kaspi.kz/shop/m/123', '2026-01-01')")

        res = await sync_channel_showcase(conn, "ch")
        self.assertFalse(res["ok"])
        self.assertIn("Конфликт конфигурации", res["error"])
        conn.close()

    async def test_g7_a03_http_failure_reports_error(self):
        """G7-A03: HTTP 403/500 or JSON decode error must report failure and update channel status."""
        for cls in (WildberriesShowcaseScanner, OzonShowcaseScanner):
            platform = "wildberries" if cls is WildberriesShowcaseScanner else "ozon"
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch', 's1', ?, ?, '123456', '2026-01-01')", (platform, platform))

            with patch("scrapers.showcase_scanners.requests.get", return_value=Mock(status_code=403)):
                res = await sync_channel_showcase(conn, "ch")

            self.assertFalse(res["ok"])
            self.assertIn("403", res["error"])

            # Verify channel metadata contains error status
            meta_raw = conn.execute("SELECT metadata_json FROM channels WHERE id='ch'").fetchone()[0]
            meta = json.loads(meta_raw)
            self.assertEqual(meta["last_sync_status"], "error")
            self.assertIn("403", meta["last_sync_error"])
            conn.close()

    def test_g7_a04_dropped_card_sets_unparsed_and_incomplete(self):
        """G7-A04: Items missing required fields must increase unparsed and mark scan incomplete."""
        wb_raw = {"data": {"products": [make_wb_item(1, 10), {"id": 999, "name": "Invalid price", "salePriceU": 0}]}}
        oz_raw = {"widgetStates": {"searchResults": {"items": [make_ozon_item(1, 10), {"id": 999, "title": "Invalid price", "price": 0}]}}}

        wb_res = WildberriesScraper.parse_payload(wb_raw, "Категория", "https://example.com", 1)
        self.assertEqual(len(wb_res), 1)
        self.assertEqual(wb_res.unparsed, 1)
        self.assertFalse(wb_res.complete)

        oz_res = OzonScraper.parse_payload(oz_raw, "Категория", "https://example.com", 1)
        self.assertEqual(len(oz_res), 1)
        self.assertEqual(oz_res.unparsed, 1)
        self.assertFalse(oz_res.complete)

    def test_g7_a04_unexpected_schema_raises_unconfirmed_end(self):
        """G7-A04: Unknown response structure must raise UnconfirmedEnd, not pretend catalog completed."""
        with self.assertRaises(UnconfirmedEnd):
            WildberriesScraper.parse_payload({"unexpected": True}, "Категория", "https://example.com", 1)

        with self.assertRaises(UnconfirmedEnd):
            OzonScraper.parse_payload({"unexpected": True}, "Категория", "https://example.com", 1)

    def test_g7_a05_raw_descriptions_scrubbed_before_storage(self):
        """G7-A05: Raw card descriptions must not be stored in offers.raw_payload_json."""
        for cls in (WildberriesShowcaseScanner, OzonShowcaseScanner):
            platform = "wildberries" if cls is WildberriesShowcaseScanner else "ozon"
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch', 's1', ?, ?, '123456', '2026-01-01')", (platform, platform))

            raw_item = make_wb_item(10, 20) if platform == "wildberries" else make_ozon_item(10, 20)
            raw_item["description"] = "SENSITIVE_PROSE_MUST_NOT_BE_STORED"
            raw_item["full_description"] = "FULL_PROSE_TEXT"
            payload = {"data": {"products": [raw_item]}} if platform == "wildberries" else {"widgetStates": {"searchResults": {"items": [raw_item]}}}

            with patch("scrapers.showcase_scanners.requests.get", return_value=Mock(status_code=200, json=Mock(return_value=payload))):
                offers, _ = cls().fetch_merchant_page("123456")

            for off in offers:
                pid, _ = match_or_create_canonical_product(conn, off, "2026-11-09T00:00:00+00:00")
                oid = upsert_showcase_offer(conn, off, pid, "s1", "ch", "2026-11-09T00:00:00+00:00")
                stored = conn.execute("SELECT raw_payload_json FROM offers WHERE id=?", (oid,)).fetchone()[0]
                self.assertNotIn("SENSITIVE_PROSE_MUST_NOT_BE_STORED", stored)
                self.assertNotIn("FULL_PROSE_TEXT", stored)
            conn.close()


if __name__ == "__main__":
    unittest.main()
