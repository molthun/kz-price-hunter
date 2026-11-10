"""TG-06: устаревшая находка правится в уже отправленном сообщении. Без сети и без рабочей БД."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL",
            "ADMIN_TELEGRAM_IDS", "TELEGRAM_STALE_MARK", "TELEGRAM_BLOCK_TRACKING", "TELEGRAM_SITE_BUTTONS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

import notifier  # noqa: E402
from database import (get_connection, get_user, init_db, record_alert, save_or_update_product,  # noqa: E402
                      save_user_settings, set_bot_blocked, upsert_telegram_user, stale_alert_candidates,
                      STALE_WATCH_HOURS)
from notifier import DeliveryResult, classify_telegram_response  # noqa: E402

USER = 6006
ANOMALY = {"type": "SUPER_DISCOUNT", "old_price": 300000, "new_price": 150000, "drop_pct": 50,
           "savings": 150000, "emoji": "Sale", "reason": "Test"}


def _response(status, body=None, headers=None):
    return Mock(status_code=status, json=Mock(return_value=body or {}), headers=headers or {})


class ResultTests(unittest.TestCase):
    def test_sent_result_carries_message_id_only_when_telegram_returned_one(self):
        self.assertEqual(classify_telegram_response(_response(200, {"ok": True, "result": {"message_id": 77}})).message_id, 77)
        for body in ({}, {"result": True}, {"result": {"message_id": "77"}}, ["unexpected"]):
            result = classify_telegram_response(_response(200, body))
            self.assertTrue(result)
            self.assertIsNone(result.message_id)

    def test_photo_alert_is_remembered_as_photo_and_text_fallback_as_text(self):
        product = {"shop": "Sulpak", "title": "T", "url": "https://www.sulpak.kz/g/x",
                   "image_url": "https://www.sulpak.kz/i.jpg"}
        ok = _response(200, {"result": {"message_id": 5}})
        with patch.object(notifier, "is_safe_image_url", return_value=True), \
                patch.object(notifier, "telegram_api", return_value=ok):
            result = notifier.send_telegram_alert(1, product, ANOMALY)
        self.assertEqual((result.message_id, result.has_photo), (5, True))
        with patch.object(notifier, "is_safe_image_url", return_value=True), \
                patch.object(notifier, "telegram_api", side_effect=[_response(400), ok]):
            result = notifier.send_telegram_alert(1, product, ANOMALY)
        self.assertEqual((result.message_id, result.has_photo), (5, False))


class StaleBase(unittest.TestCase):
    PID = "s1@astana"

    def setUp(self):
        init_db()
        with get_connection() as conn:
            for table in ("notification_outbox", "alerts", "products"):
                conn.execute(f"DELETE FROM {table}")
            conn.execute("DELETE FROM schema_metadata WHERE name = ?", (notifier.TELEGRAM_PAUSE_KEY,))
            conn.commit()
        # База прогона общая для модулей: чужие получатели не должны получать находки этого теста
        with get_connection() as conn:
            conn.execute("UPDATE users SET bot_blocked_at = 'test-stale' WHERE bot_blocked_at IS NULL AND id != ?", (USER,))
            conn.commit()
        upsert_telegram_user({"id": USER, "first_name": "Stale"})
        save_user_settings(USER, {"telegram_notify_enabled": True, "price_glitch_drop_pct": 30})
        self.addCleanup(self._remove_user)
        for patcher in (patch.object(notifier, "get_bot_token", return_value="test"),
                        patch.dict(os.environ, {"TELEGRAM_STALE_MARK": "on"})):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _remove_user():
        with get_connection() as conn:
            conn.execute("DELETE FROM notification_outbox WHERE user_id = ?", (USER,))
            conn.execute("DELETE FROM users WHERE id = ?", (USER,))
            conn.execute("UPDATE users SET bot_blocked_at = NULL WHERE bot_blocked_at = 'test-stale'")
            conn.commit()

    def deliver(self, message_id=501, has_photo=False, pid=None):
        """Ставит находку в очередь и доставляет её «в Telegram» с данным номером сообщения."""
        pid = pid or self.PID
        product = {"id": pid, "shop": "Sulpak", "title": "Телевизор <Samsung> QE55", "url": "https://www.sulpak.kz/g/x",
                   "price": 150000, "city": "Астана", "category": "ТВ"}
        save_or_update_product(product)
        record_alert(pid, "SUPER_DISCOUNT", 300000, 150000, 50, 150000,
                     deliveries=notifier.prepare_deliveries(product, ANOMALY))
        with patch.object(notifier, "send_telegram_alert",
                          return_value=DeliveryResult("sent", message_id=message_id, has_photo=has_photo)):
            self.assertEqual(notifier.deliver_pending(), 1)

    def row(self):
        with get_connection() as conn:
            return dict(conn.execute("SELECT * FROM notification_outbox WHERE user_id = ? ORDER BY id DESC",
                                     (USER,)).fetchone())

    def set_product(self, pid=None, **values):
        assignments = ", ".join(f"{column} = ?" for column in values)
        with get_connection() as conn:
            conn.execute(f"UPDATE products SET {assignments} WHERE id = ?", (*values.values(), pid or self.PID))
            conn.commit()

    def mark(self, response=None):
        calls = []

        def api(method, payload, **kwargs):
            calls.append((method, payload))
            return response or _response(200, {"result": {"message_id": 501}})
        with patch.object(notifier, "telegram_api", side_effect=api):
            edited = notifier.mark_stale_alerts(force=True)
        return edited, calls


class StaleMarkTests(StaleBase):
    def test_delivery_remembers_message_and_valid_deal_is_left_alone(self):
        self.deliver(message_id=501)
        row = self.row()
        self.assertEqual((row["tg_message_id"], row["tg_has_photo"]), (501, 0))
        self.assertIsNotNone(row["sent_at"])
        edited, calls = self.mark()
        self.assertEqual((edited, calls), (0, []))
        self.assertIsNone(self.row()["stale_checked_at"])          # ещё в силе — проверим позже

    def test_cheaper_price_is_still_a_deal(self):
        self.deliver()
        self.set_product(current_price=140000)
        self.assertEqual(self.mark()[0], 0)

    def test_price_back_up_edits_text_message_once(self):
        self.deliver(message_id=501)
        self.set_product(current_price=289990)
        edited, calls = self.mark()
        self.assertEqual(edited, 1)
        method, payload = calls[0]
        self.assertEqual(method, "editMessageText")
        self.assertEqual((payload["chat_id"], payload["message_id"]), (USER, 501))
        self.assertIn("уже неактуальна", payload["text"])
        self.assertIn("<s>150 000 ₸</s>", payload["text"])
        self.assertIn("289 990 ₸", payload["text"])
        self.assertIn("&lt;Samsung&gt;", payload["text"])          # название магазина экранировано
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["url"], "https://www.sulpak.kz/g/x")
        self.assertIsNotNone(self.row()["stale_checked_at"])
        self.assertEqual(self.mark(), (0, []))                      # второй раз не правим

    def test_photo_alert_edits_caption_within_limit(self):
        self.deliver(message_id=502, has_photo=True)
        self.set_product(current_price=200000, title="Я" * 2000)
        _, calls = self.mark()
        method, payload = calls[0]
        self.assertEqual(method, "editMessageCaption")
        self.assertLessEqual(len(payload["caption"]), 1024)
        self.assertNotIn("text", payload)

    def test_removed_product_is_reported_as_gone(self):
        self.deliver()
        self.set_product(is_active=0)
        _, calls = self.mark()
        self.assertIn("нет в продаже", calls[0][1]["text"])

    def test_outdated_scan_does_not_make_deal_false(self):
        self.deliver()
        self.set_product(current_price=289990, updated_at="2020-01-01 00:00:00")
        self.assertEqual(self.mark(), (0, []))
        self.assertIsNone(self.row()["stale_checked_at"])

    def test_switch_off_and_telegram_pause_send_nothing(self):
        self.deliver()
        self.set_product(current_price=289990)
        with patch.dict(os.environ, {"TELEGRAM_STALE_MARK": ""}):
            self.assertEqual(self.mark(), (0, []))
        notifier.pause_telegram(60)
        self.assertEqual(self.mark(), (0, []))

    def test_deleted_message_is_not_retried_and_blocked_bot_is_noted(self):
        self.deliver()
        self.set_product(current_price=289990)
        edited, calls = self.mark(_response(400))
        self.assertEqual((edited, len(calls)), (0, 1))
        self.assertIsNotNone(self.row()["stale_checked_at"])
        self.assertFalse(get_user(USER)["bot_blocked"])

        self.deliver(message_id=503, pid="s2@astana")
        self.set_product(pid="s2@astana", current_price=289990)
        self.mark(_response(403))
        self.assertTrue(get_user(USER)["bot_blocked"])

    def test_user_who_blocked_bot_is_skipped_without_request(self):
        self.deliver()
        self.set_product(current_price=289990)
        set_bot_blocked(USER, True)
        self.assertEqual(self.mark(), (0, []))
        self.assertIsNotNone(self.row()["stale_checked_at"])

    def test_rate_limit_pauses_bot_and_keeps_deal_for_next_pass(self):
        self.deliver()
        self.set_product(current_price=289990)
        edited, calls = self.mark(_response(429, {"parameters": {"retry_after": 30}}))
        self.assertEqual((edited, len(calls)), (0, 1))
        self.assertIsNone(self.row()["stale_checked_at"])
        self.assertGreater(notifier.telegram_paused_for(), 0)

    def test_old_deals_fall_out_of_watch_window(self):
        self.deliver()
        self.set_product(current_price=289990)
        with get_connection() as conn:
            conn.execute("UPDATE notification_outbox SET sent_at = ? WHERE user_id = ?",
                         (time.time() - (STALE_WATCH_HOURS + 1) * 3600, USER))
            conn.commit()
        self.assertEqual(stale_alert_candidates(), [])
        self.assertEqual(self.mark(), (0, []))

    def test_valid_deals_do_not_hide_stale_ones_from_the_pass(self):
        for n in range(25):                                  # больше, чем берёт один проход
            self.deliver(message_id=700 + n, pid=f"v{n}@astana")
        self.deliver(message_id=799, pid="late@astana")
        self.set_product(pid="late@astana", current_price=289990)
        edited, calls = self.mark()
        self.assertEqual(edited, 1)
        self.assertEqual(calls[0][1]["message_id"], 799)

    def test_pass_is_throttled_and_limited(self):
        for n in range(notifier.STALE_EDITS_PER_PASS + 2):
            pid = f"m{n}@astana"
            self.deliver(message_id=600 + n, pid=pid)
            self.set_product(pid=pid, current_price=289990)
        edited, calls = self.mark()
        self.assertEqual((edited, len(calls)), (notifier.STALE_EDITS_PER_PASS, notifier.STALE_EDITS_PER_PASS))
        with patch.object(notifier, "telegram_api") as api:        # обычный проход сразу после — по таймеру рано
            self.assertEqual(notifier.mark_stale_alerts(), 0)
        api.assert_not_called()


class VerdictTests(unittest.TestCase):
    FACTS = {"product_found": True, "product_active": True, "product_fresh": True, "current_price": 200}

    def test_watch_message_uses_its_own_price_and_digests_are_never_marked(self):
        self.assertEqual(notifier.stale_verdict(self.FACTS, {"kind": "watch", "price": 150}), "price")
        self.assertIsNone(notifier.stale_verdict(self.FACTS, {"kind": "watch", "price": 200}))
        self.assertIsNone(notifier.stale_verdict(self.FACTS, {"kind": "daily_digest"}))
        self.assertIsNone(notifier.stale_verdict(self.FACTS, {"kind": "watch", "price": 0}))

    def test_unknown_product_is_left_alone(self):
        facts = dict(self.FACTS, product_found=False, product_active=False)
        self.assertIsNone(notifier.stale_verdict(facts, {"anomaly": {"new_price": 150}}))


if __name__ == "__main__":
    unittest.main()
