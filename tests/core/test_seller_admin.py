from tests import test_support  # must be first
import sqlite3
import unittest
from unittest.mock import patch

from domain.models import ChannelType
from domain.seller_identity import IdentityType
from domain.seller_admin import SellerFormError, save_seller, seller_details
from repositories.channel_repo import ChannelRepository
from repositories.schema_v2 import init_schema_v2
from repositories.seller_identity_repo import SellerIdentityRepository
from repositories.seller_repo import SellerRepository


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

    def test_telegram_bot_is_not_saved(self):
        seller = self._create(telegram="@TechnodomBonus_bot", phone="1717")
        self.assertEqual(seller["telegram"], "")
        self.assertEqual(seller["phone"], "1717")
        channel = save_seller(self.conn, seller["id"], {**seller, "telegram": "https://t.me/shopkz"})
        self.assertEqual(channel["telegram"], "shopkz")
        removed = save_seller(self.conn, seller["id"], {**channel, "telegram": "shop_bot"})
        self.assertEqual(removed["telegram"], "")

    def test_bad_values_are_rejected(self):
        for body in ({"name": "x", "phone": "+77001112233"}, {"name": "Магазин"},
                     {"name": "Магазин", "website_url": "javascript:alert(1)"},
                     {"name": "Магазин", "marketplace_links": ["javascript:alert(1)"]},
                     {"name": "Магазин", "phone": "+77001112233", "bin": "12"}):
            with self.subTest(body=body):
                with self.assertRaises(SellerFormError):
                    save_seller(self.conn, None, body)

    def test_kaspi_links_lead_to_one_working_page(self):
        seller = self._create(marketplace_links=[
            "https://kaspi.kz/shop/info/merchant/18430177/",
            "https://kaspi.kz/shop/m/18430177/products/?ref=x",
        ])
        self.assertEqual(seller["marketplace_links"], ["https://kaspi.kz/shop/m/18430177/products/"])
        channels = ChannelRepository(self.conn).list_by_seller(seller["id"], active_only=True)
        self.assertEqual([c.external_store_id for c in channels if c.channel_type == ChannelType.MARKETPLACE],
                         ["kaspi:18430177"])

    def test_cleared_fields_are_erased_with_their_identities(self):
        seller = self._create(phone="+77001112233", legal_name="ТОО Тест")
        identities = SellerIdentityRepository(self.conn)
        identities.add_identity_values(seller["id"], IdentityType.PHONE, "+77009998877", source="discovery")
        cleared = save_seller(self.conn, seller["id"], {**seller, "phone": "", "bin": "", "legal_name": "", "website_url": ""})
        self.assertEqual((cleared["phone"], cleared["bin"], cleared["legal_name"], cleared["website_url"]), ("", "", "", ""))
        left = {(i.identity_type, i.identity_value) for i in identities.get_identities_by_seller(seller["id"])}
        self.assertEqual(left, {(IdentityType.PHONE, "+77009998877")})  # чужой признак редактор не трогает
        self.assertIsNone(SellerRepository(self.conn).get_by_id(seller["id"]).domain)

    def test_changed_bin_replaces_old_identity(self):
        seller = self._create()
        save_seller(self.conn, seller["id"], {**seller, "bin": "999999999999"})
        identities = SellerIdentityRepository(self.conn)
        self.assertEqual(identities.find_sellers_by_identity(IdentityType.BIN, "123456789012"), [])
        self.assertEqual(identities.find_sellers_by_identity(IdentityType.BIN, "999999999999"), [seller["id"]])

    def test_unknown_seller(self):
        self.assertIsNone(save_seller(self.conn, "seller_none", {"name": "Магазин"}))
        self.assertIsNone(seller_details(self.conn, "seller_none"))

    def test_cyrillic_name_gets_slug_and_disable_works(self):
        seller = self._create(name="Магазин", website_url="", marketplace_links=["https://kaspi.kz/shop/x"])
        self.assertTrue(seller["slug"].startswith("shop-"))
        off = save_seller(self.conn, seller["id"], {**seller, "is_active": False})
        self.assertFalse(off["is_active"])


    def test_social_sources_registered_and_synced(self):
        from repositories.social_source_repo import SocialSourceRepository
        social_repo = SocialSourceRepository(self.conn)
        seller = self._create(instagram="@kz_tech", telegram="https://t.me/kztech")
        sources = social_repo.list_by_seller_id(seller["id"])
        self.assertEqual(len(sources), 2)
        self.assertTrue(all(s.enabled for s in sources))
        self.assertEqual({s.platform for s in sources}, {"instagram", "telegram"})

        # Отключение магазина отключает и сбор соцсетей
        off = save_seller(self.conn, seller["id"], {**seller, "is_active": False})
        sources_off = social_repo.list_by_seller_id(seller["id"])
        self.assertTrue(all(not s.enabled for s in sources_off))

        # Повторное включение магазина включает соцсети обратно
        on = save_seller(self.conn, seller["id"], {**off, "is_active": True})
        sources_on = social_repo.list_by_seller_id(seller["id"])
        self.assertTrue(all(s.enabled for s in sources_on))

    def test_seller_details_includes_channels_with_status_labels(self):
        seller = self._create(
            website_url="https://shop.kz/feed.xml",
            instagram="@shop_almaty",
            marketplace_links=["https://kaspi.kz/shop/m/123/products/"]
        )
        details = seller_details(self.conn, seller["id"])
        self.assertIn("channels", details)
        ch_map = {c["channel_type"]: c for c in details["channels"]}
        self.assertIn("website", ch_map)
        self.assertEqual(ch_map["website"]["status_label"], "Нужен сканер фидов")
        self.assertIn("marketplace", ch_map)
        self.assertEqual(ch_map["marketplace"]["status_label"], "Готов к сканированию")
        self.assertIn("instagram", ch_map)
        self.assertEqual(ch_map["instagram"]["status_label"], "В сборе")


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
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {8202}), patch.object(auth, "PUBLIC_ORIGIN", ""):
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

    async def test_v2_admin_sellers_endpoints_and_channels_crud(self):
        import auth
        import database
        import web.server as server
        from aiohttp.test_utils import TestServer
        from tests.test_support import BrowserTestClient as TestClient
        database.init_db()
        uid = 8203
        database.upsert_telegram_user({"id": uid, "first_name": "Admin"})
        token = database.create_session(uid)
        app = server.create_app()
        app.cleanup_ctx.clear()
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {uid}), patch.object(auth, "PUBLIC_ORIGIN", ""):
            async with TestClient(TestServer(app)) as client:
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})

                # 1. Создание через /api/v2/admin/sellers
                payload = {
                    "name": "Тест Стор Алматы",
                    "city": "Алматы",
                    "phone": "+7 777 123 4567",
                    "bin": "123456789012",
                    "website_url": "https://test-store.kz",
                    "marketplace_links": ["https://kaspi.kz/shop/m/111/products/", "https://forte.kz/merchant/222"],
                    "instagram": "@teststore_kz"
                }
                res = await client.post("/api/v2/admin/sellers", json=payload)
                self.assertEqual(res.status, 201)
                created = (await res.json())["seller"]
                seller_id = created["id"]
                self.assertEqual(created["name"], "Тест Стор Алматы")
                self.assertEqual(created["city"], "Алматы")
                self.assertEqual(len(created["channels"]), 4)  # website, 2 marketplaces, instagram

                # 2. Фильтрация списка через /api/v2/admin/sellers
                list_res = await client.get("/api/v2/admin/sellers", params={"city": "Алматы", "is_active": "true"})
                self.assertEqual(list_res.status, 200)
                list_data = await list_res.json()
                self.assertTrue(list_data["ok"])
                self.assertTrue(any(s["id"] == seller_id for s in list_data["sellers"]))

                # Фильтр по другому городу не должен находить
                list_astana = await client.get("/api/v2/admin/sellers", params={"city": "Астана"})
                astana_data = await list_astana.json()
                self.assertFalse(any(s["id"] == seller_id for s in astana_data["sellers"]))

                # 3. Детали магазина через /api/v2/admin/sellers/{id}
                detail_res = await client.get(f"/api/v2/admin/sellers/{seller_id}")
                self.assertEqual(detail_res.status, 200)
                details = (await detail_res.json())["seller"]
                self.assertEqual(details["id"], seller_id)
                self.assertEqual(details["instagram"], "teststore_kz")

                # 4. PATCH обновление магазина
                patch_res = await client.patch(f"/api/v2/admin/sellers/{seller_id}", json={
                    **details,
                    "legal_name": "ТОО Тест Стор Казахстан"
                })
                self.assertEqual(patch_res.status, 200)
                patched = (await patch_res.json())["seller"]
                self.assertEqual(patched["legal_name"], "ТОО Тест Стор Казахстан")

                # 5. Добавление канала вручную через POST /api/v2/admin/sellers/{id}/channels
                ch_res = await client.post(f"/api/v2/admin/sellers/{seller_id}/channels", json={
                    "channel_type": "telegram",
                    "name": "Telegram канал магазина",
                    "base_url": "https://t.me/teststore_official",
                    "external_store_id": "teststore_official"
                })
                self.assertEqual(ch_res.status, 201)
                new_ch = (await ch_res.json())["channel"]
                channel_id = new_ch["id"]
                self.assertEqual(new_ch["channel_type"], "telegram")

                # 6. Обновление канала через PATCH /api/v2/admin/sellers/{id}/channels/{cid}
                ch_patch = await client.patch(f"/api/v2/admin/sellers/{seller_id}/channels/{channel_id}", json={
                    "name": "Telegram Витрина"
                })
                self.assertEqual(ch_patch.status, 200)
                self.assertEqual((await ch_patch.json())["channel"]["name"], "Telegram Витрина")

                # 7. Запрос fetch_now для канала
                fetch_res = await client.post(f"/api/v2/admin/sellers/{seller_id}/channels/{channel_id}/fetch")
                # Для этого канала еще нет записи в social_sources (добавили через сырой endpoint каналов)
                self.assertIn(fetch_res.status, (200, 400))

                # 8. Отключение канала через DELETE /api/v2/admin/sellers/{id}/channels/{cid}
                ch_del = await client.delete(f"/api/v2/admin/sellers/{seller_id}/channels/{channel_id}")
                self.assertEqual(ch_del.status, 200)
                self.assertTrue((await ch_del.json())["deleted"])


if __name__ == "__main__":
    unittest.main()
