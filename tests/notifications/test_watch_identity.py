from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
"""P17CR-02: наблюдения MODEL и PRODUCT продолжают следить за товаром после переноса ключа (миграция k2,
вердикт «один товар», повтор), без ложной рассылки и без регресса PRODUCT на legacy/offer ID."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import database
import watches as w
from database import DB_PATH

OLD, NEW = "audit:device:128gb", "k2:samsung:sms911b:128gb"


class WatchIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        data = Path(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(data / "prices.db")),
                        patch.object(config, "DATA_DIR", data),
                        patch.object(config, "SETTINGS_FILE", data / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()
        database.upsert_telegram_user({"id": 707, "first_name": "A"})
        database.upsert_telegram_user({"id": 808, "first_name": "B"})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def product(self, pid, title, shop, key, price=100000):
        database.save_or_update_product({"id": pid, "shop": shop, "city": "Алматы", "title": title, "price": price,
                                         "canonical_key": key, "url": f"https://example.kz/{pid}"})
        database.save_cached_canonical_keys_batch({title: key})

    def offer(self, pid):
        return database.watched_offers([pid])[0]

    def events(self, user_id=None):
        with database.get_connection() as conn:
            sql = "SELECT e.reason, w.user_id, w.kind FROM watch_events e JOIN watches w ON w.id = e.watch_id"
            rows = [tuple(r) for r in conn.execute(sql + (" WHERE w.user_id = ?" if user_id else ""),
                                                   (user_id,) if user_id else ())]
        return rows

    def test_model_and_product_watches_follow_rekey_and_rerun(self):
        self.product("p1", "Phone A", "Sulpak", OLD)
        before = self.offer("p1")
        watches = [database.create_watch(707, {"kind": "product", "target": before["canonical_product_id"],
                                               "condition": "any_drop", "city": "Алматы"}),
                   database.create_watch(707, {"kind": "model", "target": OLD, "condition": "any_drop",
                                               "city": "Алматы"}),
                   database.create_watch(707, {"kind": "product", "target": "p1", "condition": "any_drop"})]
        database.rekey_titles({"Phone A": NEW})
        database.rekey_titles({"Phone A": NEW})                       # повтор ничего не ломает
        after = self.offer("p1")
        self.assertNotEqual(after["canonical_product_id"], before["canonical_product_id"])
        self.assertEqual([w.matches(x, after) for x in database.list_watches(707)], [True, True, True])
        # Цепочка переносов: старая цель узнаётся и после второго переноса
        database.rekey_titles({"Phone A": NEW + "x"})
        again = self.offer("p1")
        self.assertEqual([w.matches(x, again) for x in database.list_watches(707)], [True, True, True])
        self.assertEqual(len(watches), 3)

    def test_price_drop_and_back_in_stock_after_rekey_without_spam(self):
        self.product("p1", "Phone A", "Sulpak", OLD, price=100000)
        pid = self.offer("p1")["canonical_product_id"]
        database.create_watch(707, {"kind": "model", "target": OLD, "condition": "any_drop", "city": "Алматы"})
        database.create_watch(707, {"kind": "product", "target": pid, "condition": "new_seller", "city": "Алматы"})
        database.create_watch(808, {"kind": "model", "target": "other:key", "condition": "any_drop"})
        database.evaluate_watches(database.watched_offers(["p1"]))     # память: цена и известный продавец
        baseline = self.events()                                       # первое появление продавца — как всегда
        database.rekey_titles({"Phone A": NEW})
        database.evaluate_watches(database.watched_offers(["p1"]))     # без изменений — без сообщений
        self.assertEqual(self.events(), baseline, "перенос ключа не создаёт ложных «новый продавец» и снижений")
        self.product("p1", "Phone A", "Sulpak", NEW, price=90000)
        database.evaluate_watches(database.watched_offers(["p1"]))
        self.assertEqual([(r[1], r[2]) for r in self.events()[len(baseline):]], [(707, "model")])
        self.assertEqual(self.events(808), [], "чужое наблюдение не срабатывает")

    def test_back_in_stock_keeps_offer_memory(self):
        self.product("p1", "Phone A", "Sulpak", OLD)
        database.create_watch(707, {"kind": "model", "target": OLD, "condition": "back_in_stock"})
        offer = self.offer("p1")
        database.evaluate_watches([dict(offer, is_available=0)])
        database.rekey_titles({"Phone A": NEW})
        database.evaluate_watches(database.watched_offers(["p1"]))
        events = self.events()
        self.assertEqual(len(events), 1, "снова в продаже — одно сообщение по памяти предложения")
        self.assertIn("снова", events[0][0])
        database.evaluate_watches(database.watched_offers(["p1"]))
        self.assertEqual(len(self.events()), 1, "без повторной рассылки")

    def test_same_merge_and_split_keep_old_target_tracking_everything(self):
        """При разделении группы наблюдение на прежний ключ следит за всеми её бывшими товарами."""
        self.product("a", "Phone A", "Sulpak", OLD)
        self.product("b", "Phone B", "DNS Казахстан", OLD)
        database.create_watch(707, {"kind": "model", "target": OLD, "condition": "any_drop"})
        database.rekey_titles({"Phone B": NEW})                      # B ушёл в новую группу, A остался
        watch = database.list_watches(707)[0]
        self.assertTrue(w.matches(watch, self.offer("a")))
        self.assertTrue(w.matches(watch, self.offer("b")))
        # Объединение по «один товар»: наблюдение на ключ второй группы узнаёт оба
        database.create_watch(707, {"kind": "model", "target": NEW, "condition": "any_drop"})
        database.unify_canonical_keys("Phone A", "Phone B", "k2:merged:1")
        for x in database.list_watches(707):
            self.assertTrue(w.matches(x, self.offer("a")) and w.matches(x, self.offer("b")), x["target"])

    def test_filters_and_unrelated_offers_not_widened(self):
        self.product("a", "Phone A", "Sulpak", OLD)
        self.product("c", "Other", "Sulpak", "other:key")
        database.create_watch(707, {"kind": "model", "target": OLD, "condition": "any_drop", "city": "Астана"})
        database.rekey_titles({"Phone A": NEW})
        watch = database.list_watches(707)[0]
        self.assertFalse(w.matches(watch, self.offer("a")), "фильтр города по-прежнему действует")
        self.assertFalse(w.matches(dict(watch, city=""), self.offer("c")), "чужой товар не становится целью")


if __name__ == "__main__":
    unittest.main()
