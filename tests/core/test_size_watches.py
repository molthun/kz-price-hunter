from tests import test_support  # noqa: F401

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import watches


class SizeWatchNormalizeTest(unittest.TestCase):
    def test_size_in_stock_normalizes_shoe_clothing_and_kids_height(self):
        w1 = watches.normalize({"kind": "product", "target": "p1", "condition": "size_in_stock", "target_size": "37,5"})
        self.assertEqual(w1["target_size"], "37.5")
        self.assertIn("появился размер 37.5", watches.describe(w1))

        w2 = watches.normalize({"kind": "product", "target": "p1", "condition": "size_in_stock", "size": "xl"})
        self.assertEqual(w2["target_size"], "XL")

        w3 = watches.normalize({"kind": "product", "target": "p1", "condition": "size_in_stock", "target_size": "рост 104 см"})
        self.assertEqual(w3["target_size"], "рост 104")

    def test_size_in_stock_requires_valid_target_size(self):
        with self.assertRaises(ValueError):
            watches.normalize({"kind": "product", "target": "p1", "condition": "size_in_stock"})
        with self.assertRaises(ValueError):
            watches.normalize({"kind": "product", "target": "p1", "condition": "size_in_stock", "target_size": "invalid_xyz"})
        with self.assertRaises(ValueError):
            watches.normalize({"kind": "deal", "target": "все", "condition": "size_in_stock", "target_size": "42"})

    def test_optional_target_size_on_price_watch_is_described(self):
        w = watches.normalize({"kind": "product", "target": "p1", "condition": "any_drop", "target_size": "42"})
        self.assertEqual(w["target_size"], "42")
        self.assertIn("размер: 42", watches.describe(w))


class SizeWatchLifecycleDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_size_watches.db"
        self._patcher = patch.object(database, "DB_PATH", self.db_path)
        self._patcher.start()
        database.init_db()
        self.t0 = datetime.datetime(2026, 11, 23, 10, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def _offer(self, price: int, sizes):
        return {
            "id": "kari_100",
            "canonical_product_id": "cp_kari_100",
            "title": "Кроссовки Kari Runner",
            "shop": "Kari",
            "city": "Алматы",
            "current_price": price,
            "is_available": True,
            "available_sizes": list(sizes),
        }

    def test_size_in_stock_triggers_on_appearance_and_deduplicates_until_restock(self):
        w = database.create_watch(101, {
            "kind": "product",
            "target": "cp_kari_100",
            "title": "Кроссовки Kari Runner",
            "condition": "size_in_stock",
            "target_size": "42",
            "cooldown_hours": 0,
            "quiet_from": "00:00",
            "quiet_to": "00:00",
            "repeat": True,
        })
        self.assertEqual(w["target_size"], "42")

        # 1. Size 42 not in stock -> 0 events, records was_available=0
        self.assertEqual(database.evaluate_watches([self._offer(20000, ["40", "41"])], now=self.t0), 0)

        # 2. Size 42 appears -> 1 event queued with clear reason
        q2 = database.evaluate_watches([self._offer(20000, ["40", "41", "42"])], now=self.t0 + datetime.timedelta(minutes=10))
        self.assertEqual(q2, 1)
        with database.get_connection() as conn:
            ev = conn.execute("SELECT reason FROM watch_events WHERE watch_id = ?", (w["id"],)).fetchone()
        self.assertEqual(ev["reason"], "появился размер 42")

        # 3. Still in stock -> 0 events (no spam!)
        q3 = database.evaluate_watches([self._offer(20000, ["40", "42"])], now=self.t0 + datetime.timedelta(minutes=20))
        self.assertEqual(q3, 0)

        # 4. Sells out -> 0 events, resets was_available=0
        q4 = database.evaluate_watches([self._offer(20000, ["40"])], now=self.t0 + datetime.timedelta(minutes=30))
        self.assertEqual(q4, 0)

        # 5. Restocked again -> 1 new event
        q5 = database.evaluate_watches([self._offer(19500, ["40", "42"])], now=self.t0 + datetime.timedelta(minutes=40))
        self.assertEqual(q5, 1)

    def test_updating_target_size_resets_watch_state_for_new_size(self):
        w = database.create_watch(102, {
            "kind": "product",
            "target": "cp_kari_100",
            "condition": "size_in_stock",
            "target_size": "41",
            "cooldown_hours": 0,
            "quiet_from": "00:00",
            "quiet_to": "00:00",
        })
        # Size 41 already in stock -> triggers once, sets was_available=1
        self.assertEqual(database.evaluate_watches([self._offer(20000, ["41", "42"])], now=self.t0), 1)
        self.assertEqual(database.evaluate_watches([self._offer(20000, ["41", "42"])], now=self.t0 + datetime.timedelta(minutes=5)), 0)

        # User changes target_size to 42 -> watch_state reset -> 42 triggers cleanly
        updated = database.create_watch(102, {
            "kind": "product",
            "target": "cp_kari_100",
            "condition": "size_in_stock",
            "target_size": "42",
            "cooldown_hours": 0,
            "quiet_from": "00:00",
            "quiet_to": "00:00",
        })
        self.assertTrue(updated.get("already_existed"))
        self.assertEqual(updated["target_size"], "42")
        self.assertEqual(database.evaluate_watches([self._offer(20000, ["41", "42"])], now=self.t0 + datetime.timedelta(minutes=10)), 1)

    def test_price_drop_watch_with_target_size_ignores_offers_without_that_size(self):
        database.create_watch(103, {
            "kind": "product",
            "target": "cp_kari_100",
            "condition": "any_drop",
            "target_size": "42",
            "cooldown_hours": 0,
            "quiet_from": "00:00",
            "quiet_to": "00:00",
        })
        # Initial observation with size 42 at 25000 -> baseline stored
        self.assertEqual(database.evaluate_watches([self._offer(25000, ["42"])], now=self.t0), 0)
        # Price drops to 18000, but size 42 is NOT in stock -> ignored
        self.assertEqual(database.evaluate_watches([self._offer(18000, ["40", "41"])], now=self.t0 + datetime.timedelta(minutes=10)), 0)
        # Price drops to 19000 WITH size 42 in stock -> triggers!
        self.assertEqual(database.evaluate_watches([self._offer(19000, ["40", "42"])], now=self.t0 + datetime.timedelta(minutes=20)), 1)


if __name__ == "__main__":
    unittest.main()
