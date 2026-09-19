"""Этап 4 аудита: идентичность предложения по городу, миграции, история цен, сопоставление моделей.
Без сети и без рабочей БД."""
import asyncio
import datetime
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

import database  # noqa: E402
from config import DATA_DIR, DB_PATH  # noqa: E402
from database import get_connection, init_db  # noqa: E402
from offer_identity import assign_offer_ids, location_slug, offer_id  # noqa: E402


def _product(pid, city="Астана", price=100000, title="Смартфон Samsung Galaxy A26 5G 8/256GB"):
    return {"id": pid, "shop": "Kaspi Магазин", "title": title, "url": "https://kaspi.kz/shop/p/x",
            "price": price, "city": city}


class OfferIdentityTest(unittest.TestCase):
    def test_location_slugs(self):
        self.assertEqual(location_slug("Астана"), "astana")
        self.assertEqual(location_slug("Алматы"), "almaty")
        for nationwide in ("Казахстан", "Астана / Казахстан"):
            self.assertEqual(location_slug(nationwide), "kz")
        for unknown in ("Регион не подтверждён", "Все", "", None, "Марс"):
            self.assertEqual(location_slug(unknown), "unknown")

    def test_offer_id_is_idempotent(self):
        p = _product("kaspi_1", "Алматы")
        self.assertEqual(offer_id(p), "kaspi_1@almaty")
        assign_offer_ids([p])
        self.assertEqual(offer_id(p), "kaspi_1@almaty")

    def test_same_product_in_two_cities_is_stored_separately(self):
        init_db()
        from database import get_product_by_id, save_or_update_product
        astana, almaty = assign_offer_ids([_product("kaspi_77", "Астана", 100000), _product("kaspi_77", "Алматы", 95000)])
        save_or_update_product(astana)
        save_or_update_product(almaty)
        save_or_update_product(dict(astana, price=100000))
        self.assertEqual(get_product_by_id("kaspi_77@astana")["current_price"], 100000)
        self.assertEqual(get_product_by_id("kaspi_77@almaty")["current_price"], 95000)
        self.assertEqual(get_product_by_id("kaspi_77@astana")["first_seen_price"], 100000)

    def test_forte_without_city_price_is_nationwide(self):
        from scrapers.fortemarket import ForteMarketScraper
        hit = {"objectID": "1", "Name": "Товар", "Price": 500,
               "Locations": {"Location": [{"ID": "KZ", "Price": 510}, {"ID": "KZ-ALA", "Price": 480}]}}
        data = {"nbHits": 1, "hits": [hit]}
        self.assertEqual(ForteMarketScraper.parse_response(data, "C", 1, city="Алматы")[0]["city"], "Алматы")
        self.assertEqual(ForteMarketScraper.parse_response(data, "C", 1, city="Астана")[0]["city"], "Казахстан")
        no_locations = {"nbHits": 1, "hits": [{"objectID": "2", "Name": "Товар", "Price": 500}]}
        self.assertEqual(ForteMarketScraper.parse_response(no_locations, "C", 1, city="Астана")[0]["city"], "Казахстан")


class LiveSearchCityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        init_db()
        import search_engine
        search_engine._LIVE_CACHE.clear()

    async def test_all_cities_polls_astana_and_labels_it(self):
        import search_engine as se
        from scrapers.fortemarket import ForteMarketScraper
        from scrapers.fourmobile import FourMobileScraper
        kaspi_item = {"id": "kaspi_5", "shop": "Kaspi Магазин", "title": "Samsung Galaxy A26 8/256GB",
                      "url": "https://kaspi.kz/shop/p/y", "price": 150000, "city": "Астана"}
        mobile_item = {"id": "4mobile_9", "shop": "4mobile", "title": "Samsung Galaxy A26 8/256GB",
                       "url": "https://wa.me/77007654321", "price": 149000, "city": "Астана"}
        with patch.object(se, "load_settings", return_value={"enabled_shops": {}}), \
             patch.object(se, "KaspiScraper") as kaspi_cls, \
             patch.object(FourMobileScraper, "search_live", new_callable=AsyncMock, return_value=[mobile_item]), \
             patch.object(ForteMarketScraper, "search_live", new_callable=AsyncMock, return_value=[]), \
             patch("scrapers.http.get") as http_get:
            kaspi_cls.return_value.search = AsyncMock(return_value=[kaspi_item])
            found = await se.search_live_stores("galaxy a26", city="Все")
        kaspi_cls.assert_called_once_with(city_code="710000000")
        http_get.assert_not_called()  # Белый Ветер не опрашивается live
        self.assertEqual(sorted(p["id"] for p in found), ["4mobile_9@astana", "kaspi_5@astana"])
        self.assertTrue(all(p["city"] == "Астана" for p in found))

    async def test_almaty_offer_does_not_overwrite_astana(self):
        import search_engine as se
        from database import get_product_by_id, save_or_update_product
        from scrapers.fortemarket import ForteMarketScraper
        from scrapers.fourmobile import FourMobileScraper
        save_or_update_product(dict(_product("kaspi_8", "Астана", 200000), id="kaspi_8@astana"))
        almaty = {"id": "kaspi_8", "shop": "Kaspi Магазин", "title": "Samsung Galaxy A26 8/256GB",
                  "url": "https://kaspi.kz/shop/p/z", "price": 180000, "city": "Алматы"}
        with patch.object(se, "load_settings", return_value={"enabled_shops": {"fourmobile": False, "fortemarket": False}}), \
             patch.object(se, "KaspiScraper") as kaspi_cls, \
             patch.object(FourMobileScraper, "search_live", new_callable=AsyncMock), \
             patch.object(ForteMarketScraper, "search_live", new_callable=AsyncMock):
            kaspi_cls.return_value.search = AsyncMock(return_value=[almaty])
            await se.search_live_stores("galaxy a26", city="Алматы")
        kaspi_cls.assert_called_once_with(city_code="750000000")
        self.assertEqual(get_product_by_id("kaspi_8@astana")["current_price"], 200000)
        self.assertEqual(get_product_by_id("kaspi_8@almaty")["current_price"], 180000)


class PriceHistoryTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM price_observations WHERE product_id IN ('hist_1@astana', 'prune@astana')")
            conn.execute("DELETE FROM products WHERE id IN ('hist_1@astana', 'prune@astana')")
            conn.commit()

    def tearDown(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM price_observations WHERE product_id IN ('hist_1@astana', 'prune@astana')")
            conn.execute("DELETE FROM products WHERE id IN ('hist_1@astana', 'prune@astana')")
            conn.commit()

    def test_observations_only_on_change(self):
        from database import get_price_observations, save_or_update_product, save_or_update_products_batch
        p = dict(_product("hist_1@astana", price=100000), old_price_on_site=120000)
        save_or_update_product(p)
        save_or_update_product(p)                       # без изменений
        save_or_update_products_batch([dict(p, price=90000)])
        save_or_update_products_batch([dict(p, price=90000, old_price_on_site=0)])  # сняли зачёркнутую
        history = get_price_observations("hist_1@astana")
        self.assertEqual([(h["price"], h["old_price_on_site"]) for h in history],
                         [(90000, 0), (90000, 120000), (100000, 120000)])

    def test_prune_keeps_recent(self):
        from database import prune_price_observations
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=200)).isoformat()
        with get_connection() as conn:
            conn.execute("INSERT INTO price_observations VALUES ('prune@astana', 1, 0, ?)", (old,))
            conn.execute("INSERT INTO price_observations VALUES ('prune@astana', 2, 0, ?)",
                         (datetime.datetime.now(datetime.timezone.utc).isoformat(),))
            conn.commit()
        self.assertGreaterEqual(prune_price_observations(180), 1)
        with get_connection() as conn:
            left = conn.execute("SELECT price FROM price_observations WHERE product_id='prune@astana'").fetchall()
        self.assertEqual([r[0] for r in left], [2])


