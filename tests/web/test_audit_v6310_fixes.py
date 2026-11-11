"""Регрессии полного аудита 6.31.0 (A01–A03, A05, A08). Без сети, на временной БД.

Повторяет воспроизведения из docs/audit_evidence/2026-11-11-full-v6310/reproduce_api.py
и проверяет критерии приёмки из отчёта FULL_AUDIT_2026-11-11_V6310_CODEX.md.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import json
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import auth
import database
import notifier
from domain import barcode_resolver
from web import server

BARCODE = "4870237159941"
UID = 700000001
ADMIN = 700000099


def _seed_catalog():
    database.init_db()
    with database.get_connection() as conn:
        for table in ("product_barcodes", "barcode_cache"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.execute("DELETE FROM offers WHERE product_id LIKE 'audit_%'")
        conn.execute("DELETE FROM canonical_products WHERE id LIKE 'audit_%'")
        conn.executemany(
            "INSERT INTO canonical_products(id, canonical_key, title, brand, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'grocery', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [
                ("audit_tompi_aroma", "k_a", "Влажные салфетки Tompi с ароматом 100 шт", "Tompi"),
                ("audit_tompi_plain", "k_b", "Влажные салфетки Tompi Без аромата 100 шт", "Tompi"),
                ("audit_tompi_100", "k_c", "Влажные салфетки Tompi 100 шт", "Tompi"),
                ("audit_other_10", "k_d", "Влажные салфетки Другой бренд 10 шт", "Другой"),
            ],
        )
        conn.commit()


def _raw_links(rows):
    """Связи в обход проверок записи: моделирует данные, оставшиеся от прежних версий."""
    with database.get_connection() as conn:
        conn.execute("DROP INDEX IF EXISTS ux_product_barcodes_barcode")
        conn.executemany("INSERT OR IGNORE INTO product_barcodes(product_id, barcode, source) VALUES (?, ?, ?)", rows)
        conn.commit()


class _Resp:
    outcome = "FOUND"

    def __init__(self, ids):
        self.ids = ids

    def to_dict(self):
        titles = {
            "audit_tompi_aroma": "Влажные салфетки Tompi с ароматом 100 шт",
            "audit_tompi_plain": "Влажные салфетки Tompi Без аромата 100 шт",
            "audit_tompi_100": "Влажные салфетки Tompi 100 шт",
            "audit_other_10": "Влажные салфетки Другой бренд 10 шт",
        }
        return {"items": [{"product": {"id": i, "title": titles[i]}} for i in self.ids]}


class AuditFixesHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _seed_catalog()
        app = web.Application(middlewares=[auth.auth_middleware])
        app.add_routes(server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url("")).rstrip("/")
        self._patches = [patch.object(auth, "expected_origin", return_value=self.origin)]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        await self.client.close()

    def _as(self, user_id):
        return patch.object(auth, "get_session_user", return_value={"id": user_id, "settings": {"is_approved": True}})

    async def _post(self, path, body, user_id=None):
        headers = {"Origin": self.origin}
        if user_id is not None:
            headers["Cookie"] = f"{auth.SESSION_COOKIE}=test"
        return await self.client.post(path, headers=headers, json=body)

    # --- A01 ---
    async def test_a01_guest_and_user_cannot_edit_catalog_barcodes(self):
        for closed in (False, True):
            with patch.object(auth, "MAINTENANCE_DEV_MODE", closed):
                r = await self._post("/api/v2/products/audit_tompi_100/barcode", {"barcode": BARCODE})
                self.assertIn(r.status, (401, 403))
                with self._as(UID):
                    r = await self._post("/api/v2/products/audit_tompi_100/barcode", {"barcode": BARCODE}, UID)
                    self.assertEqual(r.status, 403)
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])

    async def test_a01_admin_unknown_product_404_and_conflict_409(self):
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {ADMIN}), self._as(ADMIN):
            r = await self._post("/api/v2/products/audit_missing/barcode", {"barcode": BARCODE}, ADMIN)
            self.assertEqual(r.status, 404)
            self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])

            r = await self._post("/api/v2/products/audit_tompi_aroma/barcode", {"barcode": BARCODE}, ADMIN)
            self.assertEqual(r.status, 200)
            r = await self._post("/api/v2/products/audit_tompi_plain/barcode", {"barcode": BARCODE}, ADMIN)
            self.assertEqual(r.status, 409)
            self.assertEqual((await r.json())["conflict_product_ids"], ["audit_tompi_aroma"])
            self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_tompi_aroma"])

            # Явная замена переносит код одной транзакцией
            r = await self._post("/api/v2/products/audit_tompi_plain/barcode", {"barcode": BARCODE, "replace": True}, ADMIN)
            self.assertEqual(r.status, 200)
            self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_tompi_plain"])
            with database.get_connection() as conn:
                attrs = json.loads(conn.execute("SELECT attributes_json FROM canonical_products WHERE id='audit_tompi_aroma'").fetchone()[0])
                cache = conn.execute("SELECT title FROM barcode_cache WHERE barcode=?", (BARCODE,)).fetchone()[0]
            self.assertNotIn("barcode", attrs)
            # Проверенная связь перезаписывает кэш распознавания
            self.assertEqual(cache, "Влажные салфетки Tompi Без аромата 100 шт")

    # --- A02 ---
    async def _search(self, ids, title):
        async def resolver(code):
            return {"title": title, "source": "searxng"}
        with patch("local_search.enabled", return_value=False), \
             patch.object(barcode_resolver, "resolve_barcode", side_effect=resolver), \
             patch.object(server, "_run_search_v2_sync", return_value=_Resp(ids)):
            r = await self.client.get("/api/v2/search", params={"q": BARCODE})
            self.assertEqual(r.status, 200)
            return await r.json()

    async def test_a02_ambiguous_variants_are_candidates_not_links(self):
        # Нет точного совпадения в каталоге — похожие карточки выдачи только кандидаты
        data = await self._search(["audit_tompi_aroma", "audit_tompi_plain", "audit_other_10"],
                                  "Влажные салфетки Tompi 200 шт")
        self.assertEqual(data["barcode_match"], "ambiguous")
        self.assertTrue(all("barcode" not in it for it in data["items"]))
        self.assertEqual({c["id"] for c in data["barcode_candidates"]}, {"audit_tompi_aroma", "audit_tompi_plain", "audit_other_10"})
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])

    async def test_b01_two_exact_cards_in_catalog_are_ambiguous(self):
        with database.get_connection() as conn:
            conn.execute("INSERT INTO canonical_products(id, canonical_key, title, brand, category, created_at, updated_at) "
                         "VALUES ('audit_tompi_100_dup', 'k_e', 'Tompi влажные салфетки 100 шт', 'Tompi', 'grocery', "
                         "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            conn.commit()
        try:
            # Вторая точная карточка не попала на страницу, но однозначности всё равно нет
            data = await self._search(["audit_tompi_100"], "Влажные салфетки Tompi 100 шт")
            self.assertEqual(data["barcode_match"], "ambiguous")
            self.assertEqual({c["id"] for c in data["barcode_candidates"]}, {"audit_tompi_100", "audit_tompi_100_dup"})
            self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])
        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM canonical_products WHERE id = 'audit_tompi_100_dup'")
                conn.commit()

    async def test_b01_swapped_quantities_never_link_whatever_page(self):
        title = "Tompi салфетки 2 упаковки по 100 шт"
        wrong = "Tompi салфетки 100 упаковок по 2 шт"
        self.assertFalse(barcode_resolver.is_exact_variant_match(title, wrong))
        with database.get_connection() as conn:
            conn.executemany("INSERT INTO canonical_products(id, canonical_key, title, category, created_at, updated_at) "
                             "VALUES (?, ?, ?, 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                             [("audit_right", "k_r", title), ("audit_wrong", "k_w", wrong)])
            conn.commit()
        titles = {"audit_right": title, "audit_wrong": wrong}

        class Page:
            outcome = "FOUND"

            def __init__(self, ids):
                self.ids = ids

            def to_dict(self):
                return {"items": [{"product": {"id": i, "title": titles[i]}} for i in self.ids]}

        async def resolver(code):
            return {"title": title, "source": "searxng"}
        try:
            results = []
            for page_ids in (["audit_wrong"], ["audit_right", "audit_wrong"], ["audit_wrong", "audit_right"]):
                with patch("local_search.enabled", return_value=False), \
                     patch.object(barcode_resolver, "resolve_barcode", side_effect=resolver), \
                     patch.object(server, "_run_search_v2_sync", return_value=Page(page_ids)):
                    r = await self.client.get("/api/v2/search", params={"q": BARCODE, "limit": len(page_ids)})
                    results.append(await r.json())
            # Решение одно при любой странице: код за правильной фасовкой, неправильная без бейджа
            self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_right"])
            self.assertEqual(results[0]["barcode_match"], "auto_linked")
            self.assertTrue(all("barcode" not in it for it in results[0]["items"]))
            for data in results[1:]:
                self.assertEqual(data["barcode_match"], "linked")
                badged = [it["product"]["id"] for it in data["items"] if it.get("barcode")]
                self.assertEqual(badged, ["audit_right"])
        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM product_barcodes WHERE product_id IN ('audit_right', 'audit_wrong')")
                conn.execute("DELETE FROM canonical_products WHERE id IN ('audit_right', 'audit_wrong')")
                conn.commit()

    async def test_b01_parallel_admin_requests_link_one_card(self):
        from threading import Barrier
        barrier = Barrier(2, timeout=3)
        read_links = barcode_resolver.get_barcode_product_ids

        def read_before_either_write(code):
            ids = read_links(code)
            barrier.wait()
            return ids
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {ADMIN}), self._as(ADMIN), \
             patch.object(barcode_resolver, "get_barcode_product_ids", side_effect=read_before_either_write):
            replies = await asyncio.gather(
                self._post("/api/v2/products/audit_tompi_aroma/barcode", {"barcode": BARCODE}, ADMIN),
                self._post("/api/v2/products/audit_tompi_plain/barcode", {"barcode": BARCODE}, ADMIN))
        self.assertEqual(sorted(r.status for r in replies), [200, 409])
        self.assertEqual(len(read_links(BARCODE)), 1)

    async def test_a02_single_exact_variant_is_linked(self):
        data = await self._search(["audit_tompi_100", "audit_other_10", "audit_tompi_aroma"],
                                  "Tompi влажные салфетки 100 шт")
        self.assertEqual(data["barcode_match"], "auto_linked")
        self.assertEqual(data["items"][0].get("barcode"), BARCODE)
        self.assertNotIn("barcode", data["items"][1])
        self.assertNotIn("barcode", data["items"][2])
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_tompi_100"])

        # Код уже привязан: второй точный вариант не получает связь
        data = await self._search(["audit_tompi_aroma"], "Влажные салфетки Tompi с ароматом 100 шт")
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_tompi_100"])
        self.assertNotIn("barcode", data["items"][0])

    async def test_a02_verified_link_wins_over_external_cache(self):
        barcode_resolver.save_cached_barcode(BARCODE, "Ошибочный внешний товар")
        barcode_resolver.link_barcode_to_products("audit_tompi_plain", BARCODE, "manual_api")
        res = await barcode_resolver.resolve_barcode(BARCODE)
        self.assertEqual(res["product_id"], "audit_tompi_plain")
        self.assertEqual(res["source"], "product_barcodes")

    def test_a02_repair_removes_ambiguous_auto_links_keeps_manual(self):
        barcode_resolver.save_cached_barcode(BARCODE, "Влажные салфетки Tompi 100 шт")
        # Данные до 6.31.1: один код автоматически записан за четырьмя карточками
        _raw_links([(pid, BARCODE, "search_match") for pid in
                    ("audit_tompi_aroma", "audit_tompi_plain", "audit_tompi_100", "audit_other_10")])
        barcode_resolver.link_barcode_to_products("audit_tompi_100", "4006381333931", "search_match")
        barcode_resolver.save_cached_barcode("4006381333931", "Влажные салфетки Tompi 100 шт")
        barcode_resolver.link_barcode_to_products("audit_other_10", "96385074", "manual_api")
        removed = barcode_resolver.repair_unverified_auto_links()
        self.assertEqual(removed, 4)
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])
        self.assertEqual(barcode_resolver.get_barcode_product_ids("4006381333931"), ["audit_tompi_100"])
        self.assertEqual(barcode_resolver.get_barcode_product_ids("96385074"), ["audit_other_10"])
        self.assertEqual(barcode_resolver.repair_unverified_auto_links(), 0)

    def test_a02_repair_keeps_verified_photo_and_drops_other_auto_links(self):
        # Снимок стенда: одна связь сверена по фото, ещё три записаны автопоиском
        barcode_resolver.save_cached_barcode(BARCODE, "Влажные салфетки Tompi с ароматом 100 шт")
        _raw_links([("audit_tompi_aroma", BARCODE, "verified_photo"), ("audit_tompi_plain", BARCODE, "search_match"),
                    ("audit_tompi_100", BARCODE, "search_match")])
        self.assertEqual(barcode_resolver.repair_unverified_auto_links(), 2)
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), ["audit_tompi_aroma"])
        # После починки индекс «один код — одна карточка» восстановлен
        with database.get_connection() as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'ux_product_barcodes_barcode'").fetchone())

    def test_a02_variant_match_rules(self):
        m = barcode_resolver.is_exact_variant_match
        self.assertTrue(m("Влажные салфетки Tompi 100 шт", "Tompi салфетки влажные, 100 шт."))
        self.assertTrue(m("Tompi салфетки 100шт", "Tompi салфетки 100 штук"))
        self.assertFalse(m("Tompi салфетки 2 упаковки по 100 шт", "Tompi салфетки 100 упаковок по 2 шт"))
        self.assertFalse(m("Смартфон X 8/256 GB", "Смартфон X 8/128 GB"))
        self.assertFalse(m("Влажные салфетки Tompi 100 шт", "Влажные салфетки Tompi 10 шт"))
        self.assertFalse(m("Tompi с ароматом 100 шт", "Tompi Без аромата 100 шт"))
        self.assertFalse(m("Влажные салфетки Tompi 100 шт", "Влажные салфетки Другой бренд 100 шт"))

    def test_a02_link_skips_nonexistent_products(self):
        self.assertFalse(barcode_resolver.link_barcode_to_products("audit_missing", BARCODE, "search_match"))
        self.assertEqual(barcode_resolver.get_barcode_product_ids(BARCODE), [])

    # --- A08 ---
    async def test_a08_unicode_digits_are_not_barcodes(self):
        self.assertFalse(barcode_resolver.is_valid_barcode("²" * 8))
        self.assertFalse(barcode_resolver.is_valid_barcode("٤٨٧٠٢٣٧١٥٩٩٤١"))
        with patch("local_search.enabled", return_value=False), \
             patch.object(server, "_run_search_v2_sync", return_value=_Resp([])):
            r = await self.client.get("/api/v2/search", params={"q": "²" * 8})
        self.assertNotEqual(r.status, 500)

    def test_a08_upca_is_stored_as_gtin13(self):
        self.assertEqual(barcode_resolver.normalize_barcode("036000291452"), "0036000291452")
        barcode_resolver.link_barcode_to_products("audit_tompi_100", "036000291452", "manual_api")
        self.assertEqual(barcode_resolver.get_barcode_product_ids("0036000291452"), ["audit_tompi_100"])
        self.assertEqual(barcode_resolver.get_barcode_product_ids("036000291452"), ["audit_tompi_100"])

    # --- A03 ---
    async def test_a03_guest_and_body_user_id_never_reach_bot_api(self):
        with patch.object(notifier, "save_prepared_inline_message", return_value="prep") as send:
            with patch.object(auth, "MAINTENANCE_DEV_MODE", True):
                r = await self._post("/api/telegram/prepared-message",
                                     {"user_id": UID, "title": "X", "price": 1, "url": "https://example.invalid"})
                self.assertIn(r.status, (401, 403))
            with patch.object(auth, "MAINTENANCE_DEV_MODE", False):
                r = await self._post("/api/telegram/prepared-message",
                                     {"user_id": UID, "title": "X", "price": 1, "url": "https://example.invalid"})
                self.assertEqual(r.status, 401)
                # Подделанный initData не принимается
                with patch.object(server, "get_bot_token", return_value="123:abc"):
                    r = await self._post("/api/telegram/prepared-message",
                                         {"init_data": "user=%7B%22id%22%3A1%7D&hash=deadbeef", "product_id": "audit_x"})
                self.assertEqual(r.status, 401)
            self.assertEqual(send.call_count, 0)

    async def test_a03_card_comes_from_catalog_for_session_user(self):
        with database.get_connection() as conn:
            conn.execute("INSERT OR IGNORE INTO sellers(id, slug, name, created_at, updated_at) VALUES "
                         "('audit_seller', 'audit-seller', 'Магазин Аудит', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            conn.execute("INSERT OR IGNORE INTO channels(id, seller_id, channel_type, name, created_at) VALUES "
                         "('audit_ch', 'audit_seller', 'website', 'site', CURRENT_TIMESTAMP)")
            conn.execute(
                "INSERT INTO offers(id, product_id, seller_id, channel_id, external_sku, url, image_url, price, city, "
                "observed_at, created_at, updated_at) VALUES ('audit_off', 'audit_tompi_100', 'audit_seller', 'audit_ch', "
                "'sku', 'https://shop.example/tompi', '', 990, 'Астана', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
            conn.commit()
        with patch.object(notifier, "save_prepared_inline_message", return_value="prep") as send, \
             patch.object(auth, "MAINTENANCE_DEV_MODE", False), self._as(UID):
            r = await self._post("/api/telegram/prepared-message",
                                 {"user_id": UID + 5, "canonical_product_id": "audit_tompi_100",
                                  "title": "Подмена", "price": 1, "url": "https://evil.example"}, UID)
            self.assertEqual(r.status, 200)
            args = send.call_args.args
            self.assertEqual(args[0], UID)
            self.assertEqual(args[1], "Влажные салфетки Tompi 100 шт")
            self.assertEqual(args[2], 990)
            self.assertEqual(args[3], "Магазин Аудит")
            self.assertEqual(args[4], "https://shop.example/tompi")
            r = await self._post("/api/telegram/prepared-message", {"canonical_product_id": "audit_missing"}, UID)
            self.assertEqual(r.status, 404)
            r = await self._post("/api/telegram/prepared-message", {"canonical_product_id": "x" * 500}, UID)
            self.assertEqual(r.status, 400)
            self.assertEqual(send.call_count, 1)


class DeepLinkTest(unittest.IsolatedAsyncioTestCase):
    """A07: повторные гостевые GET не множат записи, размер полей ограничен, истёкшие строки удаляются."""

    async def test_repeated_requests_reuse_one_row_and_limits(self):
        database.init_db()
        with database.get_connection() as conn:
            conn.execute("DELETE FROM bot_deep_links")
            conn.execute("INSERT INTO bot_deep_links(token, action, payload_json, created_at, expires_at) "
                         "VALUES ('old', 'search', '{}', 0, 1)")
            conn.commit()
        app = web.Application(middlewares=[auth.auth_middleware])
        app.add_routes(server.routes)
        with patch.object(auth, "MAINTENANCE_DEV_MODE", True), \
                patch.object(server, "get_bot_username", return_value="SyntheticBot"):
            async with TestClient(TestServer(app)) as client:
                urls = []
                for _ in range(3):
                    r = await client.get("/api/telegram/deep-link", params={"action": "search", "target": "x" * 100})
                    self.assertEqual(r.status, 200)
                    urls.append((await r.json())["url"])
                self.assertEqual(len(set(urls)), 1)
                r = await client.get("/api/telegram/deep-link", params={"action": "search", "target": "x" * 5000})
                self.assertEqual(r.status, 400)
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bot_deep_links").fetchone()[0], 1)


class AccountDataTest(unittest.TestCase):
    """A05: темы Telegram и оценки AI входят в выгрузку и не остаются за удалённым ID."""

    def test_export_and_delete_cover_topics_and_ai_feedback(self):
        database.init_db()
        other = UID + 1
        for uid in (UID, other):
            database.upsert_telegram_user({"id": uid, "first_name": "Synthetic"})
            database.save_user_topic(uid, "alerts", 900 + uid % 10)
            database.save_ai_feedback(uid, "good")
        exported = database.export_user_data(UID)
        self.assertEqual([t["topic_type"] for t in exported["telegram_topics"]], ["alerts"])
        self.assertEqual([f["rating"] for f in exported["ai_feedback"]], ["good"])
        before = database.get_ai_feedback_stats().get("good", 0)
        counts = database.delete_user_account(UID)
        self.assertEqual(counts["telegram_topics"], 1)
        self.assertEqual(counts["ai_feedback_anonymized"], 1)
        with database.get_connection() as conn:
            left = [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (UID,)).fetchone()[0]
                    for t in ("user_telegram_topics", "ai_feedback")]
            other_left = [conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (other,)).fetchone()[0]
                          for t in ("user_telegram_topics", "ai_feedback")]
        self.assertEqual(left, [0, 0])
        self.assertEqual(other_left, [1, 1], "данные другого аккаунта не затронуты")
        self.assertEqual(database.get_ai_feedback_stats().get("good", 0), before, "агрегат сохраняется")
        database.delete_user_account(other)


if __name__ == "__main__":
    unittest.main()
