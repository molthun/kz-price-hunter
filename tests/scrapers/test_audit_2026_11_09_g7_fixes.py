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
from scrapers.showcase_scanners import OzonShowcaseScanner, ShowcaseOffer, WildberriesShowcaseScanner
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
        self.assertEqual(offer[1], "chan_seller_alser_wildberries_99999")
        conn.close()

    async def test_g7_a02_numeric_id_routing_uses_declared_channel_type(self):
        """G7-A02: Numeric store ID without base_url must respect declared channel_type."""
        for platform in ("wildberries", "ozon"):
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch', 's1', ?, ?, '123456', '2026-01-01')", (platform, platform))

            with patch("domain.channel_sync.KaspiShowcaseScanner.scan_merchant_showcase", new_callable=AsyncMock) as kaspi_mock:
                with patch("scrapers.showcase_scanners.requests.get", return_value=Mock(status_code=200, json=Mock(return_value={"data": {"products": []}} if platform == "wildberries" else {"widgetStates": {"searchResultsV2-1": {"items": []}}}))):
                    res = await sync_channel_showcase(conn, "ch", allow_disabled=True)

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
                res = await sync_channel_showcase(conn, "ch", allow_disabled=True)

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


def _v2_conn():
    """Схема v2 во временной SQLite в памяти — тот же путь записи, что у database.py."""
    return create_test_db()


def _seed_confirmed(conn, *, with_channel_ext=None, identity="wildberries:456", seller="seller_real"):
    conn.execute("INSERT INTO sellers (id, slug, name, is_active, created_at, updated_at) "
                 "VALUES (?, ?, 'Real Shop', 1, 'x', 'x')", (seller, seller))
    if identity:
        conn.execute("INSERT INTO seller_identities (id, seller_id, identity_type, identity_value, confidence, source, created_at) "
                     "VALUES (?, ?, 'marketplace_seller_id', ?, 1.0, 'onboarding', 'x')", (f"i_{seller}", seller, identity))
    if with_channel_ext is not None:
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, is_active, created_at) "
                     "VALUES (?, ?, 'wildberries', 'Real WB', ?, 1, 'x')", (f"ch_{seller}_wb", seller, with_channel_ext))


def _wb_prepared(*pairs):
    items = WildberriesScraper.parse_payload(
        {"data": {"products": [make_wb_item(sku, sup) for sku, sup in pairs]}}, "Смартфоны", "https://example.com", 1)
    return [prepare_legacy_offer_v2(it) for it in items]