class MigrationTest(unittest.TestCase):
    """Старая база (до версионирования) переходит на схему 4 с бэкапом и пересборкой каталога."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "prices.db")
        self.data_dir = type(DATA_DIR)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(self.db)),
                        patch("config.DATA_DIR", self.data_dir)]
        for p in self.patches:
            p.start()
        with get_connection() as conn:
            database._create_schema(conn.cursor())
            # Старый триггер FTS (на любое обновление) и данные «прода» без schema_version
            conn.execute("DROP TRIGGER products_au")
            conn.execute("""CREATE TRIGGER products_au AFTER UPDATE ON products BEGIN
                INSERT INTO products_fts(products_fts, rowid, id, title, shop, city, category)
                VALUES('delete', old.rowid, old.id, old.title, old.shop, old.city, old.category);
                INSERT INTO products_fts(rowid, id, title, shop, city, category)
                VALUES (new.rowid, new.id, new.title, new.shop, new.city, new.category); END;""")
            conn.execute("INSERT INTO schema_metadata VALUES ('identity_v2', '1')")
            conn.execute("""INSERT INTO products (id, shop, city, title, url, current_price, first_seen_price, min_price, max_price)
                            VALUES ('flip_1', 'Flip.kz', 'Регион не подтверждён', 'Батарейки', 'https://www.flip.kz/x', 1, 1, 1, 1)""")
            conn.execute("INSERT INTO alerts (product_id, alert_type, new_price) VALUES ('flip_1', 'MARKET_ARBITRAGE', 1)")
            conn.execute("INSERT INTO product_sources VALUES ('flip_1', 'flip', 'https://www.flip.kz/c', 1)")
            conn.execute("INSERT INTO notification_outbox (alert_id, user_id, payload, created_at) VALUES (1, 7, '{}', 0)")
            conn.execute("INSERT INTO shop_scans (shop_key, last_success_at) VALUES ('flip', '2026-09-18')")
            conn.execute("INSERT INTO users (id, first_name, settings) VALUES (7, 'Юзер', '{\"x\": 1}')")
            conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ('h', 7, '2099-01-01')")
            conn.execute("INSERT INTO tracked_categories (name, query, last_scanned_at, is_hot) VALUES ('Смартфоны', 'galaxy', '2026-09-18', 1)")
            conn.execute("INSERT INTO title_canonical_cache (title_hash, title, canonical_key) VALUES ('t', 'T', 'apple:iphone 15')")
            conn.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def _count(self, table):
        with get_connection() as conn:
            return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_legacy_database_is_backed_up_and_catalog_rebuilt(self):
        applied = database.run_migrations()
        self.assertEqual(applied, [1, 2, 3, 4, 5])
        for table in ("products", "alerts", "product_sources", "notification_outbox", "shop_scans", "price_observations"):
            self.assertEqual(self._count(table), 0, table)
        for table in ("users", "sessions", "tracked_categories", "title_canonical_cache"):
            self.assertEqual(self._count(table), 1, table)
        with get_connection() as conn:
            self.assertEqual(database.get_schema_version(conn), database.SCHEMA_VERSION)
            self.assertIsNone(conn.execute("SELECT last_scanned_at FROM tracked_categories").fetchone()[0])
            self.assertEqual(conn.execute("SELECT is_hot FROM tracked_categories").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT settings FROM users").fetchone()[0], '{"x": 1}')
            trigger = conn.execute("SELECT sql FROM sqlite_master WHERE name='products_au'").fetchone()[0]
            self.assertIn("UPDATE OF", trigger)
        backups = list((self.data_dir / "backups").glob("prices-pre-v5-*.db"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        try:
            self.assertEqual(backup.execute("SELECT id FROM products").fetchall(), [("flip_1",)])
        finally:
            backup.close()
        # Первый цикл после пересборки не рассылает уведомления
        self.assertTrue(database.notifications_muted())
        with patch.object(database.time, "time", return_value=__import__("time").time() + database.CATALOG_REBUILD_MUTE_SECONDS + 1):
            self.assertFalse(database.notifications_muted())
        # Повторный запуск ничего не делает и не создаёт новый бэкап
        self.assertEqual(database.run_migrations(), [])
        self.assertEqual(len(list((self.data_dir / "backups").glob("*.db"))), 1)

    def test_failed_migration_rolls_back_and_stops_start(self):
        def broken(conn):
            conn.execute("DELETE FROM users")
            raise RuntimeError("simulated")
        with patch.object(database, "MIGRATIONS", ((1, "broken", broken),)):
            with self.assertRaises(RuntimeError):
                database.run_migrations()
        self.assertEqual(self._count("users"), 1)
        with get_connection() as conn:
            self.assertEqual(database.get_schema_version(conn), 0)

    def test_fts_search_works_after_rebuild_and_price_update_skips_index(self):
        database.run_migrations()
        from database import save_or_update_product
        save_or_update_product({"id": "fts_1@astana", "shop": "Sulpak", "title": "Ноутбук Lenovo IdeaPad Slim 5",
                                "url": "https://www.sulpak.kz/g/x", "price": 300000, "city": "Астана"})
        save_or_update_product({"id": "fts_1@astana", "shop": "Sulpak", "title": "Ноутбук Lenovo IdeaPad Slim 5",
                                "url": "https://www.sulpak.kz/g/x", "price": 290000, "city": "Астана"})
        with get_connection() as conn:
            hits = conn.execute("SELECT id FROM products_fts WHERE products_fts MATCH 'ideapad'").fetchall()
            self.assertEqual([r[0] for r in hits], ["fts_1@astana"])
            # integrity-check бросает исключение, если индекс разошёлся с таблицей
            conn.execute("INSERT INTO products_fts(products_fts) VALUES('integrity-check')")


class MutedDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_muted_alert_is_recorded_without_deliveries(self):
        init_db()
        import web.server as server
        product = {"id": "mute_1@astana", "shop": "Sulpak", "title": "Телевизор", "price": 100000, "city": "Астана"}
        anomaly = {"type": "SUPER_DISCOUNT", "old_price": 300000, "new_price": 100000, "drop_pct": 66.7, "savings": 200000}
        with patch.object(server, "notifications_muted", return_value=True), \
             patch.object(server, "prepare_deliveries") as prepare, patch.object(server, "record_alert") as record:
            self.assertTrue(await server._process_anomaly(product, anomaly, "Sulpak"))
        prepare.assert_not_called()
        self.assertEqual(record.call_args.kwargs["deliveries"], [])


class TrackedHotTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM tracked_categories")
            for i in range(4):
                conn.execute("INSERT INTO tracked_categories (name, query, is_hot) VALUES (?, ?, 1)", (f"hot{i}", "q"))
            for i in range(3):
                conn.execute("INSERT INTO tracked_categories (name, query, is_hot) VALUES (?, ?, 0)", (f"roll{i}", "q"))
            conn.commit()

    def test_hot_never_exceeds_limit_and_leaves_room_for_rolling(self):
        from database import get_due_tracked_categories
        due = get_due_tracked_categories(3)
        self.assertEqual(len(due), 3)
        self.assertEqual(sum(1 for c in due if c["is_hot"]), 2)
        self.assertEqual(get_due_tracked_categories(1)[0]["name"].startswith("hot"), True)

    def test_hot_fill_free_slots_when_no_rolling(self):
        from database import get_due_tracked_categories
        with get_connection() as conn:
            conn.execute("DELETE FROM tracked_categories WHERE is_hot = 0")
            conn.commit()
        self.assertEqual(len(get_due_tracked_categories(3)), 3)


class TrackedHotApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_admin_only_hot_toggle(self):
        init_db()
        import auth
        import web.server as server
        from aiohttp.test_utils import TestClient, TestServer
        from database import create_session, upsert_telegram_user
        with get_connection() as conn:
            cid = conn.execute("INSERT INTO tracked_categories (name, query) VALUES ('api-hot', 'q')").lastrowid
            conn.commit()
        upsert_telegram_user({"id": 91100, "first_name": "Админ"})
        upsert_telegram_user({"id": 91101, "first_name": "Юзер"})
        app = server.create_app()
        app.cleanup_ctx.clear()
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {91100}):
            async with TestClient(TestServer(app)) as client:
                headers = {"Origin": str(client.make_url("/")).rstrip("/")}
                url = f"/api/categories/tracked/{cid}/hot"
                self.assertEqual((await client.post(url, json={"is_hot": True}, headers=headers)).status, 401)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: create_session(91101)})
                self.assertEqual((await client.post(url, json={"is_hot": True}, headers=headers)).status, 403)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: create_session(91100)})
                self.assertEqual((await client.post(url, json={"is_hot": True}, headers=headers)).status, 200)
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT is_hot FROM tracked_categories WHERE id=?", (cid,)).fetchone()[0], 1)


class MatchingCorpusTest(unittest.TestCase):
    """Размеченные пары: одна модель / разные модели."""
    SAME = [
        ("Apple iPhone 15 128GB Black", "Смартфон Apple iPhone 15 128Gb черный"),
        ("iPhone 15 128GB Black", "iPhone 15 128GB eSIM Blue"),                    # вариант SIM не указан с одной стороны
        ("Apple iPhone 15 Pro 256GB Dual SIM Natural", "iPhone 15 Pro 256 ГБ Dual Sim Titanium"),
        ("Samsung Galaxy S24 Ultra 12/256GB Black", "Смартфон Samsung Galaxy S24 Ultra 12GB/256GB серый"),
    ]
    DIFFERENT = [
        ("Apple iPhone 15 128GB eSIM Black", "Apple iPhone 15 128GB Dual SIM White"),  # воспроизведение аудита H09
        ("iPhone 15 128GB nano-SIM + eSIM", "iPhone 15 128GB eSIM"),
        ("iPhone 15 128GB", "iPhone 15 256GB"),
        ("iPhone 15 Pro 256GB", "iPhone 15 Pro Max 256GB"),
        ("Samsung Galaxy S24 Ultra 12/256GB", "Samsung Galaxy S24 Ultra 12/512GB"),
    ]

    def test_corpus(self):
        from model_matching import same_model
        for a, b in self.SAME:
            self.assertTrue(same_model(a, b), (a, b))
        for a, b in self.DIFFERENT:
            self.assertFalse(same_model(a, b), (a, b))

    def test_ai_key_format(self):
        from model_matching import valid_ai_canonical_key
        for good in ("apple:iphone 15:128gb", "palit:rtx 4060:8gb", "lg:27gp850:27", "samsung:galaxy s24 ultra:256gb"):
            self.assertTrue(valid_ai_canonical_key(good), good)
        for bad in ("", "iphone 15", "apple:", "a:b:c:d", "<b>:x", "apple:" + "x" * 200, "apple:iphone\n15", None, 5):
            self.assertFalse(valid_ai_canonical_key(bad), repr(bad))


if __name__ == "__main__":
    unittest.main()
