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


class SizeWatchRearmTest(unittest.TestCase):
    """SW-01/SW-02: смена размера у сработавшего одноразового наблюдения и псевдоним size."""

    BASE = {"kind": "product", "target": "cp_kari_100", "title": "Кроссовки Kari Runner",
            "condition": "size_in_stock", "cooldown_hours": 0, "quiet_from": "00:00", "quiet_to": "00:00"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_size_rearm.db"
        self._patcher = patch.object(database, "DB_PATH", self.db_path)
        self._patcher.start()
        database.init_db()
        self.t0 = datetime.datetime(2026, 11, 23, 10, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def _offer(self, sizes, price=20000):
        return {"id": "kari_100", "canonical_product_id": "cp_kari_100", "title": "Кроссовки Kari Runner",
                "shop": "Kari", "city": "Алматы", "current_price": price, "is_available": True,
                "available_sizes": list(sizes)}

    def _at(self, minutes):
        return self.t0 + datetime.timedelta(minutes=minutes)

    def _fired_one_shot_41(self, user_id):
        w = database.create_watch(user_id, {**self.BASE, "target_size": "41", "repeat": False})
        self.assertFalse(w["repeat"])
        self.assertEqual(database.evaluate_watches([self._offer(["40"])], now=self._at(0)), 0)
        self.assertEqual(database.evaluate_watches([self._offer(["40", "41"])], now=self._at(5)), 1)
        with database.get_connection() as conn:
            row = conn.execute("SELECT fired_at FROM watches WHERE id = ?", (w["id"],)).fetchone()
            pending = conn.execute("SELECT COUNT(*) FROM notification_outbox WHERE status = 'pending'").fetchone()[0]
        self.assertIsNotNone(row["fired_at"])
        self.assertEqual(pending, 1)
        return w

    def _outbox(self):
        with database.get_connection() as conn:
            return [(r["status"], r["last_error"], json_reason(r["payload"]))
                    for r in conn.execute("SELECT status, last_error, payload FROM notification_outbox ORDER BY id")]

    def _assert_rearmed_and_fires_for_42(self, watch_id):
        with database.get_connection() as conn:
            row = conn.execute("SELECT fired_at, target_size, is_active FROM watches WHERE id = ?", (watch_id,)).fetchone()
            ev_status = [r[0] for r in conn.execute(
                "SELECT status FROM watch_events WHERE watch_id = ? ORDER BY id", (watch_id,))]
        self.assertIsNone(row["fired_at"])
        self.assertEqual(row["target_size"], "42")
        self.assertEqual(row["is_active"], 1)
        self.assertEqual(ev_status, ["cancelled"])
        self.assertEqual(self._outbox(), [("cancelled", "watch size changed", "появился размер 41")])
        # Размер 42 ещё не в наличии, затем появляется — ровно одно новое событие
        self.assertEqual(database.evaluate_watches([self._offer(["40", "41"])], now=self._at(10)), 0)
        self.assertEqual(database.evaluate_watches([self._offer(["41", "42"])], now=self._at(15)), 1)
        self.assertEqual(self._outbox()[-1], ("pending", None, "появился размер 42"))

    def test_update_watch_changes_size_rearms_and_cancels_old_pending(self):
        w = self._fired_one_shot_41(201)
        updated = database.update_watch(201, w["id"], {"target_size": "42"})
        self.assertEqual(updated["target_size"], "42")
        self._assert_rearmed_and_fires_for_42(w["id"])

    def test_repeated_create_changes_size_rearms_and_cancels_old_pending(self):
        w = self._fired_one_shot_41(202)
        again = database.create_watch(202, {**self.BASE, "size": "42", "repeat": False})
        self.assertTrue(again["already_existed"])
        self._assert_rearmed_and_fires_for_42(w["id"])

    def test_update_watch_size_alias_overrides_stored_target_size(self):
        w = database.create_watch(203, {**self.BASE, "target_size": "41"})
        updated = database.update_watch(203, w["id"], {"size": "42"})
        self.assertEqual(updated["target_size"], "42")
        # Явный target_size главнее псевдонима в том же запросе
        both = database.update_watch(203, w["id"], {"target_size": "43", "size": "44"})
        self.assertEqual(both["target_size"], "43")

    def test_update_other_fields_keeps_size_and_does_not_rearm(self):
        w = self._fired_one_shot_41(204)
        updated = database.update_watch(204, w["id"], {"title": "Новое имя", "city": "Астана"})
        self.assertEqual(updated["target_size"], "41")
        with database.get_connection() as conn:
            fired = conn.execute("SELECT fired_at FROM watches WHERE id = ?", (w["id"],)).fetchone()[0]
        self.assertIsNotNone(fired)
        self.assertEqual(self._outbox(), [("pending", None, "появился размер 41")])

    def test_size_changed_during_scan_writes_no_event_or_state_for_old_size(self):
        w = database.create_watch(205, {**self.BASE, "target_size": "41", "repeat": True})
        self.assertEqual(database.evaluate_watches([self._offer(["40"])], now=self._at(0)), 0)

        real_scope = database._watch_scope_id
        changed = {"done": False}

        def change_size_mid_scan(watch, offer):
            if not changed["done"]:
                changed["done"] = True
                database.update_watch(205, w["id"], {"target_size": "42"})
            return real_scope(watch, offer)

        # Обход взял снимок с размером 41; пока он идёт, пользователь меняет размер на 42
        with patch.object(database, "_watch_scope_id", side_effect=change_size_mid_scan):
            queued = database.evaluate_watches([self._offer(["40", "41"])], now=self._at(5))
        self.assertTrue(changed["done"])
        self.assertEqual(queued, 0)
        self.assertEqual(self._outbox(), [])
        with database.get_connection() as conn:
            states = conn.execute("SELECT COUNT(*) FROM watch_state WHERE watch_id = ?", (w["id"],)).fetchone()[0]
        self.assertEqual(states, 0)
        # Следующий обход видит размер 42 впервые и сообщает о нём
        self.assertEqual(database.evaluate_watches([self._offer(["41", "42"])], now=self._at(10)), 1)
        self.assertEqual(self._outbox()[-1], ("pending", None, "появился размер 42"))


class SizeWatchDeliveryRaceTest(unittest.TestCase):
    """SW-03: смена размера после того, как отправщик забрал задание из очереди."""

    USER = 301
    BASE = {"kind": "product", "target": "cp_kari_100", "title": "Кроссовки Kari Runner",
            "condition": "size_in_stock", "cooldown_hours": 0, "quiet_from": "00:00", "quiet_to": "00:00",
            "repeat": False}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_size_delivery.db"
        self._patcher = patch.object(database, "DB_PATH", self.db_path)
        self._patcher.start()
        database.init_db()
        database.upsert_telegram_user({"id": self.USER, "first_name": "S"})
        database.save_user_settings(self.USER, {"telegram_notify_enabled": True})
        database.save_or_update_products_batch([
            {"id": pid, "title": "Кроссовки Kari Runner", "price": 20000, "shop": "Kari", "city": "Алматы",
             "url": f"https://kari.example/{pid}", "category": "Kari: 👟 Обувь"} for pid in ("kari_100", "kari_101")])
        self.t0 = datetime.datetime(2026, 11, 23, 10, 0, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self._patcher.stop()
        self._tmp.cleanup()

    def _offer(self, sizes, pid="kari_100"):
        return {"id": pid, "canonical_product_id": "cp_" + pid, "title": "Кроссовки Kari Runner",
                "shop": "Kari", "city": "Алматы", "current_price": 20000, "is_available": True,
                "available_sizes": list(sizes)}

    def _at(self, minutes):
        return self.t0 + datetime.timedelta(minutes=minutes)

    def _fire_41(self, extra=None):
        w = database.create_watch(self.USER, {**self.BASE, "target_size": "41", **(extra or {})})
        offers = [self._offer(["40"]), self._offer(["40"], "kari_101")]
        database.evaluate_watches(offers, now=self._at(0))
        offers = [self._offer(["40", "41"]), self._offer(["40", "41"], "kari_101")]
        database.evaluate_watches(offers, now=self._at(5))
        with database.get_connection() as conn:
            conn.execute("UPDATE notification_outbox SET next_attempt_at = 0 WHERE status = 'pending'")
            conn.commit()
        return w

    def _watch(self, wid):
        with database.get_connection() as conn:
            return dict(conn.execute("SELECT is_active, fired_at, target_size FROM watches WHERE id = ?",
                                     (wid,)).fetchone())

    def _deliver(self, **patches):
        import notifier
        sent = []

        def fake_send(user_id, payload):
            sent.append(payload.get("reason"))
            hook = patches.get("during_send")
            if hook:
                hook()
            return patches["result"] if "result" in patches else notifier.DeliveryResult("sent")

        def fake_digest(user_id, payloads):
            sent.append([pl.get("reason") for pl in payloads])
            return notifier.DeliveryResult("sent")

        ctx = [patch.object(notifier, "get_bot_token", lambda: "token"),
               patch.object(notifier, "send_watch_message", side_effect=fake_send),
               patch.object(notifier, "send_watch_digest", side_effect=fake_digest)]
        if "after_claim" in patches:
            real_claim = database.claim_notification

            def claim_then_change():
                item = real_claim()
                if item:
                    patches["after_claim"]()
                return item
            ctx.append(patch.object(database, "claim_notification", side_effect=claim_then_change))
        if "after_digest_claim" in patches:
            real_digest_claim = database.claim_watch_digest

            def digest_claim_then_change(*a, **kw):
                rows = real_digest_claim(*a, **kw)
                patches["after_digest_claim"]()
                return rows
            ctx.append(patch.object(database, "claim_watch_digest", side_effect=digest_claim_then_change))
        for c in ctx:
            c.start()
        try:
            notifier.deliver_pending(limit=10)
        finally:
            for c in reversed(ctx):
                c.stop()
        return sent

    def _statuses(self):
        with database.get_connection() as conn:
            return [r[0] for r in conn.execute("SELECT status FROM notification_outbox ORDER BY id")]

    def test_size_changed_after_claim_before_send_is_not_delivered(self):
        w = self._fire_41()
        changed = []
        sent = self._deliver(after_claim=lambda: changed or changed.append(
            database.update_watch(self.USER, w["id"], {"target_size": "42"})))
        self.assertEqual(sent, [], "устаревшее сообщение о 41 не должно уйти")
        self.assertEqual(set(self._statuses()), {"cancelled"})
        st = self._watch(w["id"])
        self.assertEqual((st["is_active"], st["fired_at"], st["target_size"]), (1, None, "42"))
        # Появление 42 — новое сообщение и только после него одноразовое наблюдение выключается
        self.assertEqual(database.evaluate_watches([self._offer(["41", "42"])], now=self._at(10)), 1)
        with database.get_connection() as conn:
            conn.execute("UPDATE notification_outbox SET next_attempt_at = 0 WHERE status = 'pending'")
            conn.commit()
        self.assertEqual(self._deliver(), ["появился размер 42"])
        self.assertEqual(self._watch(w["id"])["is_active"], 0)

    def test_size_changed_while_sending_keeps_new_watch_armed(self):
        w = self._fire_41()
        changed = []
        sent = self._deliver(during_send=lambda: changed or changed.append(
            database.update_watch(self.USER, w["id"], {"target_size": "42"})))
        self.assertEqual(sent[0], "появился размер 41", "уже переданное в Telegram не отменить")
        st = self._watch(w["id"])
        self.assertEqual((st["is_active"], st["fired_at"], st["target_size"]), (1, None, "42"),
                         "завершение отправки не гасит заново взведённое наблюдение")
        self.assertEqual(database.evaluate_watches([self._offer(["41", "42"])], now=self._at(10)), 1)

    def test_digest_drops_items_cancelled_after_digest_claim(self):
        # Наблюдение по магазину: у каждого товара своё срабатывание, сводка собирает оба
        w = self._fire_41({"kind": "shop", "target": "Kari", "mode": "digest", "repeat": True})
        self.assertEqual(self._statuses(), ["pending", "pending"])
        changed = []
        sent = self._deliver(after_digest_claim=lambda: changed or changed.append(
            database.update_watch(self.USER, w["id"], {"target_size": "42"})))
        # Сняты оба задания, включая первое: до вызова Telegram дело не дошло — ничего не уходит
        self.assertEqual(sent, [])
        self.assertEqual(self._statuses(), ["cancelled", "cancelled"])
        st = self._watch(w["id"])
        self.assertEqual((st["is_active"], st["target_size"]), (1, "42"))
        with database.get_connection() as conn:
            ev = [r[0] for r in conn.execute("SELECT status FROM watch_events ORDER BY id")]
        self.assertEqual(ev, ["cancelled", "cancelled"])

    def test_retry_after_cancel_during_send_keeps_task_cancelled(self):
        import notifier
        for result in (notifier.DeliveryResult("retry", None, "сеть"),
                       notifier.DeliveryResult("permanent", None, "bot blocked")):
            with self.subTest(result=result.status):
                with database.get_connection() as conn:
                    conn.execute("DELETE FROM notification_outbox")
                    conn.execute("DELETE FROM watch_events")
                    conn.execute("DELETE FROM watches")
                    conn.execute("DELETE FROM watch_state")
                    conn.execute("DELETE FROM users WHERE id <> ?", (self.USER,))
                    conn.commit()
                database.upsert_telegram_user({"id": self.USER, "first_name": "S"})
                database.save_user_settings(self.USER, {"telegram_notify_enabled": True})
                w = self._fire_41()
                changed = []
                sent = self._deliver(result=result, during_send=lambda: changed or changed.append(
                    database.update_watch(self.USER, w["id"], {"target_size": "42"})))
                self.assertEqual(sent, ["появился размер 41"])
                self.assertEqual(self._statuses(), ["cancelled"],
                                 "повтор/неудача не возвращают в очередь задание, снятое во время отправки")
                st = self._watch(w["id"])
                self.assertEqual((st["is_active"], st["fired_at"], st["target_size"]), (1, None, "42"))


def json_reason(payload):
    import json
    return json.loads(payload).get("reason")


if __name__ == "__main__":
    unittest.main()
