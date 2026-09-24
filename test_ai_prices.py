"""Подсказка цен: источник, неоднозначность и обязательное подтверждение владельцем."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import ai_prices
import database
from config import DB_PATH

PAGE = """
<h2 id="gemini-2.5-flash">Gemini 2.5 Flash</h2>
<p>gemini-2.5-flash</p>
<table><tr><td>Standard</td><td>Free Tier</td><td>Paid Tier, per 1M tokens in USD</td></tr>
<tr><td>Input price</td><td>Free of charge</td><td>$0.30 (text / image / video)</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$2.50</td></tr>
<tr><td>Context caching price</td><td>Not available</td><td>$0.03</td></tr></table>
<h2 id="gemini-2.5-pro">Gemini 2.5 Pro</h2>
<table><tr><td>Standard</td></tr>
<tr><td>Input price</td><td>Free of charge</td><td>$1.25, prompts &lt;= 200k tokens $2.50, prompts &gt; 200k tokens</td></tr>
<tr><td>Output price (including thinking tokens)</td><td>Free of charge</td><td>$10.00</td></tr></table>
<h2 id="gemini-live-audio">Live</h2>
<table><tr><td>Standard</td></tr>
<tr><td>Input price</td><td>Free</td><td>$1.00 / minute</td></tr>
<tr><td>Output price</td><td>Free</td><td>$2.00 / minute</td></tr></table>
"""


class ParseTest(unittest.TestCase):
    def test_simple_model_is_read_exactly(self):
        price = ai_prices.parse_gemini(PAGE, "gemini-2.5-flash")
        self.assertEqual((price["input"], price["output"]), (0.30, 2.50))
        self.assertEqual(price["source"], ai_prices.GEMINI_PRICING_URL)
        self.assertIn("Standard", price["basis"])

    def test_context_caching_is_not_mistaken_for_the_output_price(self):
        """Первая же попытка разбора спутала кэширование с ценой ответа — это не должно повториться."""
        self.assertEqual(ai_prices.parse_gemini(PAGE, "gemini-2.5-flash")["output"], 2.50)

    def test_several_prices_in_a_row_are_marked_ambiguous(self):
        price = ai_prices.parse_gemini(PAGE, "gemini-2.5-pro")
        self.assertEqual(price["input"], 1.25)
        self.assertTrue(price["ambiguous"], "цена зависит от длины запроса — об этом надо сказать")
        self.assertIn("200k", price["raw"])

    def test_prices_per_minute_are_not_taken_as_per_million(self):
        self.assertIsNone(ai_prices.parse_gemini(PAGE, "gemini-live-audio"))

    def test_unknown_model_is_not_guessed(self):
        self.assertIsNone(ai_prices.parse_gemini(PAGE, "gemini-которой-нет"))


class SuggestTest(unittest.TestCase):
    def test_found_and_missing_are_separated(self):
        with patch.object(ai_prices, "fetch_page", return_value=PAGE):
            data = asyncio.run(ai_prices.suggest(["gemini-2.5-flash", "gemini-живой-звук"]))
        self.assertIn("gemini-2.5-flash", data["found"])
        self.assertEqual(data["missing"][0]["model"], "gemini-живой-звук")
        self.assertIn("не разобрал", data["missing"][0]["why"].replace("разобралась", "разобрал"))

    def test_openai_is_refused_honestly_not_guessed(self):
        data = asyncio.run(ai_prices.suggest(["gpt-5"]))
        self.assertEqual(data["found"], {})
        self.assertIn("в браузере", data["missing"][0]["why"])
        self.assertIn("openai", data["missing"][0]["source"])

    def test_unreachable_page_is_said_plainly(self):
        with patch.object(ai_prices, "fetch_page", return_value=None):
            data = asyncio.run(ai_prices.suggest(["gemini-2.5-flash"]))
        self.assertEqual(data["found"], {})
        self.assertIn("не открылась", data["missing"][0]["why"])

    def test_answer_carries_source_time_and_disclaimer(self):
        with patch.object(ai_prices, "fetch_page", return_value=PAGE):
            data = asyncio.run(ai_prices.suggest(["gemini-2.5-flash"]))
        self.assertTrue(data["fetched_at"].startswith("20"))
        self.assertIn("не из вашего аккаунта", data["disclaimer"])

    def test_nothing_is_saved_by_a_suggestion(self):
        """Подсказка не трогает настройки: сохранение — отдельное действие владельца."""
        import config
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch("config.DATA_DIR", type(DB_PATH)(tmp.name)), \
             patch("config.SETTINGS_FILE", type(DB_PATH)(os.path.join(tmp.name, "settings.json"))), \
             patch.object(ai_prices, "fetch_page", return_value=PAGE):
            asyncio.run(ai_prices.suggest(["gemini-2.5-flash"]))
            self.assertEqual(config.load_settings().get("ai_model_prices"), {})


class PricesApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_is_admin_only(self):
        import auth
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for p in [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                  patch("config.DATA_DIR", type(DB_PATH)(tmp.name)),
                  patch("config.SETTINGS_FILE", type(DB_PATH)(os.path.join(tmp.name, "settings.json")))]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        for uid in (9111, 9112):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (9111, 9112)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get("/api/admin/ai/prices?models=gemini-2.5-flash")).status, 401)
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9111]})
            self.assertEqual((await client.get("/api/admin/ai/prices?models=gemini-2.5-flash")).status, 403)
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9112}), \
                 patch.object(ai_prices, "fetch_page", return_value=PAGE):
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9112]})
                res = await client.get("/api/admin/ai/prices?models=gemini-2.5-flash")
                self.assertEqual(res.status, 200)
                body = await res.json()
                self.assertEqual(body["found"]["gemini-2.5-flash"]["input"], 0.30)
                empty = await client.get("/api/admin/ai/prices")
                self.assertEqual(empty.status, 400)


if __name__ == "__main__":
    unittest.main()
