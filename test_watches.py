"""P07 Watch System: правила срабатывания, антиспам, тихие часы, права и изоляция пользователей."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import watches as w
from config import DB_PATH

UTC = datetime.timezone.utc


def offer(price, **kw):
    base = {"id": "p1", "title": "Смартфон Apple iPhone 15 128GB", "shop": "Kaspi", "city": "Астана",
            "url": "https://k.example/p1", "canonical_key": "apple iphone 15 128gb", "category": "Смартфоны",
            "current_price": price, "is_available": True}
    base.update(kw)
    return base


class RulesTest(unittest.TestCase):
    def watch(self, **kw):
        data = {"kind": w.PRODUCT, "target": "p1", "condition": w.ANY_DROP}
        data.update(kw)
        return w.normalize(data)

    def test_price_returning_to_the_same_value_is_not_a_new_drop(self):
        """Сценарий из плана: 149 990 → 151 000 → 149 990 не должен уведомлять дважды."""
        watch = self.watch()
        state = {"previous_price": 160000}
        met, _ = w.condition_met(watch, offer(149990), state)
        self.assertTrue(met)
        state = {"last_notified_price": 149990, "previous_price": 149990}
        self.assertFalse(w.condition_met(watch, offer(151000), state)[0])       # цена выросла — молчим
        state = {"last_notified_price": 149990, "previous_price": 151000}
        self.assertFalse(w.condition_met(watch, offer(149990), state)[0])       # вернулась — это не новость
        self.assertTrue(w.condition_met(watch, offer(145000), state)[0])        # ниже прежнего — снова событие

    def test_target_price_fires_once_until_price_goes_back_up(self):
        watch = self.watch(condition=w.TARGET_PRICE, threshold=150000)
        self.assertFalse(w.condition_met(watch, offer(151000), {})[0])
        met, reason = w.condition_met(watch, offer(150000), {})
        self.assertTrue(met)
        self.assertIn("150000", reason.replace(" ", ""))
        self.assertFalse(w.condition_met(watch, offer(150000), {"last_notified_price": 150000})[0])
        self.assertTrue(w.condition_met(watch, offer(140000), {"last_notified_price": 150000})[0])

    def test_percent_and_sum_conditions(self):
        pct = self.watch(condition=w.DROP_PCT, threshold=20)
        self.assertFalse(w.condition_met(pct, offer(90000), {"previous_price": 100000})[0])   # 10 % мало
        self.assertTrue(w.condition_met(pct, offer(75000), {"previous_price": 100000})[0])
        kzt = self.watch(condition=w.DROP_KZT, threshold=30000)
        self.assertFalse(w.condition_met(kzt, offer(80000), {"previous_price": 100000})[0])
        self.assertTrue(w.condition_met(kzt, offer(65000), {"previous_price": 100000})[0])

    def test_best_price_and_back_in_stock(self):
        best = self.watch(condition=w.BEST_PRICE)
        self.assertTrue(w.condition_met(best, offer(90000), {"min_price": 95000})[0])
        self.assertFalse(w.condition_met(best, offer(96000), {"min_price": 95000})[0])
        stock = self.watch(condition=w.BACK_IN_STOCK)
        self.assertTrue(w.condition_met(stock, offer(90000), {"was_available": 0})[0])
        self.assertFalse(w.condition_met(stock, offer(90000), {"was_available": 1})[0])
        # Пока товара нет в продаже, ценовые условия молчат
        self.assertFalse(w.condition_met(self.watch(), offer(1000, is_available=False),
                                         {"previous_price": 100000})[0])

    def test_quiet_hours_across_midnight_in_owner_timezone(self):
        watch = self.watch(quiet_from="23:00", quiet_to="08:00", timezone="Asia/Almaty")
        night = datetime.datetime(2026, 9, 20, 19, 0, tzinfo=UTC)      # 00:00 в Астане
        day = datetime.datetime(2026, 9, 20, 7, 0, tzinfo=UTC)         # 12:00 в Астане
        self.assertTrue(w.in_quiet_hours(watch, night))
        self.assertFalse(w.in_quiet_hours(watch, day))
        self.assertFalse(w.deliver_now(watch, night)[0])
        self.assertEqual(w.deliver_now(watch, night)[1], "тихие часы")
        self.assertTrue(w.deliver_now(watch, day)[0])
        # Накопленное уходит после тишины, а не среди ночи
        due = w.digest_due_at(watch, night)
        self.assertFalse(w.in_quiet_hours(watch, due))

    def test_cooldown_and_digest_hold_delivery(self):
        watch = self.watch(cooldown_hours=6)
        now = datetime.datetime(2026, 9, 20, 7, 0, tzinfo=UTC)
        self.assertFalse(w.deliver_now(watch, now, now - datetime.timedelta(hours=1))[0])
        self.assertTrue(w.deliver_now(watch, now, now - datetime.timedelta(hours=7))[0])
        digest = self.watch(mode=w.DIGEST)
        self.assertEqual(w.deliver_now(digest, now)[1], "сводка")

    def test_validation_explains_mistakes(self):
        for data, part in (({"kind": "planet", "target": "x"}, "вид"),
                           ({"kind": w.PRODUCT, "target": ""}, "Не указано"),
                           ({"kind": w.PRODUCT, "target": "p1", "condition": "magic"}, "условие"),
                           ({"kind": w.PRODUCT, "target": "p1", "condition": w.DROP_PCT, "threshold": 150}, "от 1 до 99"),
                           ({"kind": w.PRODUCT, "target": "p1", "condition": w.TARGET_PRICE, "threshold": "дёшево"}, "нужно число"),
                           ({"kind": w.PRODUCT, "target": "p1", "timezone": "Mars/Olympus"}, "часовой пояс"),
                           ({"kind": w.PRODUCT, "target": "p1", "cooldown_hours": 10000}, "от 0 до 720")):
            with self.subTest(data=data):
                with self.assertRaises(ValueError) as err:
                    w.normalize(data)
                self.assertIn(part, str(err.exception))

    def test_defaults_are_the_owner_decisions(self):
        watch = w.normalize({"kind": w.PRODUCT, "target": "p1"})
        self.assertEqual((watch["quiet_from"], watch["quiet_to"], watch["timezone"]), ("23:00", "08:00", "Asia/Almaty"))
        self.assertEqual(watch["mode"], w.INSTANT)
        self.assertTrue(watch["repeat"])

    def test_matching_by_kind_city_and_shops(self):
        self.assertTrue(w.matches(self.watch(), offer(1000)))
        self.assertFalse(w.matches(self.watch(target="p2"), offer(1000)))
        model = self.watch(kind=w.MODEL, target="apple iphone 15 128gb")
        self.assertTrue(w.matches(model, offer(1000)))
        self.assertFalse(w.matches(model, offer(1000, canonical_key="samsung s24")))
        category = self.watch(kind=w.CATEGORY, target="смартфоны")
        self.assertTrue(w.matches(category, offer(1000)))
        search = self.watch(kind=w.SEARCH, target="iphone 15")
        self.assertTrue(w.matches(search, offer(1000)))
        self.assertFalse(w.matches(search, offer(1000, title="Samsung Galaxy S24")))
        self.assertFalse(w.matches(self.watch(city="Алматы"), offer(1000)))
        self.assertFalse(w.matches(self.watch(shops=["Technodom"]), offer(1000)))
        self.assertTrue(w.matches(self.watch(shops=["Kaspi", "Technodom"]), offer(1000)))


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()
        database.upsert_telegram_user({"id": 701, "first_name": "A"})
        database.upsert_telegram_user({"id": 702, "first_name": "B"})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def add(self, user_id=701, **kw):
        data = {"kind": w.PRODUCT, "target": "p1", "condition": w.ANY_DROP, "title": "iPhone 15"}
        data.update(kw)
        return database.create_watch(user_id, data)

    def outbox(self):
        with database.get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM notification_outbox ORDER BY id")]

    def test_create_list_update_delete(self):
        created = self.add()
        self.assertEqual(created["kind"], w.PRODUCT)
        self.assertIn("iPhone 15", database.list_watches(701)[0]["description"])
        updated = database.update_watch(701, created["id"], {"condition": w.TARGET_PRICE, "threshold": 100000})
        self.assertEqual((updated["condition"], updated["threshold"]), (w.TARGET_PRICE, 100000))
        self.assertTrue(database.delete_watch(701, created["id"]))
        self.assertEqual(database.list_watches(701), [])

    def test_watches_are_private_to_their_owner(self):
        created = self.add(user_id=701)
        self.assertEqual(database.list_watches(702), [])
        self.assertIsNone(database.update_watch(702, created["id"], {"is_active": False}))
        self.assertFalse(database.delete_watch(702, created["id"]))
        self.assertEqual(len(database.list_watches(701)), 1)

    def test_duplicate_watch_is_not_created_twice(self):
        first, second = self.add(), self.add()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(database.list_watches(701)), 1)

    def test_limit_per_user(self):
        with patch.object(w, "MAX_WATCHES_PER_USER", 2):
            self.add(target="a")
            self.add(target="b")
            with self.assertRaisesRegex(ValueError, "удалите ненужные"):
                self.add(target="c")

    def test_trigger_creates_one_delivery_per_event(self):
        self.add()
        database.evaluate_watches([offer(100000)])          # первая цена — запоминаем, не уведомляем
        self.assertEqual(self.outbox(), [])
        self.assertEqual(database.evaluate_watches([offer(90000)]), 1)
        queue = self.outbox()
        self.assertEqual(len(queue), 1)
        payload = json.loads(queue[0]["payload"])
        self.assertEqual((payload["kind"], payload["price"]), ("watch", 90000))
        self.assertIn("снизилась", payload["reason"])
        # Повторный прогон того же состояния не создаёт вторую доставку
        self.assertEqual(database.evaluate_watches([offer(90000)]), 0)
        self.assertEqual(len(self.outbox()), 1)

    def test_price_bounce_does_not_notify_twice(self):
        self.add()
        database.evaluate_watches([offer(160000)])
        database.evaluate_watches([offer(149990)])
        database.evaluate_watches([offer(151000)])
        database.evaluate_watches([offer(149990)])
        self.assertEqual(len(self.outbox()), 1)

    def test_one_shot_watch_stops_after_it_fires(self):
        created = self.add(repeat=False)
        database.evaluate_watches([offer(100000)])
        database.evaluate_watches([offer(90000)])
        self.assertEqual(database.list_watches(701)[0]["is_active"], 0)
        database.evaluate_watches([offer(80000)])
        self.assertEqual(len(self.outbox()), 1)
        self.assertEqual(created["id"], database.list_watches(701)[0]["id"])

    def test_quiet_hours_delay_delivery_but_keep_the_event(self):
        self.add(quiet_from="23:00", quiet_to="08:00")
        night = datetime.datetime(2026, 9, 20, 19, 30, tzinfo=UTC)     # 00:30 в Астане
        database.evaluate_watches([offer(100000)], now=night)
        database.evaluate_watches([offer(90000)], now=night)
        queue = self.outbox()
        self.assertEqual(len(queue), 1)
        self.assertGreater(queue[0]["next_attempt_at"], night.timestamp())   # придёт после тишины
        events = database.watch_events(701)
        self.assertTrue(events[0]["status"].startswith("waiting:"))

    def test_cooldown_holds_the_next_message(self):
        self.add(cooldown_hours=6)
        start = datetime.datetime(2026, 9, 20, 7, 0, tzinfo=UTC)
        database.evaluate_watches([offer(100000)], now=start)
        database.evaluate_watches([offer(90000)], now=start)
        database.evaluate_watches([offer(80000)], now=start + datetime.timedelta(hours=1))
        queue = self.outbox()
        self.assertEqual(len(queue), 2)
        self.assertEqual(queue[0]["next_attempt_at"], 0)                     # первое — сразу
        self.assertGreater(queue[1]["next_attempt_at"], start.timestamp())   # второе — после паузы

    def test_inactive_watch_is_silent(self):
        created = self.add()
        database.evaluate_watches([offer(100000)])
        database.update_watch(701, created["id"], {"is_active": False})
        database.evaluate_watches([offer(50000)])
        self.assertEqual(self.outbox(), [])

    def test_deleting_watch_cancels_pending_delivery(self):
        created = self.add()
        database.evaluate_watches([offer(100000)])
        database.evaluate_watches([offer(90000)])
        database.delete_watch(701, created["id"])
        self.assertEqual([q["status"] for q in self.outbox()], ["cancelled"])
        self.assertEqual(database.watch_events(701), [])


if __name__ == "__main__":
    unittest.main()


class DeliveryTest(StorageTest):
    """Доставка: одно задание на срабатывание, 429, блокировка бота, перезапуск, чужие сообщения."""

    def setUp(self):
        super().setUp()
        database.save_or_update_products_batch([{"id": "p1", "title": "Смартфон Apple iPhone 15 128GB",
                                                 "price": 90000, "shop": "Kaspi", "city": "Астана",
                                                 "url": "https://k.example/p1", "category": "Смартфоны"}])
        database.save_user_settings(701, {"telegram_notify_enabled": True})

    def trigger(self):
        self.add()
        database.evaluate_watches([offer(100000)])
        database.evaluate_watches([offer(90000)])

    def deliver(self, result):
        import notifier
        with patch.object(notifier, "get_bot_token", lambda: "token"), \
             patch.object(notifier, "send_watch_message", return_value=result) as send:
            notifier.deliver_pending(limit=10)
        return send

    def test_message_explains_which_watch_fired(self):
        import notifier
        self.trigger()
        sent = {}
        with patch.object(notifier, "get_bot_token", lambda: "token"), \
             patch.object(notifier, "telegram_api",
                          side_effect=lambda method, payload, **kw: sent.update(payload) or _ok_response()):
            notifier.deliver_pending(limit=5)
        self.assertIn("Сработало ваше наблюдение", sent["text"])
        self.assertIn("iPhone 15", sent["text"])
        self.assertIn("снизилась", sent["text"])
        self.assertEqual(sent["chat_id"], 701)

    def test_one_trigger_is_delivered_once_even_after_restart(self):
        import notifier
        self.trigger()
        send = self.deliver(notifier.DeliveryResult("sent"))
        self.assertEqual(send.call_count, 1)
        # «Перезапуск»: очередь перечитывается заново, повторной отправки быть не должно
        send = self.deliver(notifier.DeliveryResult("sent"))
        self.assertEqual(send.call_count, 0)
        self.assertEqual([q["status"] for q in self.outbox()], ["sent"])
        self.assertEqual(database.watch_events(701)[0]["status"], "sent")

    def test_rate_limit_pauses_the_whole_bot_and_keeps_the_task(self):
        import notifier
        self.trigger()
        self.deliver(notifier.DeliveryResult("retry", retry_after=120, error="Telegram 429"))
        self.assertGreater(notifier.telegram_paused_for(), 0)
        self.assertEqual([q["status"] for q in self.outbox()], ["pending"])
        # Пока пауза не кончилась, ничего не отправляется
        send = self.deliver(notifier.DeliveryResult("sent"))
        self.assertEqual(send.call_count, 0)

    def test_blocked_bot_is_a_permanent_failure(self):
        import notifier
        self.trigger()
        self.deliver(notifier.DeliveryResult("permanent", error="Telegram 403"))
        self.assertEqual([q["status"] for q in self.outbox()], ["failed"])

    def test_message_is_cancelled_when_price_moved_or_watch_is_off(self):
        import notifier
        self.trigger()
        database.save_or_update_products_batch([{"id": "p1", "title": "Смартфон Apple iPhone 15 128GB",
                                                 "price": 95000, "shop": "Kaspi", "city": "Астана",
                                                 "url": "https://k.example/p1", "category": "Смартфоны"}])
        send = self.deliver(notifier.DeliveryResult("sent"))
        self.assertEqual(send.call_count, 0)
        self.assertEqual([q["status"] for q in self.outbox()], ["cancelled"])

    def test_telegram_off_means_no_message(self):
        import notifier
        database.save_user_settings(701, {"telegram_notify_enabled": False})
        self.trigger()
        send = self.deliver(notifier.DeliveryResult("sent"))
        self.assertEqual(send.call_count, 0)
        self.assertEqual([q["status"] for q in self.outbox()], ["cancelled"])

    def test_user_data_export_and_delete_include_watches(self):
        self.trigger()
        data = database.export_user_data(701)
        self.assertEqual(len(data["watches"]), 1)
        self.assertTrue(data["watch_events"])
        counts = database.delete_user_account(701)
        self.assertEqual(counts["watches"], 1)
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM watch_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM watch_state").fetchone()[0], 0)


def _ok_response():
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}
    return R()


class ApiTest(unittest.IsolatedAsyncioTestCase):
    """Права: наблюдения создаёт и видит только их владелец; гость не может ничего."""

    async def test_permissions_and_isolation(self):
        import auth
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                   patch("config.DATA_DIR", type(DB_PATH)(tmp.name))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        for uid in (9101, 9102):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (9101, 9102)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            def login(uid):
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[uid]})

            self.assertEqual((await client.get("/api/me/watches")).status, 401)
            login(9101)
            res = await client.post("/api/me/watches", json={"kind": "product", "target": "p1",
                                                             "condition": "target_price", "threshold": 100000,
                                                             "title": "iPhone 15"})
            self.assertEqual(res.status, 200)
            watch_id = (await res.json())["watch"]["id"]
            bad = await client.post("/api/me/watches", json={"kind": "product", "target": "p2",
                                                             "condition": "drop_pct", "threshold": 500})
            self.assertEqual(bad.status, 400)
            self.assertIn("от 1 до 99", (await bad.json())["message"])

            login(9102)
            res = await client.get("/api/me/watches")
            self.assertEqual(res.status, 200)
            mine = await res.json()
            self.assertEqual(mine["watches"], [])                                  # чужое не видно
            self.assertEqual((await client.post(f"/api/me/watches/{watch_id}", json={"is_active": False})).status, 404)
            self.assertEqual((await client.delete(f"/api/me/watches/{watch_id}")).status, 404)

            login(9101)
            still = await (await client.get("/api/me/watches")).json()
            self.assertEqual(len(still["watches"]), 1)
            self.assertEqual((await client.delete(f"/api/me/watches/{watch_id}")).status, 200)
