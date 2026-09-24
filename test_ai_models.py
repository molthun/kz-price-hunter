"""Список моделей приходит от провайдера, цены задаёт владелец — и попадают в расчёт расхода."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import ai_service
import database
from config import DB_PATH


class _Resp:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def json(self):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, data, status=200):
        self.data, self.status = data, status
        self.urls, self.headers = [], []

    def get(self, url, **kwargs):
        self.urls.append(url)
        self.headers.append(kwargs.get("headers") or {})
        return _Resp(self.status, self.data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _with_session(session):
    return patch.object(ai_service.aiohttp, "ClientSession", lambda **kwargs: session)


class ListModelsTest(unittest.TestCase):
    def test_gemini_returns_only_models_that_can_generate(self):
        session = _Session({"models": [
            {"name": "models/gemini-3-flash", "displayName": "Gemini 3 Flash",
             "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "displayName": "Embeddings",
             "supportedGenerationMethods": ["embedContent"]}]})
        with _with_session(session):
            result = asyncio.run(ai_service.list_gemini_models("ключ"))
        self.assertEqual([m["name"] for m in result["models"]], ["gemini-3-flash"])
        self.assertIsNone(result["error"])

    def test_openai_returns_the_ids_of_the_key(self):
        session = _Session({"data": [{"id": "gpt-5"}, {"id": "gpt-4o-mini"}]})
        with _with_session(session):
            result = asyncio.run(ai_service.list_openai_models("ключ"))
        self.assertEqual([m["name"] for m in result["models"]], ["gpt-4o-mini", "gpt-5"])
        self.assertTrue(session.headers[0]["Authorization"].startswith("Bearer "))

    def test_refusal_is_named_not_swallowed(self):
        with _with_session(_Session({}, status=403)):
            result = asyncio.run(ai_service.list_gemini_models("ключ"))
        self.assertEqual(result["models"], [])
        self.assertIn("403", result["error"])

    def test_missing_key_is_said_plainly_without_calling_anyone(self):
        session = _Session({})
        with _with_session(session):
            self.assertIn("не задан", asyncio.run(ai_service.list_openai_models(""))["error"])
        self.assertEqual(session.urls, [], "без ключа к провайдеру не ходим")

    def test_network_failure_does_not_raise(self):
        class Boom:
            def __init__(self, **kwargs):
                raise OSError("сеть недоступна")
        with patch.object(ai_service.aiohttp, "ClientSession", Boom):
            result = asyncio.run(ai_service.list_gemini_models("ключ"))
        self.assertEqual(result["models"], [])
        self.assertIn("OSError", result["error"])

    def test_custom_base_is_used_for_compatible_services(self):
        session = _Session({"data": []})
        with _with_session(session):
            asyncio.run(ai_service.list_openai_models("ключ", "https://proxy.example/v1"))
        self.assertTrue(session.urls[0].startswith("https://proxy.example/v1/models"))


class PricesTest(unittest.TestCase):
    """Цены провайдеры не отдают: их вводит владелец, и они должны доходить до расчёта."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch.object(database, "DB_PATH", data_dir / "prices.db"),
                  patch("config.DATA_DIR", data_dir),
                  patch("config.SETTINGS_FILE", data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()

    def test_saved_prices_reach_the_cost_of_a_call(self):
        import ai_router
        import config
        config.save_settings({"ai_model_prices": {"gemini-3-flash": {"input": 0.3, "output": 2.5}}})
        cost = ai_router.estimate_cost("gemini-3-flash", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost, 2.8)

    def test_unknown_model_has_unknown_cost(self):
        import ai_router
        self.assertIsNone(ai_router.estimate_cost("модель-без-цены", 1000, 1000))

    def test_wrong_price_is_refused_with_words(self):
        import config
        for bad in ({"gemini": {"input": "дорого"}}, {"gemini": {"input": -1}},
                    {"gemini": "0.3"}, {"плохое имя!": {"input": 1}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    config._validate_settings({"ai_model_prices": bad})


class ModelsApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_endpoint_is_admin_only_and_hides_the_key(self):
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
        for uid in (9901, 9902):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (9901, 9902)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        server._AI_MODELS_CACHE.update({"at": 0.0, "data": None})
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get("/api/admin/ai/models")).status, 401)
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9901]})
            self.assertEqual((await client.get("/api/admin/ai/models")).status, 403)
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9902}), \
                 patch.object(ai_service, "list_gemini_models",
                              return_value={"models": [{"name": "gemini-3-flash", "title": "G3"}],
                                            "error": None}) as gem, \
                 patch.object(ai_service, "list_openai_models",
                              return_value={"models": [], "error": "ключ OpenAI не задан"}):
                async def fake_gemini(*a, **kw):
                    return {"models": [{"name": "gemini-3-flash", "title": "G3"}], "error": None}

                async def fake_openai(*a, **kw):
                    return {"models": [], "error": "ключ OpenAI не задан"}

                gem.side_effect = None
                with patch.object(ai_service, "list_gemini_models", fake_gemini), \
                     patch.object(ai_service, "list_openai_models", fake_openai):
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9902]})
                    res = await client.get("/api/admin/ai/models")
                    self.assertEqual(res.status, 200)
                    body = await res.json()
                    self.assertEqual(body["gemini"]["models"][0]["name"], "gemini-3-flash")
                    self.assertIn("не задан", body["openai"]["error"])
                    self.assertNotIn("api_key", str(body), "ключи наружу не отдаются")


if __name__ == "__main__":
    unittest.main()


class PriceSurvivesReadTest(unittest.TestCase):
    """Цены должны переживать чтение настроек: прежде их вырезало слияние, и расчёт всегда был пуст."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch("config.DATA_DIR", data_dir),
                  patch("config.SETTINGS_FILE", data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)

    def test_saved_prices_are_read_back(self):
        import config
        config.save_settings({"ai_model_prices": {"gemini-3-flash": {"input": 0.3, "output": 2.5},
                                                  "gpt-5": {"input": 1.25, "output": 10.0}}})
        prices = config.load_settings()["ai_model_prices"]
        self.assertEqual(prices["gemini-3-flash"], {"input": 0.3, "output": 2.5})
        self.assertEqual(prices["gpt-5"]["output"], 10.0)

    def test_cost_is_computed_from_them(self):
        import ai_router
        import config
        config.save_settings({"ai_model_prices": {"gpt-5": {"input": 1.25, "output": 10.0}}})
        self.assertAlmostEqual(ai_router.estimate_cost("gpt-5", 200_000, 50_000), 0.75)

    def test_other_dictionary_settings_keep_their_strict_rule(self):
        """У переключателей магазинов ключи задаёт код — там прежнее правило должно остаться."""
        import config
        config.save_settings({"enabled_shops": {"kaspi": False, "несуществующий": True}})
        shops = config.load_settings()["enabled_shops"]
        self.assertIs(shops["kaspi"], False)
        self.assertNotIn("несуществующий", shops)

    def test_prices_survive_an_unrelated_save(self):
        import config
        config.save_settings({"ai_model_prices": {"gpt-5": {"input": 1.25, "output": 10.0}}})
        config.save_settings({"candidate_drop_pct": 33})
        self.assertIn("gpt-5", config.load_settings()["ai_model_prices"])
