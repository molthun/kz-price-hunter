"""CA-03/05/08/09: сквозные проверки на настоящей SQLite: товар -> лента -> карточка, основание цены,
пагинация с порогом скидки и действующий scope наблюдения."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

import database
import web.server as srv


def product(pid, price, old_on_site=None, key=None, title=None, shop="Sulpak", **extra):
    item = {"id": pid, "shop": shop, "city": "Алматы", "title": title or f"Item {pid}", "category": "smartphones",
            "price": price, "url": f"https://example.invalid/{pid}", "canonical_key": key or f"key {pid}"}
    if old_on_site:
        item["old_price_on_site"] = old_on_site
    item.update(extra)
    return item


class _DbMixin:
    def start_db(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name) / "prices.db")
        self.db_patch.start()
        database.init_db()

    def stop_db(self):
        self.db_patch.stop()
        self.tmp.cleanup()


class TestDealsApi(_DbMixin, AioHTTPTestCase):
    async def get_application(self):
        self.start_db()
        app = web.Application()
        app.add_routes([web.get("/api/v2/deals", srv.deals_v2_handler),
                        web.get("/api/v2/products/{product_id}", srv.product_detail_v2_handler)])
        return app

    async def tearDownAsync(self):
        self.stop_db()

    async def deals(self, query=""):
        return await (await self.client.get("/api/v2/deals" + query)).json()

    @unittest_run_loop
    async def test_feed_id_opens_real_card(self):
        database.save_or_update_products_batch([product("legacy-1", 10000, 20000)])
        deal = (await self.deals())["deals"][0]
        self.assertEqual(deal["product_id"], "legacy-1")
        self.assertRegex(deal["canonical_product_id"], r"^prod_[0-9a-f]{16}$")
        self.assertEqual((await self.client.get(f"/api/v2/products/{deal['canonical_product_id']}")).status, 200)

    @unittest_run_loop
    async def test_two_legacy_offers_of_one_product_share_canonical_id(self):
        database.save_or_update_products_batch([product("a", 10000, 20000, key="same"),
                                                product("b", 12000, 24000, key="same", shop="Mechta")])
        ids = {d["canonical_product_id"] for d in (await self.deals())["deals"]}
        self.assertEqual(len(ids), 1)
        self.assertEqual((await self.client.get(f"/api/v2/products/{ids.pop()}")).status, 200)

    @unittest_run_loop
    async def test_deal_without_v2_product_has_no_invented_id(self):
        deal = {"product_id": "orphan", "canonical_key": "no such product", "old_price": 200, "new_price": 100}
        database._annotate_deals([deal])
        self.assertIsNone(deal["canonical_product_id"])
        self.assertEqual(deal["price_basis"], "unconfirmed")

    @unittest_run_loop
    async def test_store_claim_on_first_observation_is_not_history(self):
        database.save_or_update_products_batch([product("first", 10000, 20000)])
        deal = (await self.deals())["deals"][0]
        self.assertEqual(deal["price_basis"], "store_claim")
        self.assertEqual(deal["provenance"], "Скидка по данным магазина")

    @unittest_run_loop
    async def test_observed_drop_is_history(self):
        database.save_or_update_products_batch([product("drop", 20000)])
        database.save_or_update_products_batch([product("drop", 10000, 20000)])
        deal = (await self.deals())["deals"][0]
        self.assertEqual(deal["price_basis"], "observed")
        self.assertEqual(deal["provenance"], "Снижение по нашим наблюдениям")

    @unittest_run_loop
    async def test_store_old_price_above_anything_observed_stays_store_claim(self):
        database.save_or_update_products_batch([product("mix", 15000)])
        database.save_or_update_products_batch([product("mix", 10000, 30000)])
        self.assertEqual((await self.deals())["deals"][0]["price_basis"], "store_claim")

    @unittest_run_loop
    async def test_arbitrage_and_unconfirmed_alert(self):
        database.save_or_update_products_batch([product("arb", 10000), product("sup", 10000, key="k sup")])
        with database.get_connection() as conn:
            conn.execute("""INSERT INTO alerts (shop, city, product_id, alert_type, old_price, new_price, discount_pct,
                            savings_kzt, competitor_shop, competitor_seen_at, is_dismissed)
                            VALUES ('Sulpak', 'Алматы', 'arb', 'MARKET_ARBITRAGE', 20000, 10000, 50, 10000, 'Mechta',
                                    datetime('now'), 0)""")
            conn.execute("""INSERT INTO alerts (shop, city, product_id, alert_type, old_price, new_price, discount_pct,
                            savings_kzt, is_dismissed)
                            VALUES ('Sulpak', 'Алматы', 'sup', 'SUPER_DISCOUNT', 40000, 10000, 75, 30000, 0)""")
            conn.commit()
        by_id = {d["product_id"]: d for d in (await self.deals())["deals"]}
        self.assertEqual(by_id["arb"]["price_basis"], "market")
        self.assertEqual(by_id["sup"]["price_basis"], "unconfirmed")
        self.assertEqual(by_id["sup"]["provenance"], "Основание скидки не подтверждено")

    @unittest_run_loop
    async def test_min_discount_empty_selection_terminates(self):
        database.save_or_update_products_batch([product("half", 10000, 20000)])
        data = await self.deals("?min_discount=60&limit=1")
        self.assertEqual((data["deals"], data["total"], data["has_more"]), ([], 0, False))

    @unittest_run_loop
    async def test_min_discount_paginates_without_gaps_in_every_sort(self):
        # Чётные — со скидкой 50%, нечётные — 10%; подходящие идут вперемешку с остальными
        items = [product(f"m{i:02d}", 10000 + i, 20000 + 2 * i if i % 2 == 0 else int((10000 + i) / 0.9))
                 for i in range(12)]
        database.save_or_update_products_batch(items)
        expected = {f"m{i:02d}" for i in range(12) if i % 2 == 0}
        for sort in ("discount_desc", "savings_desc", "price_asc"):
            with self.subTest(sort=sort):
                seen, offset = [], 0
                for _ in range(20):
                    data = await self.deals(f"?min_discount=40&limit=2&offset={offset}&sort={sort}")
                    self.assertEqual(data["total"], len(expected))
                    seen += [d["product_id"] for d in data["deals"]]
                    offset += len(data["deals"])
                    if not data["has_more"]:
                        break
                else:
                    self.fail("has_more не завершился")
                self.assertEqual(len(seen), len(set(seen)), "повторы")
                self.assertEqual(set(seen), expected)


class TestWatchScope(_DbMixin, unittest.TestCase):
    USER = 909

    def setUp(self):
        self.start_db()
        database.upsert_telegram_user({"id": self.USER, "first_name": "W"})

    def tearDown(self):
        self.stop_db()

    def stored(self, watch_id):
        return next(w for w in database.list_watches(self.USER) if w["id"] == watch_id)

    def test_recreate_applies_confirmed_city_and_delivery(self):
        base = {"kind": "product", "target": "prod_x", "condition": "any_drop"}
        first = database.create_watch(self.USER, {**base, "city": "Алматы", "quiet_from": "22:00"})
        self.assertNotIn("already_existed", first)
        second = database.create_watch(self.USER, {**base, "city": "Астана", "quiet_from": "21:30", "mode": "digest"})
        self.assertEqual(second["id"], first["id"])
        self.assertTrue(second["already_existed"])
        stored = self.stored(first["id"])
        self.assertEqual((second["city"], stored["city"]), ("Астана", "Астана"))
        self.assertEqual((stored["quiet_from"], stored["mode"]), ("21:30", "digest"))

    def test_recreate_without_field_keeps_it(self):
        base = {"kind": "product", "target": "prod_x", "condition": "any_drop"}
        first = database.create_watch(self.USER, {**base, "city": "Алматы", "quiet_from": "22:00"})
        database.create_watch(self.USER, base)
        stored = self.stored(first["id"])
        self.assertEqual((stored["city"], stored["quiet_from"]), ("Алматы", "22:00"))

    def test_city_can_be_cleared_explicitly(self):
        base = {"kind": "product", "target": "prod_x", "condition": "any_drop"}
        first = database.create_watch(self.USER, {**base, "city": "Алматы"})
        database.create_watch(self.USER, {**base, "city": None})
        self.assertIsNone(self.stored(first["id"])["city"])

    def test_recreate_at_limit_is_not_refused(self):
        import watches
        base = {"kind": "search", "condition": "any_drop"}
        for i in range(watches.MAX_WATCHES_PER_USER):
            database.create_watch(self.USER, {**base, "target": f"q{i}"})
        again = database.create_watch(self.USER, {**base, "target": "q0", "city": "Астана"})
        self.assertEqual(again["city"], "Астана")
        with self.assertRaises(ValueError):
            database.create_watch(self.USER, {**base, "target": "new"})

    def test_concurrent_create_without_threshold_gives_one_watch(self):
        results, errors = [], []

        def worker():
            try:
                results.append(database.create_watch(
                    self.USER, {"kind": "product", "target": "prod_c", "condition": "any_drop", "city": "Астана"})["id"])
            except Exception as exc:  # noqa: BLE001 - тест собирает любую ошибку потока
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(database.list_watches(self.USER)), 1)


if __name__ == "__main__":
    unittest.main()
