from tests import test_support  # must be first
import sqlite3
import unittest
from unittest.mock import patch

from domain.models import ChannelType
from domain.seller_admin import SellerFormError, save_seller, seller_details
from repositories.channel_repo import ChannelRepository
from repositories.schema_v2 import init_schema_v2


class SellerAdminTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.addCleanup(self.conn.close)

    def _create(self, **extra):
        body = {"name": "Тест Магазин", "city": "Астана", "website_url": "shop.kz", "bin": "123456789012",
                "marketplace_links": ["https://kaspi.kz/shop/info/merchant/111", "https://halykmarket.kz/merchant/22",
                                      "https://www.ozon.kz/seller/abc-33"], **extra}
        return save_seller(self.conn, None, body)

    def test_create_keeps_every_marketplace(self):
        seller = self._create()
        self.assertEqual(len(seller["marketplace_links"]), 3)
        self.assertEqual(seller["city"], "Астана")
        self.assertEqual(seller["website_url"], "https://shop.kz")
        self.assertEqual(seller["bin"], "123456789012")

    def test_edit_removes_adds_and_restores_links(self):
        seller = self._create()
        first = seller["marketplace_links"][0]
        body = {**seller, "name": "Новое имя", "marketplace_links": ["https://flip.kz/shop/1"]}
        edited = save_seller(self.conn, seller["id"], body)
        self.assertEqual(edited["name"], "Новое имя")
        self.assertEqual(edited["marketplace_links"], ["https://flip.kz/shop/1"])
        restored = save_seller(self.conn, seller["id"], {**edited, "marketplace_links": [first]})
        self.assertEqual(restored["marketplace_links"], [first])

    def test_edit_keeps_scraper_channels(self):
        seller = self._create()
        ChannelRepository(self.conn).get_or_create(seller["id"], "kaspi", "Сбор Kaspi")
        edited = save_seller(self.conn, seller["id"], {**seller, "marketplace_links": []})
        self.assertIn("Сбор Kaspi", edited["collection_channels"])
        self.assertEqual(edited["marketplace_links"], [])

    def test_social_handles_round_trip(self):
        seller = self._create(instagram="@shop_kz", telegram="https://t.me/shopkz")
        self.assertEqual(seller["instagram"], "shop_kz")
        cleared = save_seller(self.conn, seller["id"], {**seller, "instagram": ""})
        self.assertEqual(cleared["instagram"], "")
        self.assertEqual(cleared["telegram"], seller["telegram"])

    def test_bad_values_are_rejected(self):
        for body in ({"name": "x", "phone": "+77001112233"}, {"name": "Магазин"},
                     {"name": "Магазин", "website_url": "javascript:alert(1)"},
                     {"name": "Магазин", "marketplace_links": ["javascript:alert(1)"]},
                     {"name": "Магазин", "phone": "+77001112233", "bin": "12"}):
            with self.subTest(body=body):
                with self.assertRaises(SellerFormError):
                    save_seller(self.conn, None, body)

    def test_unknown_seller(self):
        self.assertIsNone(save_seller(self.conn, "seller_none", {"name": "Магазин"}))
        self.assertIsNone(seller_details(self.conn, "seller_none"))

    def test_cyrillic_name_gets_slug_and_disable_works(self):
        seller = self._create(name="Магазин", website_url="", marketplace_links=["https://kaspi.kz/shop/x"])
        self.assertTrue(seller["slug"].startswith("shop-"))
        off = save_seller(self.conn, seller["id"], {**seller, "is_active": False})
        self.assertFalse(off["is_active"])


class SellerAdminApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_admin_can_use_editor_and_save_starts_nothing(self):
        import auth
        import database
        import web.server as server
        from aiohttp.test_utils import TestServer
        from tests.test_support import BrowserTestClient as TestClient
        database.init_db()
        for uid in (8201, 8202):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (8201, 8202)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        body = {"name": "Новый Магазин", "marketplace_links": ["https://kaspi.kz/shop/a", "https://flip.kz/shop/b"]}
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {8202}):
            async with TestClient(TestServer(app)) as client:
                self.assertEqual((await client.get("/api/admin/sellers")).status, 401)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[8201]})
                self.assertEqual((await client.get("/api/admin/sellers")).status, 403)
                self.assertEqual((await client.post("/api/admin/sellers", json=body)).status, 403)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[8202]})
                res = await client.post("/api/admin/sellers", json=body)
                self.assertEqual(res.status, 201)
                seller = (await res.json())["seller"]
                self.assertEqual(len(seller["marketplace_links"]), 2)
                res = await client.put(f"/api/admin/sellers/{seller['id']}", json={**seller, "bin": "12"})
                self.assertEqual(res.status, 400)
                self.assertIn("12 цифр", (await res.json())["errors"][0])
                res = await client.put("/api/admin/sellers/seller_none", json=body)
                self.assertEqual(res.status, 404)
                listed = await (await client.get("/api/admin/sellers", params={"search": "Новый"})).json()
                self.assertEqual([item["id"] for item in listed["sellers"]], [seller["id"]])
                self.assertEqual(listed["sellers"][0]["channels_count"], 2)
                self.assertEqual((await client.get(f"/api/admin/sellers/{seller['id']}")).status, 200)


if __name__ == "__main__":
    unittest.main()