class AuditG7RecheckTest(unittest.IsolatedAsyncioTestCase):
    """Перепроверка Claude после v6.29.1: остаточные случаи G7-A01/A03/A04."""

    def test_confirmed_seller_existing_wb_channel_is_reused(self):
        """Канал WB продавца с тем же номером витрины переиспользуется, запись не падает
        на uq_channels_seller_type_ext; предложение и матрица — у реального продавца."""
        conn = _v2_conn()
        _seed_confirmed(conn, with_channel_ext="456")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, is_active, created_at) "
                     "VALUES ('ch_real_web', 'seller_real', 'website', 'Real site', 1, 'x')")
        write_legacy_offers_v2(conn, _wb_prepared((101, 456)), "2026-11-09T00:00:00+00:00")
        row = conn.execute("SELECT seller_id, channel_id FROM offers WHERE external_sku = 'wb_101'").fetchone()
        self.assertEqual(tuple(row), ("seller_real", "ch_seller_real_wb"))
        self.assertEqual(conn.execute("SELECT count(*) FROM channels WHERE seller_id = 'seller_real' "
                                      "AND channel_type = 'wildberries'").fetchone()[0], 1)

        # Матрица из того, что записано в БД: прямое предложение продавца дешевле его витрины WB
        product_id = row and conn.execute("SELECT product_id FROM offers WHERE external_sku='wb_101'").fetchone()[0]
        conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, currency, "
                     "condition, availability, city, is_active, observed_at, created_at, updated_at) "
                     "VALUES ('o_web', ?, 'seller_real', 'ch_real_web', 'web1', 'https://real.kz/p', 90000, 'KZT', "
                     "'NEW', 'in_stock', 'Алматы', 1, 'x', 'x', 'x')", (product_id,))
        db_offers = [Offer(id=r[0], product_id=r[1], seller_id=r[2], channel_id=r[3], external_sku=r[4], url=r[5], price=r[6])
                     for r in conn.execute("SELECT id, product_id, seller_id, channel_id, external_sku, url, price "
                                           "FROM offers WHERE product_id = ?", (product_id,))]
        types = dict(conn.execute("SELECT id, channel_type FROM channels"))
        sellers_map = {r[0]: Seller(id=r[0], slug=r[1], name=r[2]) for r in conn.execute("SELECT id, slug, name FROM sellers")}
        matrix = build_channel_matrix(product_id=product_id, canonical_key="k", title="t", offers=db_offers,
                                      sellers_map=sellers_map, channel_types=types)
        self.assertEqual(len(matrix.cross_channel_opportunities), 1)
        opp = matrix.cross_channel_opportunities[0]
        self.assertEqual(opp.seller_id, "seller_real")
        self.assertEqual(opp.marketplace_channel_type, ChannelType.WILDBERRIES)
        self.assertEqual(matrix.best_direct_price, 90000.0)
        self.assertEqual(matrix.best_marketplace_price, 100000.0)
        conn.close()

    def test_late_confirmed_seller_wins_over_auto_seller(self):
        """Первый обход создаёт seller_mp_*; позже подтверждённый продавец получает предложения
        детерминированно, а не по порядку строк identity."""
        conn = _v2_conn()
        write_legacy_offers_v2(conn, _wb_prepared((101, 456)), "t1")
        self.assertEqual(conn.execute("SELECT seller_id FROM offers WHERE external_sku='wb_101'").fetchone()[0],
                         "seller_mp_wildberries_456")
        _seed_confirmed(conn)
        write_legacy_offers_v2(conn, _wb_prepared((101, 456)), "t2")
        row = conn.execute("SELECT seller_id, channel_id FROM offers WHERE external_sku='wb_101'").fetchone()
        self.assertEqual(tuple(row), ("seller_real", "chan_seller_real_wildberries_456"))
        conn.close()

    def test_ambiguous_identity_is_not_merged(self):
        """Один номер поставщика у двух подтверждённых продавцов — не объединяем, остаётся автопродавец."""
        conn = _v2_conn()
        _seed_confirmed(conn, seller="seller_a")
        _seed_confirmed(conn, seller="seller_b")
        write_legacy_offers_v2(conn, _wb_prepared((101, 456)), "t")
        self.assertEqual(conn.execute("SELECT seller_id FROM offers WHERE external_sku='wb_101'").fetchone()[0],
                         "seller_mp_wildberries_456")
        conn.close()

    def test_resolution_is_idempotent_on_retry(self):
        """Повтор той же порции (откат и запись по одному) даёт тот же результат."""
        conn = _v2_conn()
        _seed_confirmed(conn, with_channel_ext="456")
        prepared = _wb_prepared((101, 456), (102, 789))
        write_legacy_offers_v2(conn, prepared, "t1")
        write_legacy_offers_v2(conn, prepared, "t2")
        rows = dict(conn.execute("SELECT external_sku, seller_id FROM offers").fetchall())
        self.assertEqual(rows, {"wb_101": "seller_real", "wb_102": "seller_mp_wildberries_789"})
        conn.close()

    async def _sync(self, platform, responses, max_pages=3):
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1','s1','S','x','x')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, metadata_json, created_at) "
                     "VALUES ('ch', 's1', ?, ?, '123456', ?, 'x')",
                     (platform, platform, json.dumps({"last_sync_at": "2026-01-01T00:00:00+00:00"})))
        with patch("scrapers.showcase_scanners.requests.get", side_effect=responses), \
                patch("scrapers.showcase_scanners.asyncio.sleep", new=AsyncMock()):
            res = await sync_channel_showcase(conn, "ch", max_pages=max_pages, allow_disabled=True)
        meta = json.loads(conn.execute("SELECT metadata_json FROM channels WHERE id='ch'").fetchone()[0])
        count = conn.execute("SELECT count(*) FROM offers").fetchone()[0]
        conn.close()
        return res, meta, count

    @staticmethod
    def _ok(payload):
        return Mock(status_code=200, json=Mock(return_value=payload))

    async def test_showcase_failures_do_not_refresh(self):
        """HTTP 403/429/500, не-JSON и смена схемы — отказ: ok=false, last_sync_at прежний."""
        bad_json = Mock(status_code=200, json=Mock(side_effect=ValueError("not json")))
        cases = {
            "wildberries": [Mock(status_code=403), Mock(status_code=429), Mock(status_code=500), bad_json,
                            self._ok({"unexpected": True}), self._ok({"data": {"products": "x"}})],
            "ozon": [Mock(status_code=403), Mock(status_code=500), bad_json,
                     self._ok({"unexpected": True}), self._ok({"widgetStates": {"header-1": "{}"}})],
        }
        for platform, responses in cases.items():
            for resp in responses:
                with self.subTest(platform=platform, resp=repr(resp)[:60]):
                    res, meta, count = await self._sync(platform, [resp])
                    self.assertFalse(res["ok"])
                    self.assertEqual(res["status"], "error")
                    self.assertEqual(meta["last_sync_status"], "error")
                    self.assertEqual(meta["last_sync_at"], "2026-01-01T00:00:00+00:00")
                    self.assertIn("last_attempt_at", meta)
                    self.assertEqual(count, 0)

    async def test_confirmed_empty_showcase_differs_from_failure(self):
        for platform, payload in (("wildberries", {"data": {"products": []}}),
                                  ("ozon", {"widgetStates": {"searchResultsV2-1": json.dumps({"items": []})}})):
            with self.subTest(platform=platform):
                res, meta, count = await self._sync(platform, [self._ok(payload)])
                self.assertTrue(res["ok"])
                self.assertEqual(res["status"], "ok")
                self.assertEqual(meta["last_sync_status"], "ok")
                self.assertNotEqual(meta["last_sync_at"], "2026-01-01T00:00:00+00:00")

    async def test_second_page_failure_is_limited_and_keeps_first_page(self):
        page1_wb = {"data": {"products": [make_wb_item(i, 1) for i in range(100, 120)]}}
        page1_oz = {"widgetStates": {"searchResults": {"items": [make_ozon_item(i, 1) for i in range(100, 112)]}}}
        for platform, page1 in (("wildberries", page1_wb), ("ozon", page1_oz)):
            for second in (Mock(status_code=500), self._ok({"unexpected": True})):
                with self.subTest(platform=platform, second=repr(second)[:40]):
                    res, meta, count = await self._sync(platform, [self._ok(page1), second])
                    self.assertTrue(res["ok"])
                    self.assertEqual(res["status"], "limited")
                    self.assertFalse(res["complete"])
                    self.assertIn("Страница 2", res["reason"])
                    self.assertEqual(meta["last_sync_status"], "limited")
                    self.assertEqual(meta["last_sync_at"], "2026-01-01T00:00:00+00:00")
                    self.assertGreater(count, 0)

    async def test_dropped_showcase_card_and_page_limit_are_limited(self):
        mixed_wb = {"data": {"products": [make_wb_item(1, 1), {"id": 2, "name": "Без цены"}]}}
        mixed_oz = {"widgetStates": {"searchResults": {"items": [make_ozon_item(1, 1), {"id": 2, "title": "Без цены"}]}}}
        for platform, payload in (("wildberries", mixed_wb), ("ozon", mixed_oz)):
            with self.subTest(platform=platform):
                res, meta, _ = await self._sync(platform, [self._ok(payload)])
                self.assertEqual(res["status"], "limited")
                self.assertIn("Не разобрано карточек: 1", res["reason"])
        full = {"data": {"products": [make_wb_item(i, 1) for i in range(100, 120)]}}
        res, meta, _ = await self._sync("wildberries", [self._ok(full)], max_pages=1)
        self.assertEqual(res["status"], "limited")
        self.assertIn("лимит страниц", res["reason"])

    def test_scheduler_retries_from_last_attempt(self):
        """После отказа повтор — через интервал от попытки, а не на каждом такте."""
        from datetime import datetime, timedelta, timezone
        from domain.channel_scheduler import get_channel_sync_schedule
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1','s1','S','x','x')")
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, metadata_json, created_at) "
                     "VALUES ('ch', 's1', 'wildberries', 'WB', '123', ?, 'x')",
                     (json.dumps({"last_sync_at": "2026-01-01T00:00:00+00:00", "last_attempt_at": recent,
                                  "last_sync_status": "error", "sync_interval_hours": 12}),))
        entry = next(e for e in get_channel_sync_schedule(conn) if e["channel_id"] == "ch")
        self.assertFalse(entry["is_due"])
        self.assertEqual(entry["last_sync_status"], "error")
        self.assertEqual(entry["last_sync_at"], "2026-01-01T00:00:00+00:00")
        conn.close()

    def test_nested_description_removed_before_storage(self):
        """Вложенное описание тоже не доходит до raw_payload_json, в том числе при повторном upsert."""
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1','s1','S','x','x')")
        raw = make_wb_item(1, 1)
        raw["extra"] = {"description": "SYNTHETIC_NESTED_DESCRIPTION", "nested": [{"full_description": "SYNTHETIC_NESTED_DESCRIPTION"}]}
        with patch("scrapers.showcase_scanners.requests.get", return_value=self._ok({"data": {"products": [raw]}})):
            offers, _ = WildberriesShowcaseScanner().fetch_merchant_page("1")
        for _ in range(2):
            pid, _ = match_or_create_canonical_product(conn, offers[0], "2026-11-09T00:00:00+00:00")
            oid = upsert_showcase_offer(conn, offers[0], pid, "s1", "ch", "2026-11-09T00:00:00+00:00")
        stored = conn.execute("SELECT raw_payload_json FROM offers WHERE id = ?", (oid,)).fetchone()[0]
        self.assertNotIn("SYNTHETIC_NESTED_DESCRIPTION", stored)
        conn.close()



