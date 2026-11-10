"""TG-04: учёт блокировки бота пользователем; кнопки на сайт как Mini App. Без сети и без рабочей БД."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import os
import unittest
from unittest.mock import AsyncMock, patch

for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL",
            "ADMIN_TELEGRAM_IDS", "TELEGRAM_BLOCK_TRACKING", "TELEGRAM_SITE_BUTTONS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

import auth  # noqa: E402
import notifier  # noqa: E402
import telegram_bot as bot  # noqa: E402
from database import (get_connection, get_notification_recipients, get_user, init_db, record_alert,  # noqa: E402
                      save_or_update_product, save_user_settings, set_bot_blocked, upsert_telegram_user,
                      export_user_data)
from notifier import DeliveryResult  # noqa: E402

USER = 4242
ANOMALY = {"type": "SUPER_DISCOUNT", "old_price": 300000, "new_price": 150000, "drop_pct": 50,
           "savings": 150000, "emoji": "Sale", "reason": "Test"}


def _membership(status, user_id=USER, chat_id=USER, kind="private"):
    return {"my_chat_member": {"chat": {"id": chat_id, "type": kind}, "from": {"id": user_id},
                               "new_chat_member": {"status": status}}}


class BlockTrackingBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            for table in ("notification_outbox", "alerts", "products"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM schema_metadata WHERE name = ?", (notifier.TELEGRAM_PAUSE_KEY,))
            conn.execute("UPDATE users SET bot_blocked_at = NULL")
            conn.commit()
        upsert_telegram_user({"id": USER, "first_name": "Block"})
        save_user_settings(USER, {"telegram_notify_enabled": True, "price_glitch_drop_pct": 30})
        self.addCleanup(self._remove_user)
        for target in ((notifier, "get_bot_token"),):
            patcher = patch.object(*target, return_value="test")
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _remove_user():
        # База тестового прогона общая для модулей: лишний получатель менял бы чужие проверки доставки
        with get_connection() as conn:
            conn.execute("DELETE FROM notification_outbox WHERE user_id = ?", (USER,))
            conn.execute("DELETE FROM users WHERE id = ?", (USER,))
            conn.commit()

    def enqueue(self, pid="b1@astana"):
        product = {"id": pid, "shop": "Sulpak", "title": "Телевизор Samsung QE55", "url": "https://www.sulpak.kz/g/x",
                   "price": 150000, "city": "Астана", "category": "ТВ"}
        save_or_update_product(product)
        deliveries = [d for d in notifier.prepare_deliveries(product, ANOMALY)]
        return record_alert(pid, "SUPER_DISCOUNT", 300000, 150000, 50, 150000, deliveries=deliveries), deliveries

    def outbox(self, user_id=USER):
        with get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM notification_outbox WHERE user_id = ? ORDER BY id", (user_id,))]


class StorageTests(BlockTrackingBase):
    async def test_mark_is_idempotent_and_keeps_first_block_time(self):
        self.assertFalse(get_user(USER)["bot_blocked"])
        self.assertTrue(set_bot_blocked(USER, True))
        first = get_user(USER)["bot_blocked_at"]
        self.assertTrue(first)
        self.assertFalse(set_bot_blocked(USER, True))
        self.assertEqual(get_user(USER)["bot_blocked_at"], first)
        self.assertTrue(set_bot_blocked(USER, False))
        self.assertFalse(set_bot_blocked(USER, False))
        self.assertFalse(get_user(USER)["bot_blocked"])

    async def test_unknown_user_is_not_created(self):
        self.assertFalse(set_bot_blocked(999_001, True))
        self.assertIsNone(get_user(999_001))

    async def test_bot_block_is_separate_from_admin_block_and_is_exported(self):
        set_bot_blocked(USER, True)
        user = get_user(USER)
        self.assertFalse(user["is_blocked"])                    # вход на сайт остаётся
        self.assertTrue(export_user_data(USER)["profile"]["bot_blocked_at"])


class DeliveryTests(BlockTrackingBase):
    async def test_blocked_user_gets_no_new_deliveries_and_queued_ones_are_cancelled(self):
        self.enqueue("b1@astana")
        set_bot_blocked(USER, True)
        self.assertNotIn(USER, [u["id"] for u in get_notification_recipients()])
        _, deliveries = self.enqueue("b2@astana")
        self.assertNotIn(USER, [d["user_id"] if isinstance(d, dict) else d[0] for d in deliveries])
        with patch.object(notifier, "send_telegram_alert") as send:
            notifier.deliver_pending()
        send.assert_not_called()                                # к Telegram не обращаемся
        self.assertEqual([row["status"] for row in self.outbox()], ["cancelled"])

    async def test_403_marks_user_and_next_delivery_is_not_attempted(self):
        self.enqueue("b1@astana")
        self.enqueue("b2@astana")
        with patch.object(notifier, "send_telegram_alert",
                          return_value=DeliveryResult("permanent", None, "Telegram 403")) as send:
            notifier.deliver_pending()
        self.assertEqual(send.call_count, 1)
        self.assertTrue(get_user(USER)["bot_blocked"])
        self.assertEqual([row["status"] for row in self.outbox()], ["failed", "cancelled"])

    async def test_other_permanent_errors_do_not_mark_user(self):
        self.enqueue()
        with patch.object(notifier, "send_telegram_alert",
                          return_value=DeliveryResult("permanent", None, "Telegram 400")):
            notifier.deliver_pending()
        self.assertFalse(get_user(USER)["bot_blocked"])

    async def test_switch_off_restores_previous_behaviour(self):
        set_bot_blocked(USER, True)
        with patch.dict(os.environ, {"TELEGRAM_BLOCK_TRACKING": "off"}):
            self.assertIn(USER, [u["id"] for u in get_notification_recipients()])
            self.enqueue()
            with patch.object(notifier, "send_telegram_alert",
                              return_value=DeliveryResult("permanent", None, "Telegram 403")) as send:
                notifier.deliver_pending()
            send.assert_called_once()
            await bot.process_telegram_update(None, "fake", _membership("member"))
        self.assertTrue(get_user(USER)["bot_blocked"])          # выключенный учёт отметку не трогает


class BotUpdateTests(BlockTrackingBase):
    async def test_kicked_marks_and_member_clears(self):
        await bot.process_telegram_update(None, "fake", _membership("kicked"))
        self.assertTrue(get_user(USER)["bot_blocked"])
        await bot.process_telegram_update(None, "fake", _membership("member"))
        self.assertFalse(get_user(USER)["bot_blocked"])

    async def test_group_and_foreign_updates_are_ignored(self):
        for update in (_membership("kicked", kind="group", chat_id=-5),
                       _membership("kicked", chat_id=USER + 1),
                       _membership("kicked", user_id=True, chat_id=True)):
            await bot.process_telegram_update(None, "fake", update)
        self.assertFalse(get_user(USER)["bot_blocked"])

    async def test_message_from_marked_user_clears_mark(self):
        set_bot_blocked(USER, True)
        message = {"message": {"chat": {"id": USER, "type": "private"}, "from": {"id": USER}, "text": "/status"}}
        with patch.object(bot, "_message_limiter", auth.RateLimiter(10, 60)), \
                patch.object(bot, "handle_status_command", new_callable=AsyncMock) as status:
            await bot.process_telegram_update(None, "fake", message)
        status.assert_awaited_once()
        self.assertFalse(get_user(USER)["bot_blocked"])

    async def test_dispatcher_takes_chat_from_membership_update(self):
        seen = []

        async def handle(session, token, update):
            seen.append(update["my_chat_member"]["chat"]["id"])
        with patch.object(bot, "process_telegram_update", side_effect=handle):
            dispatcher = bot.TelegramDispatcher(None, "fake", workers=1, capacity=2)
            try:
                await dispatcher.submit(_membership("kicked"))
                await dispatcher.queue.join()
                self.assertEqual(seen, [USER])
                self.assertEqual(dispatcher.chat_locks, {})
            finally:
                await dispatcher.close()


class SiteButtonTests(unittest.TestCase):
    def test_https_site_link_opens_as_mini_app(self):
        self.assertEqual(notifier.site_button("Сайт", "https://shop.example.kz/deals"),
                         {"text": "Сайт", "web_app": {"url": "https://shop.example.kz/deals"}})

    def test_plain_http_and_switch_keep_ordinary_link(self):
        self.assertEqual(notifier.site_button("Сайт", "http://192.168.1.5:8080"),
                         {"text": "Сайт", "url": "http://192.168.1.5:8080"})
        with patch.dict(os.environ, {"TELEGRAM_SITE_BUTTONS": "url"}):
            self.assertEqual(notifier.site_button("Сайт", "https://shop.example.kz"),
                             {"text": "Сайт", "url": "https://shop.example.kz"})

    def test_search_link_encodes_query(self):
        with patch.object(bot, "APP_URL", "https://shop.example.kz"):
            self.assertEqual(bot._site_search_url("айфон 15 & чехол"),
                             "https://shop.example.kz?tab=best-price&q=%D0%B0%D0%B9%D1%84%D0%BE%D0%BD%2015%20%26%20"
                             "%D1%87%D0%B5%D1%85%D0%BE%D0%BB")

    def test_shop_links_stay_ordinary(self):
        product = {"shop": "Sulpak", "title": "T", "url": "https://www.sulpak.kz/g/x"}
        calls = []
        with patch.object(notifier, "APP_URL", "https://shop.example.kz"), \
                patch.object(notifier, "telegram_api", side_effect=lambda m, p, **k: calls.append(p) or
                             unittest.mock.Mock(status_code=200)):
            notifier.send_telegram_alert(1, product, ANOMALY)
        rows = calls[0]["reply_markup"]["inline_keyboard"]
        self.assertEqual(rows[0][0]["url"], "https://www.sulpak.kz/g/x")
        self.assertEqual(rows[1][0]["web_app"], {"url": "https://shop.example.kz"})


if __name__ == "__main__":
    unittest.main()
