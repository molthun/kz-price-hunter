import unittest
from unittest.mock import patch

from tests import test_support  # noqa: F401

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import auth
import config
from web import server


class ParseCanonicalRedirectHostsTest(unittest.TestCase):
    def test_parses_hosts_urls_ports_and_deduplicates(self):
        parsed = config.parse_canonical_redirect_hosts(
            " SHDEV.molthun.ru, https://old.example.kz:443/path , shdev.molthun.ru:8080 , , "
        )
        self.assertEqual(parsed, ("shdev.molthun.ru", "old.example.kz"))

    def test_empty_returns_empty_tuple(self):
        self.assertEqual(config.parse_canonical_redirect_hosts(""), ())
        self.assertEqual(config.parse_canonical_redirect_hosts(" , "), ())


class CanonicalHostRedirectMiddlewareTest(AioHTTPTestCase):
    async def get_application(self):
        async def _ok(request):
            return web.json_response({"ok": True, "path": request.path})

        app = web.Application(
            middlewares=[server.telemetry_middleware, server.canonical_host_redirect_middleware]
        )
        app.router.add_get("/", _ok)
        app.router.add_get("/deals", _ok)
        app.router.add_get("/api/version", _ok)
        app.router.add_get("/api/health", _ok)
        app.router.add_post("/api/favorites", _ok)
        app.router.add_get("/{tail:.*}", _ok)
        return app

    async def test_redirects_get_with_path_and_query(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.get(
                "/deals?city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B&size=42",
                headers={"Host": "shdev.molthun.ru"},
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 301)
            self.assertEqual(
                resp.headers.get("Location"),
                "https://shop.molthun.ru/deals?city=%D0%90%D0%BB%D0%BC%D0%B0%D1%82%D1%8B&size=42",
            )
            self.assertEqual(resp.headers.get("Vary"), "Host")
            self.assertTrue(resp.headers.get("X-Request-ID"))

    async def test_redirects_head_request(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.head(
                "/api/version",
                headers={"Host": "shdev.molthun.ru"},
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 301)
            self.assertEqual(resp.headers.get("Location"), "https://shop.molthun.ru/api/version")

    async def test_never_loops_on_canonical_host(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shop.molthun.ru", "shdev.molthun.ru")):
            resp = await self.client.get(
                "/deals",
                headers={"Host": "shop.molthun.ru"},
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 200)

    async def test_healthcheck_on_localhost_not_redirected(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.get("/api/health", allow_redirects=False)
            self.assertEqual(resp.status, 200)

    async def test_post_is_not_redirected(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.post(
                "/api/favorites",
                headers={"Host": "shdev.molthun.ru"},
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 200)

    async def test_x_forwarded_host_honored_only_from_trusted_proxy(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            with patch.object(auth, "trusted_proxy", return_value=False):
                resp = await self.client.get(
                    "/",
                    headers={"Host": "shop.molthun.ru", "X-Forwarded-Host": "shdev.molthun.ru"},
                    allow_redirects=False,
                )
                self.assertEqual(resp.status, 200)

            with patch.object(auth, "trusted_proxy", return_value=True):
                resp = await self.client.get(
                    "/",
                    headers={"Host": "127.0.0.1:35539", "X-Forwarded-Host": "shdev.molthun.ru, proxy.local"},
                    allow_redirects=False,
                )
                self.assertEqual(resp.status, 301)
                self.assertEqual(resp.headers.get("Location"), "https://shop.molthun.ru/")

    async def test_prevents_protocol_relative_open_redirect(self):
        class _FakeReq:
            def __init__(self, raw_path):
                self.raw_path = raw_path

        self.assertEqual(
            server._build_canonical_redirect_url(_FakeReq("//evil.example/steal?x=1"), "https://shop.molthun.ru"),
            "https://shop.molthun.ru/evil.example/steal?x=1",
        )
        self.assertEqual(
            server._build_canonical_redirect_url(_FakeReq("/\\evil.example/steal"), "https://shop.molthun.ru"),
            "https://shop.molthun.ru/evil.example/steal",
        )
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.get(
                "///evil.example/steal?x=1",
                headers={"Host": "shdev.molthun.ru"},
                allow_redirects=False,
            )
            self.assertEqual(resp.status, 301)
            self.assertEqual(
                resp.headers.get("Location"),
                "https://shop.molthun.ru/evil.example/steal?x=1",
            )

    async def test_disabled_when_hosts_or_public_origin_empty(self):
        with patch.object(config, "PUBLIC_ORIGIN", "https://shop.molthun.ru"), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ()):
            resp = await self.client.get("/", headers={"Host": "shdev.molthun.ru"}, allow_redirects=False)
            self.assertEqual(resp.status, 200)

        with patch.object(config, "PUBLIC_ORIGIN", ""), \
             patch.object(config, "CANONICAL_REDIRECT_HOSTS", ("shdev.molthun.ru",)):
            resp = await self.client.get("/", headers={"Host": "shdev.molthun.ru"}, allow_redirects=False)
            self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
