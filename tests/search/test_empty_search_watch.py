"""ES-01/ES-03 (6.58.1): наблюдение «уведомить о появлении» из пустой выдачи хранит условия поиска.

Payload повторяет buildSearchWatchPayload из search_first.html (её разбор проверяет
tests/frontend/test_frontend_empty_search_actions.cjs). Здесь — серверная половина: такое наблюдение
на временной SQLite пропускает предложения дороже предела, с чужим состоянием и без нужного размера.
"""
from tests import test_support  # noqa: F401

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from repositories.schema_v2 import init_schema_v2
from tests.search.test_card_attributes_consistency import seed


def frontend_payload(query, city, size, max_price=None, min_price=None, condition=None, gift=False, seller=None):
    spec = {
        "attributes": {"size": size} if size else {},
        "brand": None, "category": None, "gift": True if gift else None, "inferred_attributes": {}, "kind": None,
        "max_price": max_price, "min_price": min_price, "model": None,
        "original_query": query, "query": query, "seller": seller,
    }
    payload = {
        "kind": "search", "target": "@local-search:" + json.dumps(spec, ensure_ascii=False),
        "title": query, "condition": "size_in_stock" if size else "new_offer", "repeat": False,
        "item_conditions": [condition] if condition else [], "city": city,
        "cooldown_hours": 0, "quiet_from": "00:00", "quiet_to": "00:00",
    }
    if size:
        payload["target_size"] = size
    return payload


