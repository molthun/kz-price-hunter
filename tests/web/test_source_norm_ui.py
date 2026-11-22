from tests import test_support  # noqa: F401
import asyncio
import datetime
import pathlib
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestServer

import auth
import data_quality as dq
import database
import web.server as server
from tests.test_support import BrowserTestClient


class SourceNormRecommendationsUiTest(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def _seed_degraded_streak(self, shop_key: str, source_url: str, category: str, valid: int = 35):
        now = datetime.datetime.now(datetime.timezone.utc)
        for i in range(dq.STABLE_SHIFT_WINDOW):
            ts = (now - datetime.timedelta(minutes=5 * (dq.STABLE_SHIFT_WINDOW - i))).isoformat()
            database.record_source_scan(
                shop_key,
                source_url,
                category,
                f"scan_{i}",
                ts,
                "complete",
                {
                    "quality": dq.DEGRADED,
                    "reasons": [f"товаров {valid} при норме 100 (35%)"],
                    "warnings": [],
                    "learn": False,
                    "baseline": 100.0,
                    "basis": "active_offers",
                },
                {"received": valid, "valid": valid, "rejected": 0, "duplicates": 0, "with_image": valid},
            )

    def test_recommendations_api_auth_list_and_confirm_clears_item(self):
        shop_key = "forte_ui_test"
        url = "https://market.forte.kz/catalog/smartphones-ui-test"
        self._seed_degraded_streak(shop_key, url, "Forte: Смартфоны UI", valid=35)

        uid = 8444
        database.upsert_telegram_user({"id": uid, "first_name": "NormAdmin"})
        token = database.create_session(uid)

        async def _scenario():
            app = server.create_app()
            app.cleanup_ctx.clear()
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {uid}), patch.object(auth, "PUBLIC_ORIGIN", ""):
                async with BrowserTestClient(TestServer(app)) as client:
                    # 1. Гость не имеет доступа
                    r_guest = await client.get("/api/admin/source-norm/recommendations")
                    self.assertIn(r_guest.status, (401, 403))

                    # 2. Администратор видит рекомендацию
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})
                    r_list = await client.get("/api/admin/source-norm/recommendations")
                    self.assertEqual(r_list.status, 200)
                    data = await r_list.json()
                    self.assertTrue(data["ok"])
                    matched = [it for it in data["items"] if it["shop_key"] == shop_key and it["source_url"] == url]
                    self.assertEqual(len(matched), 1)
                    self.assertEqual(matched[0]["recommended_valid"], 35.0)
                    self.assertEqual(matched[0]["samples"], dq.STABLE_SHIFT_WINDOW)

                    # 3. Подтверждение нормы убирает источник из списка ожидающих
                    r_confirm = await client.post(
                        "/api/admin/source-norm/confirm",
                        json={"shop_key": shop_key, "source_url": url},
                    )
                    self.assertEqual(r_confirm.status, 200)

                    r_after = await client.get("/api/admin/source-norm/recommendations")
                    after_data = await r_after.json()
                    self.assertFalse(
                        any(it["shop_key"] == shop_key and it["source_url"] == url for it in after_data["items"])
                    )

        asyncio.run(_scenario())

    def test_admin_template_contains_source_norm_controls(self):
        html = pathlib.Path("web/templates/admin.html").read_text(encoding="utf-8")
        for token in (
            'id="sourceNormSection"',
            'id="sourceNormState"',
            'id="sourceNormList"',
            '/api/admin/source-norm/recommendations',
            '/api/admin/source-norm/confirm',
            'Подтвердить новую норму',
        ):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
