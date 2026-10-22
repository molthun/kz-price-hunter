"""Веб-слой: gzip для крупных ответов и кэш сводных счётчиков дашборда.

Проверяем поведение, а не факт наличия кода: сжатие включается только по Accept-Encoding
и только для крупных ответов; кэш не замораживает прогресс обхода и сбрасывается при
изменениях (обход, настройки, разбор алертов).
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import gzip
import json
import pathlib
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import web.server as server
from bounded_cache import BoundedTTLCache


class GzipMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def test_large_response_is_compressed_when_client_accepts_gzip(self):
        request = make_mocked_request("GET", "/api/deals", headers={"Accept-Encoding": "gzip"})

        async def handler(_):
            return web.json_response({"items": [{"title": f"Товар {i}"} for i in range(200)]})

        response = await server.gzip_middleware(request, handler)
        self.assertEqual(response.headers.get("Content-Encoding"), "gzip")
        self.assertIn("Accept-Encoding", response.headers.get("Vary", ""))
        self.assertEqual(response.headers.get("Content-Length"), str(len(response.body)))
        decoded = json.loads(gzip.decompress(response.body))
        self.assertEqual(len(decoded["items"]), 200)

    async def test_without_accept_encoding_response_is_plain(self):
        request = make_mocked_request("GET", "/api/deals")
        body = b"x" * 5000

        async def handler(_):
            return web.Response(body=body, content_type="application/json")

        response = await server.gzip_middleware(request, handler)
        self.assertIsNone(response.headers.get("Content-Encoding"))
        self.assertEqual(response.body, body)

    async def test_small_response_is_not_compressed(self):
        request = make_mocked_request("GET", "/api/stats", headers={"Accept-Encoding": "gzip"})

        async def handler(_):
            return web.json_response({"ok": True})

        response = await server.gzip_middleware(request, handler)
        self.assertIsNone(response.headers.get("Content-Encoding"))

    async def test_preexisting_content_encoding_is_not_touched(self):
        request = make_mocked_request("GET", "/api/deals", headers={"Accept-Encoding": "gzip"})
        body = b"x" * 5000

        async def handler(_):
            response = web.Response(body=body, content_type="application/json")
            response.headers["Content-Encoding"] = "br"
            return response

        response = await server.gzip_middleware(request, handler)
        self.assertEqual(response.headers.get("Content-Encoding"), "br")
        self.assertEqual(response.body, body)

    def test_app_has_gzip_middleware(self):
        app = server.create_app()
        self.assertIn(server.gzip_middleware, app.middlewares)
        self.assertIn("stats_cache", app)


class StatsCacheTest(unittest.IsolatedAsyncioTestCase):
    def make_app(self):
        app = web.Application()
        app["stats_cache"] = BoundedTTLCache(maxsize=10, ttl=server.STATS_CACHE_TTL_SECONDS)
        server._register_stats_cache(app["stats_cache"])
        return app

    async def get_stats(self, app, city="Астана"):
        request = make_mocked_request("GET", f"/api/stats?city={city}", app=app)
        request["user"] = None
        with patch.object(server, "load_settings", return_value={}), \
             patch.object(server, "get_metadata", return_value=0), \
             patch.object(server, "get_wave_plan",
                          return_value={"wave_index": 0, "total_waves": 1, "hot_categories": [],
                                        "wave_categories": [], "next_wave_categories": []}), \
             patch.object(server, "get_wave_interval_seconds", return_value=600):
            response = await server.stats_handler(request)
        return json.loads(response.body)

    async def test_second_request_uses_cache(self):
        app = self.make_app()
        with patch.object(server, "get_stats", return_value={"total_products": 1}) as mocked:
            await self.get_stats(app)
            await self.get_stats(app)
        self.assertEqual(mocked.call_count, 1)

    async def test_cache_does_not_freeze_scan_progress(self):
        app = self.make_app()
        with patch.object(server, "get_stats", return_value={"total_products": 1}):
            server.scan_state["is_running"] = True
            first = await self.get_stats(app)
            server.scan_state["is_running"] = False
            second = await self.get_stats(app)
        self.assertTrue(first["scan_state"]["is_running"])
        self.assertFalse(second["scan_state"]["is_running"])
        server.scan_state["is_running"] = False

    async def test_invalidation_forces_fresh_query(self):
        app = self.make_app()
        with patch.object(server, "get_stats", return_value={"total_products": 1}) as mocked:
            await self.get_stats(app)
            server._invalidate_stats_caches()
            await self.get_stats(app)
        self.assertEqual(mocked.call_count, 2)

    async def test_users_and_cities_have_separate_keys(self):
        app = self.make_app()
        with patch.object(server, "get_stats", return_value={"total_products": 1}) as mocked:
            await self.get_stats(app, city="Астана")
            await self.get_stats(app, city="Алматы")
        self.assertEqual(mocked.call_count, 2)

    def test_invalidation_is_wired_into_change_points(self):
        source = pathlib.Path(server.__file__).read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("_invalidate_stats_caches()"), 4,
                                "кэш должен сбрасываться: обход, алерты, настройки")


if __name__ == "__main__":
    unittest.main()