class EmptySearchWatchKeepsConditionsTest(unittest.TestCase):
    USER = 401

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "es01.db"
        self._p = patch.object(database, "DB_PATH", self.db_path)
        self._p.start()
        database.init_db()
        with database.get_connection() as conn:
            init_schema_v2(conn)
            seed(conn, pid="nike1", title="Кроссовки Nike Air Zoom", category="Обувь", brand="Nike", price=25000)
            conn.commit()
        self.now = datetime.datetime(2026, 11, 23, 12, 0, tzinfo=datetime.timezone.utc)
        self.n = 0

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def offer(self, price, condition, sizes, seller="shop", **extra):
        self.n += 1
        return {"id": f"onike1_{self.n}", "offer_id": f"onike1_{self.n}", "canonical_product_id": "nike1",
                "product_id": "nike1", "title": "Кроссовки Nike Air Zoom", "shop": "Shop", "city": "Астана",
                "current_price": price, "is_available": True, "condition": condition,
                "item_condition": condition, "available_sizes": list(sizes),
                "seller_slug": seller, "seller_id": seller, "seller_name": seller.title(), **extra}

    def events(self):
        with database.get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM notification_outbox").fetchone()[0]

    def test_new_nike_42_under_30000_skips_used_expensive_and_wrong_size(self):
        w = database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "42",
                                                              max_price=30000, condition="new"))
        self.assertEqual((w["condition"], w["target_size"], w["item_conditions"]), ("size_in_stock", "42", ["new"]))
        t = self.now
        cases = [
            (self.offer(60000, "used", ["42"]), "б/у дороже предела (исходный дефект ES-01)"),
            (self.offer(60000, "new", ["42"]), "новый, но дороже предела"),
            (self.offer(25000, "used", ["42"]), "в пределе, но б/у"),
            (self.offer(25000, "new", ["41"]), "новый в пределе, но без размера 42"),
        ]
        for offer, why in cases:
            t += datetime.timedelta(minutes=5)
            self.assertEqual(database.evaluate_watches([offer], now=t), 0, why)
        self.assertEqual(self.events(), 0)
        t += datetime.timedelta(minutes=5)
        self.assertEqual(database.evaluate_watches([self.offer(25000, "new", ["41", "42"])], now=t), 1,
                         "новый Nike 42 до 30 000 ₸ — ровно одно уведомление")
        with database.get_connection() as conn:
            reason = conn.execute("SELECT reason FROM watch_events").fetchone()[0]
        self.assertEqual(reason, "появился размер 42")

    def test_without_size_new_offer_respects_price_and_condition(self):
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "",
                                                          max_price=30000, condition="new"))
        t = self.now
        for offer in (self.offer(60000, "used", []), self.offer(60000, "new", []), self.offer(25000, "used", [])):
            t += datetime.timedelta(minutes=5)
            self.assertEqual(database.evaluate_watches([offer], now=t), 0)
        t += datetime.timedelta(minutes=5)
        self.assertEqual(database.evaluate_watches([self.offer(25000, "new", [])], now=t), 1)


    def test_old_plain_payload_reproduces_es01(self):
        """Прежняя кнопка 6.58.0: только текст, город и размер — б/у за 60 000 ₸ проходит."""
        database.create_watch(self.USER, {"kind": "search", "target": "кроссовки Nike", "title": "кроссовки Nike",
                                          "condition": "size_in_stock", "target_size": "42", "repeat": False,
                                          "city": "Астана", "cooldown_hours": 0,
                                          "quiet_from": "00:00", "quiet_to": "00:00"})
        self.assertEqual(database.evaluate_watches([self.offer(60000, "used", ["42"])], now=self.now), 1)


    def test_gift_filter_false_and_true_match(self):
        """ES-04: «С подарком» — без подарка нет уведомления, с подарком есть (поле предложения и БД)."""
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "42", max_price=30000, gift=True))
        t = self.now
        for offer, why in ((self.offer(25000, "new", ["42"], has_gift=0), "без подарка"),
                           (self.offer(25000, "new", ["42"]), "признак неизвестен и в БД нет — не подарок")):
            t += datetime.timedelta(minutes=5)
            self.assertEqual(database.evaluate_watches([offer], now=t), 0, why)
        t += datetime.timedelta(minutes=5)
        self.assertEqual(database.evaluate_watches([self.offer(25000, "new", ["41", "42"], has_gift=1)], now=t), 1)

    def test_gift_read_from_offers_table_when_offer_dict_lacks_it(self):
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "", max_price=30000, gift=True))
        with database.get_connection() as conn:
            conn.execute("UPDATE offers SET has_gift = 1 WHERE id = 'onike1'")
            conn.commit()
        stored = {"id": "onike1", "offer_id": "onike1", "canonical_product_id": "nike1", "product_id": "nike1",
                  "title": "Кроссовки Nike Air Zoom", "shop": "Shop", "city": "Астана", "current_price": 25000,
                  "is_available": True, "condition": "new", "available_sizes": [], "seller_slug": "shop"}
        self.assertEqual(database.evaluate_watches([stored], now=self.now), 1)

    def test_seller_filter_false_and_true_match(self):
        """ES-04: «Продавец» — чужой продавец не уведомляет, нужный уведомляет."""
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "42", max_price=30000,
                                                          seller="approved-seller"))
        t = self.now + datetime.timedelta(minutes=5)
        self.assertEqual(database.evaluate_watches([self.offer(25000, "new", ["42"], seller="other-seller")], now=t), 0)
        t += datetime.timedelta(minutes=5)
        self.assertEqual(database.evaluate_watches([self.offer(25000, "new", ["42"], seller="approved-seller")], now=t), 1)

    def test_seller_is_exact_like_search_not_substring(self):
        """ES-04: approved-seller не совпадает с not-approved-seller при всех остальных подходящих условиях."""
        for size in ("42", ""):
            with self.subTest(mode="size_in_stock" if size else "new_offer"):
                with database.get_connection() as conn:
                    for t in ("notification_outbox", "watch_events", "watch_state", "watches"):
                        conn.execute(f"DELETE FROM {t}")
                    conn.commit()
                database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", size, max_price=30000,
                                                                  condition="new", gift=True,
                                                                  seller="approved-seller"))
                sizes = ["42"] if size else []
                t = self.now + datetime.timedelta(minutes=5)
                self.assertEqual(database.evaluate_watches(
                    [self.offer(25000, "new", sizes, seller="not-approved-seller", has_gift=1)], now=t), 0)
                t += datetime.timedelta(minutes=5)
                self.assertEqual(database.evaluate_watches(
                    [self.offer(25000, "new", sizes, seller="approved-seller", has_gift=1)], now=t), 1)

    def test_seller_read_from_db_when_offer_dict_lacks_it(self):
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "", max_price=30000, seller="shop"))
        stored = {"id": "onike1", "offer_id": "onike1", "canonical_product_id": "nike1", "product_id": "nike1",
                  "title": "Кроссовки Nike Air Zoom", "shop": "Shop", "city": "Астана", "current_price": 25000,
                  "is_available": True, "condition": "new", "available_sizes": []}
        self.assertEqual(database.evaluate_watches([stored], now=self.now), 1)

    def test_manual_watch_sellers_keep_historical_substring(self):
        """Ручные наблюдения (поле sellers) по-прежнему принимают вхождение — их семантика не меняется."""
        import watches
        w = watches.normalize({"kind": "search", "target": "кроссовки", "condition": "new_offer",
                               "sellers": ["approved-seller"]})
        offer = {"id": "x", "seller_slug": "not-approved-seller", "title": "кроссовки"}
        self.assertTrue(any(ws in offer["seller_slug"] for ws in w["sellers"]))

    def test_old_payload_without_gift_and_seller_reproduces_es04(self):
        """Цель 6.58.1 без gift и sellers пропускает товар без подарка от другого продавца."""
        database.create_watch(self.USER, frontend_payload("кроссовки Nike", "Астана", "42", max_price=30000))
        offer = self.offer(25000, "new", ["42"], seller="other-seller", has_gift=0)
        self.assertEqual(database.evaluate_watches([offer], now=self.now), 1)


