"""Регрессии полного аудита 6.31.5 (C01, C02) и кнопки «Сравнить цены». Без сети, на временной БД.

Повторяет docs/audit_evidence/2026-11-11-full-v6315/reproduce_api.py и критерии
FULL_AUDIT_2026-11-11_V6315_CODEX.md.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import auth
import database
import notifier
from domain import barcode_resolver as barcode
from web import server
from tests.web.test_audit_v6313_fixes import BOT_TOKEN, UID, _seed, _sign

TITLE = "Сок Добрый яблочный 1 л"
CODE = "4870237159941"


def _products(rows):
    with database.get_connection() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO canonical_products(id, canonical_key, title, category, created_at, updated_at) "
            "VALUES (?, ?, ?, 'grocery', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [(pid, pid, title) for pid, title in rows])
        conn.commit()


def _cleanup(prefixes):
    with database.get_connection() as conn:
        for p in prefixes:
            conn.execute("DELETE FROM canonical_products WHERE id LIKE ?", (p + "%",))
        conn.execute("DELETE FROM product_barcodes WHERE barcode = ?", (CODE,))
        conn.execute("DELETE FROM barcode_cache WHERE barcode = ?", (CODE,))
        conn.commit()


class BarcodeFullCatalogTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        _cleanup(["c01_"])

    def tearDown(self):
        _cleanup(["c01_"])

    def test_second_exact_variant_beyond_500_fts_rows_blocks_auto_link(self):
        rows = [("c01_candidate1", TITLE)]
        rows += [(f"c01_decoy{i:03d}", f"{TITLE} дополнительные сведения") for i in range(499)]
        rows.append(("c01_candidate2", TITLE))
        _products(rows)
        found, complete = barcode.find_exact_catalog_matches(TITLE, with_completeness=True)
        self.assertTrue(complete)
        self.assertEqual(sorted(r["id"] for r in found), ["c01_candidate1", "c01_candidate2"])
        data = {"items": [{"product": {"id": "c01_candidate1", "title": TITLE}}]}
        asyncio.run(server._apply_barcode_matches(data, CODE, {"title": TITLE, "source": "searxng"}))
        self.assertEqual(data["barcode_match"], "ambiguous")
        self.assertEqual(barcode.get_barcode_product_ids(CODE), [])
        self.assertNotIn("barcode", data["items"][0]["product"])

    def test_incomplete_scan_never_auto_links(self):
        _products([("c01_only", TITLE)] + [(f"c01_decoy{i}", f"{TITLE} другое") for i in range(5)])
        with patch.object(barcode, "EXACT_MATCH_SCAN_CAP", 3):
            found, complete = barcode.find_exact_catalog_matches(TITLE, with_completeness=True)
            self.assertFalse(complete)
            data = {"items": []}
            asyncio.run(server._apply_barcode_matches(data, CODE, {"title": TITLE, "source": "searxng"}))
        self.assertNotEqual(data["barcode_match"], "auto_linked")
        self.assertEqual(barcode.get_barcode_product_ids(CODE), [])
        # Без ограничения — единственный вариант привязывается
        data = {"items": []}
        asyncio.run(server._apply_barcode_matches(data, CODE, {"title": TITLE, "source": "searxng"}))
        self.assertEqual(data["barcode_match"], "auto_linked")
        self.assertEqual(barcode.get_barcode_product_ids(CODE), ["c01_only"])

    def test_clothing_sizes_are_different_variants(self):
        s = "Футболка Nike Sportswear белая S"
        m = "Футболка Nike Sportswear белая M"
        self.assertFalse(barcode.is_exact_variant_match(s, m))
        self.assertTrue(barcode.is_exact_variant_match(s, "футболка NIKE sportswear белая s"))
        _products([("c01_s", s), ("c01_m", m)])
        self.assertEqual([r["id"] for r in barcode.find_exact_catalog_matches(s)], ["c01_s"])
        # Знак умножения не становится различающим словом
        self.assertTrue(barcode.is_exact_variant_match("Салфетки Tompi 2 x 100 шт", "Салфетки Tompi 2x100 шт"))

    def test_startup_repair_drops_existing_auto_link_when_catalog_is_ambiguous(self):
        _products([("c01_candidate1", TITLE), ("c01_candidate2", TITLE)])
        with database.get_connection() as conn:
            conn.execute("DROP INDEX IF EXISTS ux_product_barcodes_barcode")
            conn.execute("INSERT INTO product_barcodes(product_id, barcode, source) VALUES ('c01_candidate1', ?, "
                         "'search_match')", (CODE,))
            conn.execute("INSERT OR REPLACE INTO barcode_cache(barcode, title) VALUES (?, ?)", (CODE, TITLE))
            conn.commit()
        self.assertEqual(barcode.repair_unverified_auto_links(), 1)
        self.assertEqual(barcode.get_barcode_product_ids(CODE), [])
        # Подтверждённая человеком связь не снимается
        barcode.link_barcode_to_products("c01_candidate1", CODE, "manual_api")
        barcode.repair_unverified_auto_links()
        self.assertEqual(barcode.get_barcode_product_ids(CODE), ["c01_candidate1"])


class OfferShareTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _seed()
        with database.get_connection() as conn:
            conn.execute("DELETE FROM products WHERE id = 'c02_legacy'")
            conn.execute(
                "INSERT INTO products(id, title, shop, url, current_price, first_seen_price, min_price, max_price, "
                "is_active, city, updated_at) VALUES ('c02_legacy', 'Synthetic product', 'Synthetic shop', "
                "'https://example.invalid/legacy', 80000, 80000, 80000, 80000, 1, 'Алматы', CURRENT_TIMESTAMP)")
            conn.commit()
        app = web.Application(middlewares=[auth.auth_middleware])
        app.add_routes(server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url("")).rstrip("/")
        self.patches = [
            patch.object(auth, "expected_origin", return_value=self.origin),
            patch.object(auth, "MAINTENANCE_DEV_MODE", False),
            patch.object(server, "get_bot_token", return_value=BOT_TOKEN),
        ]
        for p in self.patches:
            p.start()
        server.prepared_message_limiter._calls.clear()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.client.close()

    async def _share(self, **body):
        body.setdefault("init_data", _sign(UID))
        with patch.object(notifier, "save_prepared_inline_message", return_value="fixture") as send:
            r = await self.client.post("/api/telegram/prepared-message", headers={"Origin": self.origin}, json=body)
        return r, send

    async def test_offer_page_shares_the_opened_offer_despite_ui_city(self):
        r, send = await self._share(canonical_product_id="b02_target", city="Алматы",
                                    offer_id="b02_astana_2@astana", offer_selected=True)
        self.assertEqual(r.status, 200)
        _, _, price, _, url, city, _, path, start = send.call_args.args
        self.assertEqual((price, url, city), (130000, "https://example.invalid/astana2", "Астана"))
        self.assertEqual(path, "/offer/b02_astana_2%40astana")
        import base64
        self.assertEqual(base64.urlsafe_b64decode(start[2:] + "==").decode(), "b02_astana_2@astana")
        self.assertRegex(start, r"^o_[A-Za-z0-9_-]{1,62}$")

    async def test_unavailable_opened_offer_is_not_replaced(self):
        for bad in ("b02_expired", "b02_astana_gone", "b02_astana_stale", "missing@astana"):
            r, send = await self._share(canonical_product_id="b02_target", city="Астана",
                                        offer_id=bad, offer_selected=True)
            self.assertEqual(r.status, 404, bad)
            send.assert_not_called()

    async def test_product_card_still_picks_city_offer_and_links_product_page(self):
        r, send = await self._share(canonical_product_id="b02_target", city="Алматы", offer_id="b02_astana_2@astana")
        self.assertEqual(r.status, 200)
        args = send.call_args.args
        self.assertEqual((args[2], args[5]), (80000, "Алматы"))
        self.assertEqual(args[7], "/product/b02_target?city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B")
        self.assertEqual(args[8], "p_b02_target")
        r, send = await self._share(canonical_product_id="b02_target", city="Все")
        self.assertEqual(send.call_args.args[7], "/product/b02_target")

    async def test_legacy_record_region_is_its_own_city(self):
        r, send = await self._share(product_id="c02_legacy", city="Астана")
        self.assertEqual(r.status, 200)
        args = send.call_args.args
        self.assertEqual((args[2], args[4], args[5]), (80000, "https://example.invalid/legacy", "Алматы"))
        self.assertEqual(args[7], "/search?q=Synthetic+product")


class CompareButtonTest(unittest.TestCase):
    def setUp(self):
        self._cache = dict(auth._bot_username_cache)
        self._token = patch.object(auth, "get_bot_token", return_value="tok")
        self._token.start()
        self.addCleanup(self._token.stop)
        auth._bot_username_cache.update(token="tok", username="kzph_test_bot", main_web_app=False)

    def tearDown(self):
        auth._bot_username_cache.clear()
        auth._bot_username_cache.update(self._cache)

    def test_compare_button_opens_mini_app_inside_telegram(self):
        """Владелец: https-ссылка открывалась во внешнем браузере и просила войти."""
        auth._bot_username_cache["main_web_app"] = True
        with patch.object(auth, "get_bot_token", return_value="tok"):
            self.assertEqual(self._buttons("/offer/x", "o_eA"), "https://t.me/kzph_test_bot?startapp=o_eA")
            # Недопустимый параметр — обратно на страницу сайта
            self.assertEqual(self._buttons("/offer/x", "o_a@b"), "https://shdev.example/offer/x")
        auth._bot_username_cache["main_web_app"] = False
        with patch.object(auth, "get_bot_token", return_value="tok"):
            self.assertEqual(self._buttons("/offer/x", "o_eA"), "https://shdev.example/offer/x")

    def _buttons(self, compare_path, start_param=""):
        calls = []
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"id": "prep"}}
        with patch("notifier.get_bot_token", return_value="tok"), \
                patch.object(notifier, "APP_URL", "https://shdev.example"), \
                patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}), \
                patch.object(notifier, "telegram_api", side_effect=lambda m, p: calls.append((m, p)) or resp):
            notifier.save_prepared_inline_message(1, "T", 1, "S", "https://shop.kz/x", "Астана", "", compare_path,
                                                  start_param)
        rows = calls[0][1]["result"]["reply_markup"]["inline_keyboard"]
        return [b for row in rows for b in row if b.get("text") == "📊 Сравнить цены"][0]["url"]

    def test_compare_button_opens_product_page(self):
        self.assertEqual(self._buttons("/offer/a%40astana"), "https://shdev.example/offer/a%40astana")
        self.assertEqual(self._buttons("/product/p1?city=x"), "https://shdev.example/product/p1?city=x")

    def test_network_error_is_retried_once(self):
        import requests
        ok = Mock(status_code=200)
        ok.json.return_value = {"ok": True, "result": {"id": "prep"}}
        attempts = []

        def flaky(method, payload):
            attempts.append(method)
            if len(attempts) == 1:
                raise requests.ConnectionError("reset")
            return ok

        with patch("notifier.get_bot_token", return_value="tok"), \
                patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}), \
                patch.object(notifier.time, "sleep"), \
                patch.object(notifier, "telegram_api", side_effect=flaky):
            self.assertEqual(notifier.save_prepared_inline_message(1, "T", 1, "S", "https://shop.kz/x"), "prep")
        self.assertEqual(len(attempts), 2)
        bad = Mock(status_code=400, text="Bad Request")
        with patch("notifier.get_bot_token", return_value="tok"), \
                patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}), \
                patch.object(notifier, "telegram_api", return_value=bad) as api:
            self.assertIsNone(notifier.save_prepared_inline_message(1, "T", 1, "S", "https://shop.kz/x"))
        self.assertEqual(api.call_count, 1)

    def test_compare_button_rejects_foreign_targets(self):
        for bad in ("", "//evil.example/x", "https://evil.example"):
            self.assertEqual(self._buttons(bad), "https://shdev.example")


if __name__ == "__main__":
    unittest.main()
