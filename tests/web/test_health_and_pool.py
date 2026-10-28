from tests import test_support  # noqa: F401
import unittest
from aiohttp.test_utils import AioHTTPTestCase
from web import server


class HealthAndPoolTest(AioHTTPTestCase):
    async def get_application(self):
        app = server.create_app()
        app.cleanup_ctx.clear()
        return app

    async def test_live_probe_returns_200_instantly(self):
        resp = await self.client.get("/api/live")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(data.get("status"), "live")

    async def test_health_probe_returns_200_with_db_status(self):
        resp = await self.client.get("/api/health")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("status", data)
        self.assertIn(data["status"], ("ok", "degraded"))


if __name__ == "__main__":
    unittest.main()