class ShowcaseSyncSellerWatchTest(unittest.TestCase):
    """ES-04: штатный обход витрины Kaspi кладёт в seller_slug ID, а в seller_name имя канала.

    Подменён только ответ сканера. Наблюдение по фасету продавца сравнивает канонические ID, slug и имя
    продавца этого предложения из БД — и не совпадает с именем канала или чужим slug с общим началом.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "es04_showcase.db"
        self._p = patch.object(database, "DB_PATH", self.db_path)
        self._p.start()
        database.init_db()
        with database.get_connection() as conn:
            init_schema_v2(conn)
            conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) "
                         "VALUES ('s_nike', 'Nike Official', 'nike-store', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) "
                         "VALUES ('s_other', 'Other Shop', 'other-shop', '2026-01-01', '2026-01-01')")
            conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, base_url, created_at) "
                         "VALUES ('ch_kaspi', 's_nike', 'kaspi', 'Витрина Kaspi Nike', "
                         "'https://kaspi.kz/shop/info/merchant/nikestore/', '2026-01-01')")
            conn.commit()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def test_slug_id_and_name_match_channel_name_and_foreign_slug_do_not(self):
        import asyncio
        from domain.channel_sync import sync_channel_showcase
        from scrapers.showcase_scanners import KaspiShowcaseScanner, ShowcaseOffer

        cases = {"nike-store": True, "s_nike": True, "Nike Official": True,
                 "Витрина Kaspi Nike": False, "nike": False, "other-shop": False}
        users = {}
        for n, seller in enumerate(cases, start=500):
            database.upsert_telegram_user({"id": n, "first_name": "U"})
            w = database.create_watch(n, frontend_payload("кроссовки Nike", None, "", max_price=60000, seller=seller))
            users[w["id"]] = seller

        offer = ShowcaseOffer(marketplace="kaspi", merchant_id="nikestore", external_sku="sku_nike_1",
                              title="Кроссовки Nike Air Zoom Pegasus", price=45000.0,
                              url="https://kaspi.kz/shop/p/nike1", city="Алматы", in_stock=True)

        async def run():
            conn = database.get_connection()
            try:
                with patch.object(KaspiShowcaseScanner, "scan_merchant_showcase", return_value=[offer]):
                    return await sync_channel_showcase(conn, "ch_kaspi")
            finally:
                conn.close()

        result = asyncio.run(run())
        self.assertTrue(result["ok"])
        with database.get_connection() as conn:
            fired = {users[r[0]] for r in conn.execute("SELECT DISTINCT watch_id FROM watch_events")}
        self.assertEqual(fired, {s for s, ok in cases.items() if ok},
                         "по slug, ID и имени продавца — да; по имени канала и чужому slug — нет")


if __name__ == "__main__":
    unittest.main()
