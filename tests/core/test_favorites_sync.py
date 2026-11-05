from tests import test_support  # noqa: F401
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import auth
import database
import web.server as srv


class TestFavoritesSync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name) / "prices.db")
        self.db_patch.start()
        database.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_save_and_list_user_favorites(self):
        uid = 55501
        database.upsert_telegram_user({"id": uid, "username": "tester1"})

        # Добавляем товар в избранное
        item1 = {
            "id": "prod_1",
            "canonical_product_id": "prod_1",
            "title": "Пылесос Artel VCC 0220",
            "shop": "Kaspi",
            "city": "Алматы",
            "new_price": 45000,
            "saved_price": 45000,
            "old_price": 50000,
            "discount_pct": 10.0,
            "url": "https://kaspi.kz/p1",
            "image_url": "https://example.com/p1.jpg"
        }
        saved = database.save_user_favorite(uid, item1)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["item_id"], "prod_1")

        favs = database.list_user_favorites(uid)
        self.assertEqual(len(favs), 1)
        self.assertEqual(favs[0]["title"], "Пылесос Artel VCC 0220")
        self.assertFalse(favs[0]["has_price_drop"])
        self.assertEqual(favs[0]["current_price"], 45000)

    def test_price_drop_calculation_when_catalog_price_lowers(self):
        uid = 55502
        database.upsert_telegram_user({"id": uid, "username": "tester2"})

        # Сохраняем товар с ценой 50 000
        item = {
            "id": "prod_drop_1",
            "canonical_product_id": "prod_canonical_1",
            "title": "Наушники Sony WH-1000XM5",
            "shop": "Kaspi",
            "city": "Алматы",
            "new_price": 50000,
            "saved_price": 50000,
        }
        database.save_user_favorite(uid, item)

        # Добавляем в каталог предложение со сниженной ценой 40 000
        with database.get_connection() as conn:
            conn.execute("""
                INSERT INTO products (id, title, current_price, first_seen_price, min_price, max_price, url)
                VALUES ('prod_drop_1', 'Наушники Sony WH-1000XM5', 40000, 50000, 40000, 50000, 'http://example.com/item')
            """)
            conn.commit()

        favs = database.list_user_favorites(uid)
        self.assertEqual(len(favs), 1)
        f = favs[0]
        self.assertTrue(f["has_price_drop"])
        self.assertEqual(f["current_price"], 40000)
        self.assertEqual(f["saved_price"], 50000)
        self.assertEqual(f["price_drop_amount"], 10000)
        self.assertEqual(f["price_drop_pct"], 20.0)

    def test_remove_and_clear_favorites(self):
        uid = 55503
        database.upsert_telegram_user({"id": uid, "username": "tester3"})

        database.save_user_favorite(uid, {"id": "item_a", "title": "A", "saved_price": 100})
        database.save_user_favorite(uid, {"id": "item_b", "title": "B", "saved_price": 200})
        self.assertEqual(len(database.list_user_favorites(uid)), 2)

        # Удаление одного
        removed = database.remove_user_favorite(uid, "item_a")
        self.assertTrue(removed)
        favs = database.list_user_favorites(uid)
        self.assertEqual(len(favs), 1)
        self.assertEqual(favs[0]["item_id"], "item_b")

        # Очистка всех
        cleared = database.clear_user_favorites(uid)
        self.assertEqual(cleared, 1)
        self.assertEqual(len(database.list_user_favorites(uid)), 0)

    def test_sync_user_favorites_batch(self):
        uid = 55504
        database.upsert_telegram_user({"id": uid, "username": "tester4"})

        # Изначально на сервере один товар
        database.save_user_favorite(uid, {"id": "server_item", "title": "Серверный товар", "saved_price": 1000})

        # Клиент присылает локальную пачку из 2 товаров (один новый, один дубликат)
        batch = [
            {"id": "server_item", "title": "Серверный товар (локально)", "saved_price": 1000},
            {"id": "client_item", "title": "Локальный товар", "saved_price": 2500}
        ]
        result = database.sync_user_favorites_batch(uid, batch)
        self.assertEqual(len(result), 2)
        ids = {r["item_id"] for r in result}
        self.assertEqual(ids, {"server_item", "client_item"})

    def test_export_and_delete_account_includes_favorites(self):
        uid = 55505
        database.upsert_telegram_user({"id": uid, "username": "tester5"})
        database.save_user_favorite(uid, {"id": "fav_x", "title": "X", "saved_price": 300})

        # Экспорт данных содержит favorites
        export_data = database.export_user_data(uid)
        self.assertIsNotNone(export_data)
        self.assertIn("favorites", export_data)
        self.assertEqual(len(export_data["favorites"]), 1)
        self.assertEqual(export_data["favorites"][0]["item_id"], "fav_x")

        # Удаление аккаунта очищает favorites
        counts = database.delete_user_account(uid)
        self.assertEqual(counts.get("favorites"), 1)
        self.assertEqual(len(database.list_user_favorites(uid)), 0)


