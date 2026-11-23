from tests import test_support  # must be first import

import datetime
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from aiohttp.test_utils import make_mocked_request

from database import get_connection, init_db
from domain.freshness_v2 import (
    FreshnessStateV2,
    SourceFreshnessType,
    get_freshness_stats,
    reap_expired_offers,
)
from domain.models import (
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.pricing_repo import PricingRepository
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import init_schema_v2
from repositories.seller_repo import SellerRepository
import web.server as server


def _utc_iso(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).isoformat()


class TestFreshnessReaper(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_reaper.db")
        self.patcher = patch("database.DB_PATH", Path(self.db_path))
        self.patcher.start()

        init_db()
        conn = get_connection()
        try:
            init_schema_v2(conn)
            conn.commit()
        finally:
            conn.close()

        self.now = datetime.datetime(2026, 10, 31, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _seed_base_entities(self, conn):
        s_repo = SellerRepository(conn)
        s_repo.save_or_update(Seller(id="s1", slug="seller-one", name="Магазин Один"))
        p_repo = ProductRepository(conn)
        p_repo.save_or_update(
            CanonicalProduct(
                id="p1",
                canonical_key="apple:iphone-16-pro:256gb",
                title="Apple iPhone 16 Pro 256GB Desert Titanium",
                category="Смартфоны",
                brand="Apple",
                model="iPhone 16 Pro",
            )
        )
        ch_repo = ChannelRepository(conn)
        ch_repo.save_or_update(
            Channel(
                id="ch_web",
                seller_id="s1",
                channel_type=ChannelType.WEBSITE,
                name="Официальный сайт",
            )
        )
        ch_repo.save_or_update(
            Channel(
                id="ch_story",
                seller_id="s1",
                channel_type=ChannelType.INSTAGRAM,
                name="Instagram Stories",
            )
        )
        ch_repo.save_or_update(
            Channel(
                id="ch_food",
                seller_id="s1",
                channel_type=ChannelType.FOOD_AGGREGATOR,
                name="Food Delivery",
            )
        )
        conn.commit()

    def test_reap_expired_by_explicit_ttl(self):
        conn = get_connection()
        try:
            self._seed_base_entities(conn)
            o_repo = OfferRepository(conn)

            # Оффер с явным expires_at в прошлом
            past_expires = _utc_iso(self.now - datetime.timedelta(hours=2))
            o_expired = Offer(
                id="off_ttl_exp",
                product_id="p1",
                seller_id="s1",
                channel_id="ch_web",
                external_sku="SKU-TTL",
                url="https://s1.kz/p1",
                price=500000.0,
                city="Алматы",
                observed_at=_utc_iso(self.now - datetime.timedelta(hours=10)),
                expires_at=past_expires,
                is_active=1,
            )
            o_repo.save_or_update(o_expired)

            # Оффер с явным expires_at в будущем
            future_expires = _utc_iso(self.now + datetime.timedelta(hours=5))
            o_active = Offer(
                id="off_ttl_act",
                product_id="p1",
                seller_id="s1",
                channel_id="ch_web",
                external_sku="SKU-ACT",
                url="https://s1.kz/p2",
                price=510000.0,
                city="Алматы",
                observed_at=_utc_iso(self.now - datetime.timedelta(hours=1)),
                expires_at=future_expires,
                is_active=1,
            )
            o_repo.save_or_update(o_active)
            conn.commit()

            # Запуск Reaper
            result = reap_expired_offers(conn, now=self.now, batch_size=500, dry_run=False)

            self.assertTrue(result["ok"])
            self.assertEqual(result["total_reaped"], 1)
            self.assertEqual(result["by_reason"]["ttl_expired"], 1)
            self.assertIn("off_ttl_exp", result["reaped_ids_sample"])

            # Проверяем состояние в базе данных
            row_exp = conn.execute("SELECT is_active, expires_at FROM offers WHERE id = 'off_ttl_exp'").fetchone()
            self.assertEqual(row_exp["is_active"], 0)

            row_act = conn.execute("SELECT is_active FROM offers WHERE id = 'off_ttl_act'").fetchone()
            self.assertEqual(row_act["is_active"], 1)
        finally:
            conn.close()

    def test_reap_by_source_freshness_thresholds(self):
        conn = get_connection()
        try:
            self._seed_base_entities(conn)
            o_repo = OfferRepository(conn)

            # Оффер еды (порог EXPIRED > 24ч): возраст 30ч -> должен быть списан
            o_food = Offer(
                id="off_food_old",
                product_id="p1",
                seller_id="s1",
                channel_id="ch_food",
                external_sku="SKU-FOOD",
                url="https://s1.kz/food",
                price=15000.0,
                city="Алматы",
                observed_at=_utc_iso(self.now - datetime.timedelta(hours=30)),
                is_active=1,
            )
            o_repo.save_or_update(o_food)

            # Оффер сайта: возраст 180ч (> 168ч предельного порога сайта) -> должен быть списан
            o_site_dead = Offer(
                id="off_site_dead",
                product_id="p1",
                seller_id="s1",
                channel_id="ch_web",
                external_sku="SKU-DEAD-SITE",
                url="https://s1.kz/dead",
                price=490000.0,
                city="Алматы",
                observed_at=_utc_iso(self.now - datetime.timedelta(hours=180)),
                is_active=1,
            )
            o_repo.save_or_update(o_site_dead)

            # Свежий оффер сайта (возраст 5 часов) -> должен остаться активным
            o_site_fresh = Offer(
                id="off_site_fresh",
                product_id="p1",
                seller_id="s1",
                channel_id="ch_web",
                external_sku="SKU-FRESH",
                url="https://s1.kz/fresh",
                price=495000.0,
                city="Алматы",
                observed_at=_utc_iso(self.now - datetime.timedelta(hours=5)),
                is_active=1,
            )
            o_repo.save_or_update(o_site_fresh)
            conn.commit()

            # Тест dry_run
            dry_res = reap_expired_offers(conn, now=self.now, batch_size=500, dry_run=True)
            self.assertTrue(dry_res["ok"])
            self.assertEqual(dry_res["total_reaped"], 2)
            # В базе ничего не изменилось
            active_count = conn.execute("SELECT COUNT(*) FROM offers WHERE is_active = 1").fetchone()[0]
            self.assertEqual(active_count, 3)

            # Реальный запуск списания
            live_res = reap_expired_offers(conn, now=self.now, batch_size=500, dry_run=False)
            self.assertEqual(live_res["total_reaped"], 2)
            self.assertEqual(live_res["by_reason"]["source_threshold_expired"], 2)

            # В базе активным остался только свежий оффер сайта
            active_after = conn.execute("SELECT id FROM offers WHERE is_active = 1").fetchall()
            self.assertEqual(len(active_after), 1)
            self.assertEqual(active_after[0]["id"], "off_site_fresh")
        finally:
            conn.close()

    def test_get_freshness_stats(self):
        conn = get_connection()
        try:
            self._seed_base_entities(conn)
            o_repo = OfferRepository(conn)

            # Добавляем 1 свежий и 1 просроченный
            o_repo.save_or_update(
                Offer(
                    id="o_fresh",
                    product_id="p1",
                    seller_id="s1",
                    channel_id="ch_web",
                    external_sku="SF-1",
                    url="https://s1.kz/1",
                    price=100.0,
                    city="Алматы",
                    observed_at=_utc_iso(self.now - datetime.timedelta(hours=2)),
                    is_active=1,
                )
            )
            o_repo.save_or_update(
                Offer(
                    id="o_expired",
                    product_id="p1",
                    seller_id="s1",
                    channel_id="ch_food",
                    external_sku="SF-2",
                    url="https://s1.kz/2",
                    price=200.0,
                    city="Алматы",
                    observed_at=_utc_iso(self.now - datetime.timedelta(hours=48)),
                    is_active=1,
                )
            )
            conn.commit()

            stats = get_freshness_stats(conn, now=self.now)
            self.assertEqual(stats["total_offers"], 2)
            self.assertEqual(stats["active_offers"], 2)
            self.assertEqual(stats["states"][FreshnessStateV2.FRESH], 1)
            self.assertEqual(stats["states"][FreshnessStateV2.EXPIRED], 1)
            self.assertEqual(stats["pending_reap_count"], 1)
        finally:
            conn.close()


class TestFreshnessEndpoints(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_server.db")
        self.patcher = patch("database.DB_PATH", Path(self.db_path))
        self.patcher.start()

        init_db()
        conn = get_connection()
        try:
            init_schema_v2(conn)
            s_repo = SellerRepository(conn)
            s_repo.save_or_update(Seller(id="s_adm", slug="adm-shop", name="Админ Маркет"))
            p_repo = ProductRepository(conn)
            p_repo.save_or_update(
                CanonicalProduct(
                    id="p_hist",
                    canonical_key="samsung:s24:256",
                    title="Samsung Galaxy S24 256GB",
                    category="Смартфоны",
                )
            )
            ch_repo = ChannelRepository(conn)
            ch_repo.save_or_update(
                Channel(
                    id="ch_kaspi",
                    seller_id="s_adm",
                    channel_type=ChannelType.KASPI,
                    name="Kaspi Магазин",
                )
            )
            ch_repo.save_or_update(
                Channel(
                    id="ch_direct",
                    seller_id="s_adm",
                    channel_type=ChannelType.WEBSITE,
                    name="Прямой сайт",
                )
            )
            o_repo = OfferRepository(conn)
            o_repo.save_or_update(
                Offer(
                    id="off_kaspi",
                    product_id="p_hist",
                    seller_id="s_adm",
                    channel_id="ch_kaspi",
                    external_sku="KASPI-S24",
                    url="https://kaspi.kz/s24",
                    price=420000.0,
                    city="Алматы",
                    observed_at="2026-10-31T10:00:00+00:00",
                    is_active=1,
                )
            )
            o_repo.save_or_update(
                Offer(
                    id="off_direct",
                    product_id="p_hist",
                    seller_id="s_adm",
                    channel_id="ch_direct",
                    external_sku="DIRECT-S24",
                    url="https://adm.kz/s24",
                    price=399000.0,
                    city="Алматы",
                    observed_at="2026-10-31T10:00:00+00:00",
                    is_active=1,
                )
            )
            # Добавим срез в историю цен
            conn.execute(
                "INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES (?, ?, ?, ?)",
                ("off_kaspi", 420000.0, 430000.0, "2026-10-31T10:00:00+00:00"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES (?, ?, ?, ?)",
                ("off_kaspi", 415000.0, 420000.0, "2026-10-31T11:00:00+00:00"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES (?, ?, ?, ?)",
                ("off_direct", 399000.0, None, "2026-10-31T10:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    async def test_freshness_reap_admin_required(self):
        # Гость без авторизации получает 401
        req_anon = make_mocked_request("POST", "/api/v2/admin/freshness/reap")
        resp = await server.admin_freshness_reap_handler(req_anon)
        self.assertEqual(resp.status, 401)

        # Обычный пользователь без прав админа получает 403
        req_user = make_mocked_request("POST", "/api/v2/admin/freshness/reap")
        req_user["user"] = {"id": 10, "username": "regular_user"}
        with patch("auth.is_admin", return_value=False):
            resp_user = await server.admin_freshness_reap_handler(req_user)
            self.assertEqual(resp_user.status, 403)

        # С правами админа получает 200
        req_adm = make_mocked_request(
            "POST",
            "/api/v2/admin/freshness/reap?batch_size=100&dry_run=true",
        )
        req_adm["user"] = {"id": 1, "username": "admin"}
        with patch("auth.is_admin", return_value=True):
            resp_adm = await server.admin_freshness_reap_handler(req_adm)
            self.assertEqual(resp_adm.status, 200)
            data = json.loads(resp_adm.text)
            self.assertTrue(data.get("ok"))
            self.assertTrue(data.get("dry_run"))

    async def test_freshness_stats_admin_required(self):
        req_anon = make_mocked_request("GET", "/api/v2/admin/freshness/stats")
        resp = await server.admin_freshness_stats_handler(req_anon)
        self.assertEqual(resp.status, 401)

        req_user = make_mocked_request("GET", "/api/v2/admin/freshness/stats")
        req_user["user"] = {"id": 10, "username": "regular_user"}
        with patch("auth.is_admin", return_value=False):
            resp_user = await server.admin_freshness_stats_handler(req_user)
            self.assertEqual(resp_user.status, 403)

        req_adm = make_mocked_request("GET", "/api/v2/admin/freshness/stats")
        req_adm["user"] = {"id": 1, "username": "admin"}
        with patch("auth.is_admin", return_value=True):
            resp_adm = await server.admin_freshness_stats_handler(req_adm)
            self.assertEqual(resp_adm.status, 200)
            data = json.loads(resp_adm.text)
            self.assertTrue(data.get("ok"))
            self.assertIn("states", data)
            self.assertIn("pending_reap_count", data)

    async def test_product_price_history_v2_endpoint(self):
        # Проверяем изолированные серии без смешивания
        req = make_mocked_request("GET", "/api/v2/products/p_hist/history", match_info={"product_id": "p_hist"})
        resp = await server.product_price_history_v2_handler(req)
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.text)
        self.assertTrue(data.get("ok"))
        report = data.get("report") or {}
        self.assertEqual(report.get("product_id"), "p_hist")
        series_list = report.get("series") or []
        self.assertEqual(len(series_list), 2)

        # Проверяем, что ряды изолированы по каналам
        kaspi_series = next((s for s in series_list if s["channel_type"] == "kaspi"), None)
        self.assertIsNotNone(kaspi_series)
        self.assertEqual(kaspi_series["latest_price"], 415000.0)
        self.assertEqual(len(kaspi_series["points"]), 2)

        direct_series = next((s for s in series_list if s["channel_type"] == "website"), None)
        self.assertIsNotNone(direct_series)
        self.assertEqual(direct_series["latest_price"], 399000.0)


class TestFreshnessReaperLegacySync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_reaper_sync.db"
        self.patcher = patch("database.DB_PATH", self.db_path)
        self.patcher.start()
        init_db()
        conn = get_connection()
        try:
            init_schema_v2(conn)
            conn.commit()
        finally:
            conn.close()
        self.now = datetime.datetime(2026, 11, 23, 12, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.patcher.stop()
        self.temp_dir.cleanup()

    def _seed_pair(self, conn, pid: str, observed_at: str):
        s_repo = SellerRepository(conn)
        s_repo.save_or_update(Seller(id="s1", slug="shop-1", name="Shop 1"))
        p_repo = ProductRepository(conn)
        p_repo.save_or_update(
            CanonicalProduct(
                id="cp_" + pid,
                canonical_key="key_" + pid,
                title="Product " + pid,
                category="Одежда",
            )
        )
        ch_repo = ChannelRepository(conn)
        ch_repo.save_or_update(
            Channel(id="ch_web", seller_id="s1", channel_type=ChannelType.WEBSITE, name="Site")
        )
        o_repo = OfferRepository(conn)
        o_repo.save_or_update(
            Offer(
                id="off_" + pid,
                product_id="cp_" + pid,
                seller_id="s1",
                channel_id="ch_web",
                external_sku="SKU-" + pid,
                url="https://shop1.kz/" + pid,
                price=10000.0,
                city="Алматы",
                observed_at=observed_at,
                is_active=True,
            )
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO products
                (id, shop, title, category, url, current_price, first_seen_price,
                 min_price, max_price, canonical_key, created_at, updated_at, city, is_active)
            VALUES (?, 'Shop 1', ?, 'Одежда', ?, 10000, 10000, 10000, 10000, ?, ?, ?, 'Алматы', 1)
            """,
            (pid, "Product " + pid, "https://shop1.kz/" + pid, "key_" + pid, observed_at, observed_at),
        )
        conn.commit()

    def test_reap_deactivates_paired_legacy_products_and_keeps_fresh(self):
        conn = get_connection()
        try:
            old_ts = _utc_iso(self.now - datetime.timedelta(days=10))
            fresh_ts = _utc_iso(self.now - datetime.timedelta(hours=2))
            self._seed_pair(conn, "legacy_old", old_ts)
            self._seed_pair(conn, "legacy_fresh", fresh_ts)

            dry = reap_expired_offers(conn, now=self.now, dry_run=True)
            self.assertEqual(dry["total_reaped"], 1)
            self.assertEqual(dry["legacy_products_deactivated"], 0)
            self.assertEqual(
                conn.execute("SELECT is_active FROM products WHERE id = 'legacy_old'").fetchone()[0],
                1,
            )

            res = reap_expired_offers(conn, now=self.now, dry_run=False)
            self.assertEqual(res["total_reaped"], 1)
            self.assertEqual(res["legacy_products_deactivated"], 1)
            self.assertEqual(
                conn.execute("SELECT is_active FROM offers WHERE id = 'off_legacy_old'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT is_active FROM products WHERE id = 'legacy_old'").fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT is_active FROM products WHERE id = 'legacy_fresh'").fetchone()[0],
                1,
            )
        finally:
            conn.close()

    def test_reap_skips_offer_refreshed_by_scanner_between_read_and_write(self):
        """FR-01: сканер во втором соединении подтверждает предложение после выборки очистки."""
        import domain.freshness_v2 as fv2

        old_ts = _utc_iso(self.now - datetime.timedelta(days=10))
        fresh_ts = _utc_iso(self.now - datetime.timedelta(minutes=5))
        conn = get_connection()
        try:
            self._seed_pair(conn, "legacy_race", old_ts)
            self._seed_pair(conn, "legacy_stale", old_ts)
        finally:
            conn.close()

        real_eval = fv2.evaluate_offer_freshness
        refreshed = {"done": False}

        def eval_with_scanner(*args, **kwargs):
            if not refreshed["done"]:
                refreshed["done"] = True
                scanner = sqlite3.connect(str(self.db_path), timeout=5)
                try:
                    scanner.execute("UPDATE offers SET observed_at = ? WHERE id = 'off_legacy_race'", (fresh_ts,))
                    scanner.execute("UPDATE products SET updated_at = ? WHERE id = 'legacy_race'", (fresh_ts,))
                    scanner.commit()
                finally:
                    scanner.close()
            return real_eval(*args, **kwargs)

        conn = get_connection()
        try:
            with patch.object(fv2, "evaluate_offer_freshness", side_effect=eval_with_scanner):
                res = reap_expired_offers(conn, now=self.now, dry_run=False)
        finally:
            conn.close()

        self.assertTrue(refreshed["done"])
        self.assertEqual(res["scanned_candidates"], 2)
        self.assertEqual(res["total_reaped"], 1)
        self.assertEqual(res["skipped_refreshed"], 1)
        self.assertEqual(res["legacy_products_deactivated"], 1)
        self.assertEqual(res["by_reason"]["source_threshold_expired"], 1)
        self.assertEqual(sum(res["by_source_type"].values()), 1)
        self.assertEqual(res["reaped_ids_sample"], ["off_legacy_stale"])

        check = sqlite3.connect(str(self.db_path))
        try:
            state = dict(check.execute("SELECT id, is_active FROM offers WHERE id LIKE 'off_legacy_%'").fetchall())
            products = dict(check.execute("SELECT id, is_active FROM products WHERE id LIKE 'legacy_%'").fetchall())
        finally:
            check.close()
        self.assertEqual(state, {"off_legacy_race": 1, "off_legacy_stale": 0})
        self.assertEqual(products, {"legacy_race": 1, "legacy_stale": 0})

    def test_reap_skips_offer_deactivated_or_ttl_extended_concurrently(self):
        """FR-01: новый expires_at или уже снятое предложение не считаются списанными этой очисткой."""
        import domain.freshness_v2 as fv2

        old_ts = _utc_iso(self.now - datetime.timedelta(days=10))
        conn = get_connection()
        try:
            self._seed_pair(conn, "legacy_ttl", old_ts)
            self._seed_pair(conn, "legacy_gone", old_ts)
        finally:
            conn.close()

        real_eval = fv2.evaluate_offer_freshness
        state = {"done": False}

        def eval_with_scanner(*args, **kwargs):
            if not state["done"]:
                state["done"] = True
                other = sqlite3.connect(str(self.db_path), timeout=5)
                try:
                    future = _utc_iso(self.now + datetime.timedelta(days=3))
                    other.execute("UPDATE offers SET expires_at = ? WHERE id = 'off_legacy_ttl'", (future,))
                    other.execute("UPDATE offers SET is_active = 0 WHERE id = 'off_legacy_gone'")
                    other.commit()
                finally:
                    other.close()
            return real_eval(*args, **kwargs)

        conn = get_connection()
        try:
            with patch.object(fv2, "evaluate_offer_freshness", side_effect=eval_with_scanner):
                res = reap_expired_offers(conn, now=self.now, dry_run=False)
            self.assertEqual(res["total_reaped"], 0)
            self.assertEqual(res["skipped_refreshed"], 2)
            self.assertEqual(res["legacy_products_deactivated"], 0)
            self.assertEqual(
                conn.execute("SELECT is_active FROM offers WHERE id = 'off_legacy_ttl'").fetchone()[0], 1)
            self.assertEqual(
                dict(conn.execute("SELECT id, is_active FROM products WHERE id LIKE 'legacy_%'").fetchall()),
                {"legacy_ttl": 1, "legacy_gone": 1},
            )
        finally:
            conn.close()

    def test_reap_works_without_legacy_products_table(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        try:
            init_schema_v2(conn)
            s_repo = SellerRepository(conn)
            s_repo.save_or_update(Seller(id="s1", slug="shop-1", name="Shop 1"))
            p_repo = ProductRepository(conn)
            p_repo.save_or_update(CanonicalProduct(id="cp1", canonical_key="k1", title="T1", category="Одежда"))
            ch_repo = ChannelRepository(conn)
            ch_repo.save_or_update(Channel(id="ch_web", seller_id="s1", channel_type=ChannelType.WEBSITE, name="Site"))
            o_repo = OfferRepository(conn)
            o_repo.save_or_update(
                Offer(
                    id="off_only_v2",
                    product_id="cp1",
                    seller_id="s1",
                    channel_id="ch_web",
                    external_sku="SKU-1",
                    url="https://shop1.kz/1",
                    price=10000.0,
                    city="Алматы",
                    observed_at=_utc_iso(self.now - datetime.timedelta(days=10)),
                    is_active=True,
                )
            )
            conn.commit()
            res = reap_expired_offers(conn, now=self.now, dry_run=False)
            self.assertEqual(res["total_reaped"], 1)
            self.assertEqual(res["legacy_products_deactivated"], 0)
        finally:
            conn.close()

