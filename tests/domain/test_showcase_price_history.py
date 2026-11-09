from tests import test_support  # noqa: F401
"""Тесты для отображения динамики цен и истории изменений по витринам маркетплейсов (Шаг 5)."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from domain.channel_sync import upsert_showcase_offer, sync_channel_showcase
from scrapers.showcase_scanners import ShowcaseOffer
from repositories.schema_v2 import init_schema_v2
from repositories.history_repo import HistoryRepository


class ShowcasePriceHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_history.db")
        self.addCleanup(self.temp_dir.cleanup)

        conn = self._get_conn()
        init_schema_v2(conn)

        # Создаем базовые сущности: товар, продавец, канал витрины
        conn.execute("""
            INSERT INTO canonical_products (
                id, canonical_key, title, category, brand, model,
                item_kind, description, image_url, attributes_json,
                created_at, updated_at
            ) VALUES (
                'prod_test_vacuum', 'key_vacuum', 'Пылесос LG VK89309H', 'Пылесосы', 'LG', 'VK89309H',
                'device', 'Тестовый пылесос', 'http://img/lg.jpg', '{}',
                '2026-11-01T10:00:00Z', '2026-11-01T10:00:00Z'
            )
        """)
        conn.execute("""
            INSERT INTO sellers (id, name, slug, domain, is_active, created_at, updated_at)
            VALUES ('seller_lg', 'LG Brand Shop', 'lg-shop', 'lg.kz', 1, '2026-11-01T10:00:00Z', '2026-11-01T10:00:00Z')
        """)
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, base_url, is_active, metadata_json, created_at)
            VALUES ('ch_kaspi_lg', 'seller_lg', 'marketplace', 'Kaspi Магазин', '123', 'https://kaspi.kz/shop/info/merchant/123', 1, '{}', '2026-11-01T10:00:00Z')
        """)
        conn.commit()
        conn.close()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def test_upsert_showcase_offer_tracks_price_changes_and_history(self):
        """Проверяет, что при изменении цены витрины фиксируются old_price и точки истории."""
        conn = self._get_conn()
        try:
            # Наблюдение 1: начальная цена 100 000 ₸
            offer_v1 = ShowcaseOffer(
                marketplace="kaspi",
                merchant_id="merchant_123",
                external_sku="SKU-LG-001",
                title="Пылесос LG VK89309H",
                price=100000.0,
                url="https://kaspi.kz/shop/p/lg-001",
                old_price=None,
                image_url="http://img/1.jpg",
                in_stock=True,
                city="Астана",
            )
            off_id = upsert_showcase_offer(
                conn,
                offer_v1,
                product_id="prod_test_vacuum",
                seller_id="seller_lg",
                channel_id="ch_kaspi_lg",
                now_iso="2026-11-01T12:00:00Z",
            )
            conn.commit()

            # Проверяем оффер после 1-го наблюдения
            row = conn.execute("SELECT price, old_price FROM offers WHERE id = ?", (off_id,)).fetchone()
            self.assertEqual(row["price"], 100000.0)
            self.assertIsNone(row["old_price"])

            # Наблюдение 2: цена снизилась до 85 000 ₸
            offer_v2 = ShowcaseOffer(
                marketplace="kaspi",
                merchant_id="merchant_123",
                external_sku="SKU-LG-001",
                title="Пылесос LG VK89309H",
                price=85000.0,
                url="https://kaspi.kz/shop/p/lg-001",
                old_price=None,  # скрапер не вернул old_price, система должна взять прежнюю цену
                image_url="http://img/1.jpg",
                in_stock=True,
                city="Астана",
            )
            upsert_showcase_offer(
                conn,
                offer_v2,
                product_id="prod_test_vacuum",
                seller_id="seller_lg",
                channel_id="ch_kaspi_lg",
                now_iso="2026-11-03T15:00:00Z",
            )
            conn.commit()

            # Проверяем оффер после 2-го наблюдения
            row = conn.execute("SELECT price, old_price FROM offers WHERE id = ?", (off_id,)).fetchone()
            self.assertEqual(row["price"], 85000.0)
            self.assertEqual(row["old_price"], 100000.0)

            # Проверяем историю цен offer_price_history
            hist_rows = conn.execute(
                "SELECT price, old_price, observed_at, product_id, seller_id, channel_id FROM offer_price_history WHERE offer_id = ? ORDER BY observed_at ASC",
                (off_id,),
            ).fetchall()
            self.assertEqual(len(hist_rows), 2)
            self.assertEqual(hist_rows[0]["price"], 100000.0)
            self.assertEqual(hist_rows[1]["price"], 85000.0)
            self.assertEqual(hist_rows[1]["old_price"], 100000.0)
            self.assertEqual(hist_rows[1]["product_id"], "prod_test_vacuum")
            self.assertEqual(hist_rows[1]["seller_id"], "seller_lg")
            self.assertEqual(hist_rows[1]["channel_id"], "ch_kaspi_lg")

            # Проверяем агрегацию через HistoryRepository для карточки товара
            repo = HistoryRepository(conn)
            report = repo.get_product_price_history("prod_test_vacuum")
            self.assertIsNotNone(report)
            self.assertEqual(report.total_series, 1)
            self.assertEqual(report.total_observations, 2)
            series = report.series[0]
            self.assertEqual(series.seller_id, "seller_lg")
            self.assertEqual(series.channel_id, "ch_kaspi_lg")
            self.assertEqual(series.first_price, 100000.0)
            self.assertEqual(series.latest_price, 85000.0)
            self.assertEqual(series.min_price, 85000.0)
            self.assertEqual(series.max_price, 100000.0)
            self.assertEqual(series.change_amount, -15000.0)
            self.assertEqual(series.change_pct, -15.0)
        finally:
            conn.close()

    def test_sync_channel_showcase_records_sync_history_metadata(self):
        """Проверяет, что синхронизация витрины маркетплейса сохраняет журнал запусков сбора."""
        fake_offers = [
            ShowcaseOffer(
                marketplace="kaspi",
                merchant_id="merchant_123",
                external_sku="SKU-1",
                title="Пылесос LG VK89309H",
                price=89000.0,
                url="https://kaspi.kz/shop/p/1",
                old_price=99000.0,
                image_url="http://img/1.jpg",
                in_stock=True,
            )
        ]

        conn = self._get_conn()
        try:
            async def _run():
                with patch("domain.channel_sync.KaspiShowcaseScanner.scan_merchant_showcase", new=AsyncMock(return_value=fake_offers)):
                    res = await sync_channel_showcase(conn, "ch_kaspi_lg")
                    return res

            res = asyncio.run(_run())
            self.assertTrue(res["ok"])
            self.assertEqual(res["scanned_count"], 1)

            # Проверяем metadata_json канала
            row = conn.execute("SELECT metadata_json FROM channels WHERE id = 'ch_kaspi_lg'").fetchone()
            meta = json.loads(row["metadata_json"])
            self.assertIn("sync_history", meta)
            self.assertEqual(len(meta["sync_history"]), 1)
            run_record = meta["sync_history"][0]
            self.assertEqual(run_record["scanned_count"], 1)
            self.assertEqual(run_record["offers_upserted"], 1)
            self.assertEqual(run_record["status"], "ok")
            self.assertIn("synced_at", run_record)
        finally:
            conn.close()

    def test_admin_channel_history_endpoint(self):
        """Проверяет эндпоинт GET /api/v2/admin/channels/{channel_id}/history."""
        from aiohttp.test_utils import make_mocked_request
        from web.server import admin_channel_history_handler

        # Подготовим оффер с изменением цены
        conn = self._get_conn()
        offer = ShowcaseOffer(
            marketplace="kaspi",
            merchant_id="merchant_123",
            external_sku="SKU-CH-HIST",
            title="Пылесос LG VK89309H",
            price=90000.0,
            url="https://kaspi.kz/shop/p/lg-hist",
            old_price=105000.0,
            image_url="http://img/lg.jpg",
            in_stock=True,
            city="Астана",
        )
        upsert_showcase_offer(
            conn,
            offer,
            product_id="prod_test_vacuum",
            seller_id="seller_lg",
            channel_id="ch_kaspi_lg",
            now_iso="2026-11-04T00:00:00Z",
        )
        conn.commit()
        conn.close()

        async def _test():
            req = make_mocked_request(
                "GET",
                "/api/v2/admin/channels/ch_kaspi_lg/history",
                match_info={"channel_id": "ch_kaspi_lg"},
            )
            req["user"] = {"id": 1, "username": "admin"}
            with patch("auth.is_admin", return_value=True), patch("database.get_connection", side_effect=self._get_conn):
                resp = await admin_channel_history_handler(req)
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.text)
                self.assertTrue(data["ok"])
                self.assertEqual(data["channel_id"], "ch_kaspi_lg")
                self.assertEqual(data["seller_name"], "LG Brand Shop")
                self.assertEqual(data["stats"]["total_offers"], 1)
                self.assertEqual(data["stats"]["total_points"], 1)
                self.assertEqual(len(data["price_changes"]), 1)
                change = data["price_changes"][0]
                self.assertEqual(change["price"], 90000.0)
                self.assertEqual(change["old_price"], 105000.0)
                self.assertEqual(change["diff"], -15000.0)
                self.assertEqual(change["diff_pct"], -14.3)
                self.assertEqual(change["title"], "Пылесос LG VK89309H")

                # Проверка 404 для несуществующего канала
                req404 = make_mocked_request(
                    "GET",
                    "/api/v2/admin/channels/non_existent/history",
                    match_info={"channel_id": "non_existent"},
                )
                req404["user"] = {"id": 1}
                resp404 = await admin_channel_history_handler(req404)
                self.assertEqual(resp404.status, 404)

        asyncio.run(_test())

    def test_product_price_history_v2_endpoint(self):
        """Проверяет эндпоинт GET /api/v2/products/{product_id}/history."""
        from aiohttp.test_utils import make_mocked_request
        from web.server import product_price_history_v2_handler

        conn = self._get_conn()
        offer = ShowcaseOffer(
            marketplace="kaspi",
            merchant_id="merchant_123",
            external_sku="SKU-PROD-HIST",
            title="Пылесос LG VK89309H",
            price=77000.0,
            url="https://kaspi.kz/shop/p/lg-p",
            old_price=None,
            in_stock=True,
            city="Алматы",
        )
        upsert_showcase_offer(
            conn,
            offer,
            product_id="prod_test_vacuum",
            seller_id="seller_lg",
            channel_id="ch_kaspi_lg",
            now_iso="2026-11-04T01:00:00Z",
        )
        conn.commit()
        conn.close()

        async def _test():
            req = make_mocked_request(
                "GET",
                "/api/v2/products/prod_test_vacuum/history",
                match_info={"product_id": "prod_test_vacuum"},
            )
            with patch("database.get_connection", side_effect=self._get_conn):
                resp = await product_price_history_v2_handler(req)
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.text)
                self.assertTrue(data["ok"])
                self.assertEqual(data["product_id"], "prod_test_vacuum")
                self.assertEqual(len(data["series"]), 1)
                self.assertEqual(data["series"][0]["latest_price"], 77000.0)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