class TestFavoritesApi(AioHTTPTestCase):
    async def get_application(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name) / "prices.db")
        self.maint_patch = patch.object(auth, "MAINTENANCE_DEV_MODE", False)
        self.db_patch.start()
        self.maint_patch.start()
        database.init_db()

        app = web.Application(middlewares=[srv.auth_middleware])
        app.add_routes([
            web.get("/api/me/favorites", srv.my_favorites_handler),
            web.post("/api/me/favorites", srv.save_favorite_handler),
            web.delete("/api/me/favorites/{item_id}", srv.delete_favorite_item_handler),
            web.delete("/api/me/favorites", srv.clear_favorites_handler),
        ])
        return app

    async def setUpAsync(self):
        await super().setUpAsync()
        self.origin = {"Origin": str(self.client.make_url("/")).rstrip("/")}

    async def tearDownAsync(self):
        self.maint_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_favorites_api_requires_auth(self):
        # Без куки сессии эндпоинты должны возвращать 401
        res = await self.client.get("/api/me/favorites")
        self.assertEqual(res.status, 401)

        res = await self.client.post("/api/me/favorites", json={"id": "1", "title": "Test"}, headers=self.origin)
        self.assertEqual(res.status, 401)

    async def test_favorites_api_flow(self):
        uid = 77701
        database.upsert_telegram_user({"id": uid, "username": "auth_user"})
        token = database.create_session(uid)
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})

        # 1. GET пустой список
        res = await self.client.get("/api/me/favorites")
        self.assertEqual(res.status, 200)
        data = await res.json()
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["favorites"], [])

        # 2. POST одиночный товар
        post_res = await self.client.post("/api/me/favorites", json={
            "id": "item_101",
            "title": "Умные часы Samsung",
            "shop": "Mechta",
            "saved_price": 99000,
            "new_price": 99000
        }, headers=self.origin)
        self.assertEqual(post_res.status, 200)

        # 3. GET проверка
        res2 = await self.client.get("/api/me/favorites")
        data2 = await res2.json()
        self.assertEqual(data2["count"], 1)
        self.assertEqual(data2["favorites"][0]["item_id"], "item_101")

        # 4. POST пакетное слияние
        batch_res = await self.client.post("/api/me/favorites", json={
            "items": [
                {"id": "item_102", "title": "Наушники", "saved_price": 15000},
                {"id": "item_103", "title": "Чехол", "saved_price": 3000}
            ]
        }, headers=self.origin)
        self.assertEqual(batch_res.status, 200)
        batch_data = await batch_res.json()
        self.assertEqual(batch_data["count"], 3)

        # 5. DELETE одиночный товар
        del_one = await self.client.delete("/api/me/favorites/item_102", headers=self.origin)
        self.assertEqual(del_one.status, 200)
        res3 = await self.client.get("/api/me/favorites")
        self.assertEqual((await res3.json())["count"], 2)

        # 6. DELETE всё избранное
        del_all = await self.client.delete("/api/me/favorites", headers=self.origin)
        self.assertEqual(del_all.status, 200)
        res4 = await self.client.get("/api/me/favorites")
        self.assertEqual((await res4.json())["count"], 0)
