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


def frontend_payload(query, city, size, max_price=None, min_price=None, condition=None):
    spec = {
        "attributes": {"size": size} if size else {},
        "brand": None, "category": None, "inferred_attributes": {}, "kind": None,
        "max_price": max_price, "min_price": min_price, "model": None,
        "original_query": query, "query": query,
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

    def offer(self, price, condition, sizes):
        self.n += 1
        return {"id": f"onike1_{self.n}", "offer_id": f"onike1_{self.n}", "canonical_product_id": "nike1",
                "product_id": "nike1", "title": "Кроссовки Nike Air Zoom", "shop": "Shop", "city": "Астана",
                "current_price": price, "is_available": True, "condition": condition,
                "item_condition": condition, "available_sizes": list(sizes)}

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


if __name__ == "__main__":
    unittest.main()