class ReauditG7V6292Test(unittest.IsolatedAsyncioTestCase):
    """Повторный аудит Codex v6.29.2: R01–R03."""

    LONG_SELLER = "seller_kazakhstan_electronics_official_store"

    def test_r01_long_confirmed_channel_keeps_offers_apart(self):
        """parser → подтверждённый продавец с длинным id → два SKU и повтор обхода: предложения раздельны."""
        conn = create_test_db()
        _seed_confirmed(conn, seller=self.LONG_SELLER)
        prepared = _wb_prepared((100001, 456), (100002, 456))
        write_legacy_offers_v2(conn, prepared, "t1")
        channel_id = conn.execute("SELECT DISTINCT channel_id FROM offers").fetchone()[0]
        self.assertLessEqual(len(channel_id), 48)

        scanner_offers = []
        for sku, price in (("100001", 100000.0), ("100002", 200000.0)):
            scanner_offers.append(ShowcaseOffer(marketplace="wildberries", merchant_id="456", external_sku=sku,
                                                title=f"Synthetic phone {sku}", price=price,
                                                url=f"https://www.wildberries.kz/catalog/{sku}/detail.aspx"))
        for run, bump in (("2026-11-09T00:00:00+00:00", 0), ("2026-11-09T01:00:00+00:00", 5)):
            for off in scanner_offers:
                off.price += bump
                pid, _ = match_or_create_canonical_product(conn, off, run)
                upsert_showcase_offer(conn, off, pid, self.LONG_SELLER, channel_id, run)
        rows = conn.execute("SELECT external_sku, price FROM offers WHERE channel_id = ? AND id LIKE 'off_%' "
                            "AND external_sku IN ('100001','100002') ORDER BY external_sku", (channel_id,)).fetchall()
        self.assertEqual([tuple(r) for r in rows], [("100001", 100005.0), ("100002", 200005.0)])
        conn.close()

    def test_r01_offer_id_bounded_unique_and_backward_compatible(self):
        from domain.channel_sync import showcase_offer_id
        self.assertEqual(showcase_offer_id("ch_kaspi", "123"), "off_ch_kaspi_123")  # прежняя форма
        long_a = "chan_" + "x" * 70 + "_a"
        long_b = "chan_" + "x" * 70 + "_b"  # одинаковый длинный префикс
        ids = {showcase_offer_id(c, s) for c in (long_a, long_b) for s in ("100001", "100002")}
        self.assertEqual(len(ids), 4)
        self.assertTrue(all(len(i) <= 64 for i in ids))
        self.assertEqual(showcase_offer_id(long_a, "100001"), showcase_offer_id(long_a, "100001"))
        self.assertTrue(showcase_offer_id(long_a, "100001").endswith("100001"))

    def test_r01_manual_long_channel_sync_keeps_offers_apart(self):
        """Длинный канал, созданный вручную (не resolver): тот же путь upsert без склейки."""
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1','s1','S','x','x')")
        ch = "ch_" + "very_long_marketplace_channel_name_" * 2
        for sku in ("A1", "A2", "A3"):
            off = ShowcaseOffer(marketplace="ozon", merchant_id="1", external_sku=sku, title=f"Item {sku}",
                                price=1000.0, url=f"https://ozon.kz/product/{sku}")
            pid, _ = match_or_create_canonical_product(conn, off, "t")
            upsert_showcase_offer(conn, off, pid, "s1", ch, "t")
        self.assertEqual(conn.execute("SELECT count(*) FROM offers WHERE channel_id = ?", (ch,)).fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT count(DISTINCT offer_id) FROM offer_price_history").fetchone()[0], 3)
        conn.close()

    def test_r02_ozon_damaged_second_page_is_not_complete(self):
        import contextlib, io
        import data_quality
        first = {"widgetStates": {"searchResults": {"items": [make_ozon_item(i, 1) for i in range(100, 112)]}}}
        damaged = {
            "header_only": {"widgetStates": {"header": "{}"}},
            "broken_json": {"widgetStates": {"searchResults": "not json"}},
            "items_not_list": {"widgetStates": {"searchResults": {"items": "x"}}},
            "state_not_object": {"widgetStates": {"searchResults": "[1, 2]"}},
        }
        for label, second in damaged.items():
            with self.subTest(label=label):
                scanner = OzonScraper()
                scanner.PAGE_DELAY_SECONDS = 0
                responses = [Mock(status_code=200, json=Mock(return_value=first)),
                             Mock(status_code=200, json=Mock(return_value=second))]
                with patch("scrapers.http.get", side_effect=responses), contextlib.redirect_stdout(io.StringIO()):
                    result = scanner._scrape_sync("Synthetic", "https://ozon.kz/search/?text=x", 3)
                self.assertEqual(len(result), 12)
                self.assertFalse(result.complete)
                assessment = data_quality.assess(data_quality.measure(result), complete=result.complete,
                                                 error=result.error, baseline=None)
                self.assertFalse(assessment["may_retire"])

    def test_r02_ozon_damaged_widget_next_to_valid_one_counts_unparsed(self):
        payload = {"widgetStates": {"searchResultsV2-1": {"items": [make_ozon_item(1, 1)]},
                                    "searchResultsV2-2": "not json"}}
        result = OzonScraper.parse_payload(payload, "c", "u", 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.unparsed, 1)
        self.assertFalse(result.complete)
        # Подтверждённый пустой список отличается от отказа
        empty = OzonScraper.parse_payload({"widgetStates": {"searchResultsV2-1": json.dumps({"items": []})}}, "c", "u", 5)
        self.assertTrue(empty.complete)
        with self.assertRaises(UnconfirmedEnd):
            OzonScraper.parse_payload({"widgetStates": {"header": "{}"}}, "c", "u", 5)

    async def _route(self, channel_type, base_url, ext):
        conn = create_test_db()
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1','s1','S','x','x')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, external_store_id, metadata_json, created_at) "
                     "VALUES ('ch','s1',?, 'Ch', ?, ?, ?, 'x')",
                     (channel_type, base_url, ext, json.dumps({"last_sync_at": "2026-01-01T00:00:00+00:00"})))
        calls = []

        async def fake_scan(self_, merchant_id, max_pages=15, delay_seconds=0.5):
            calls.append((type(self_).__name__, merchant_id))
            from scrapers.showcase_scanners import ShowcaseScanResult
            return ShowcaseScanResult([], "complete")

        with patch("scrapers.showcase_scanners.WildberriesShowcaseScanner.scan_merchant_showcase", new=fake_scan), \
                patch("scrapers.showcase_scanners.OzonShowcaseScanner.scan_merchant_showcase", new=fake_scan), \
                patch("domain.channel_sync.KaspiShowcaseScanner.scan_merchant_showcase", new=fake_scan), \
                patch("scrapers.showcase_scanners.requests.get", side_effect=AssertionError("сеть")):
            res = await sync_channel_showcase(conn, "ch", allow_disabled=True)
        meta = json.loads(conn.execute("SELECT metadata_json FROM channels WHERE id='ch'").fetchone()[0])
        conn.close()
        return res, calls, meta

    async def test_r03_conflicts_rejected_before_scanner(self):
        wb_url = "https://www.wildberries.kz/seller/111/"
        for ctype, url, ext in (
            ("wildberries", wb_url, "ozon:222"),
            ("wildberries", wb_url, "wildberries:222"),
            ("wildberries", wb_url, "222"),
            ("marketplace", wb_url, "ozon:222"),
            ("ozon", "https://ozon.kz/seller/my-store-7788/", "7788"),  # slug и номер — не одно и то же
        ):
            with self.subTest(ctype=ctype, ext=ext):
                res, calls, meta = await self._route(ctype, url, ext)
                self.assertFalse(res["ok"])
                self.assertIn("Конфликт конфигурации", res["error"])
                self.assertEqual(calls, [])
                self.assertEqual(meta["last_sync_at"], "2026-01-01T00:00:00+00:00")

    async def test_r03_matching_representations_still_work(self):
        wb_url = "https://www.wildberries.kz/seller/111/"
        for ctype, url, ext, expected in (
            ("wildberries", wb_url, "111", ("WildberriesShowcaseScanner", "111")),
            ("wildberries", wb_url, "wildberries:111", ("WildberriesShowcaseScanner", "111")),
            ("wb", wb_url, "wb:111", ("WildberriesShowcaseScanner", "111")),
            ("wildberries", wb_url, None, ("WildberriesShowcaseScanner", "111")),
            ("wildberries", None, "111", ("WildberriesShowcaseScanner", "111")),
            ("marketplace", None, "ozon:my-store", ("OzonShowcaseScanner", "my-store")),
            ("kaspi", "https://kaspi.kz/shop/m/30123456/", "30123456", ("KaspiShowcaseScanner", "30123456")),
            ("kaspi", None, "30123456", ("KaspiShowcaseScanner", "30123456")),
        ):
            with self.subTest(ctype=ctype, url=url, ext=ext):
                res, calls, _ = await self._route(ctype, url, ext)
                self.assertTrue(res["ok"], res)
                self.assertEqual(calls, [expected])

    async def test_wb_and_ozon_showcase_sync_disabled_by_default(self):
        """Wildberries и Ozon отключены решением владельца: sync_channel_showcase отклоняет их."""
        for platform in ("wildberries", "ozon"):
            conn = create_test_db()
            conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s1', 's1', 'Seller', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, created_at) VALUES ('ch', 's1', ?, ?, '123456', '2026-01-01')", (platform, platform))
            res = await sync_channel_showcase(conn, "ch")
            self.assertFalse(res["ok"])
            self.assertEqual(res["status"], "error")
            self.assertIn("отключены решением владельца", res["error"])
            conn.close()
