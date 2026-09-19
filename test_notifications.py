"""M13: доставка уведомлений — скрытые алерты, ответы Telegram, дубли. Без сети и без рабочей БД."""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

import notifier  # noqa: E402
from database import (get_connection, init_db, record_alert, save_or_update_product,  # noqa: E402
                      save_user_settings, upsert_telegram_user, dismiss_alert)
from notifier import DeliveryResult, classify_telegram_response  # noqa: E402

ANOMALY = {"type": "SUPER_DISCOUNT", "old_price": 300000, "new_price": 150000, "drop_pct": 50,
           "savings": 150000, "emoji": "Sale", "reason": "Test"}


def _response(status, json_body=None, headers=None):
    return Mock(status_code=status, json=Mock(return_value=json_body or {}), headers=headers or {})


class ClassifyTest(unittest.TestCase):
    def test_statuses(self):
        self.assertTrue(classify_telegram_response(_response(200)))
        retry = classify_telegram_response(_response(429, {"parameters": {"retry_after": 7}}))
        self.assertEqual((retry.status, retry.retry_after), ("retry", 7.0))
        self.assertEqual(classify_telegram_response(_response(429, headers={"Retry-After": "3"})).retry_after, 3.0)
        for code in (400, 403, 404):
            self.assertEqual(classify_telegram_response(_response(code)).status, "permanent")
        self.assertEqual(classify_telegram_response(_response(502)).status, "retry")
        bad_json = Mock(status_code=429, json=Mock(side_effect=ValueError), headers={})
        self.assertEqual(classify_telegram_response(bad_json).status, "retry")


class SendTest(unittest.TestCase):
    PRODUCT = {"id": "p@astana", "shop": "Sulpak", "title": "Телевизор", "url": "https://www.sulpak.kz/g/x",
               "image_url": "https://img.example/x.jpg", "category": "ТВ", "city": "Астана"}

    def _send(self, *responses):
        with patch.object(notifier, "telegram_api", side_effect=list(responses)) as api:
            result = notifier.send_telegram_alert(1, self.PRODUCT, ANOMALY)
        return result, [c.args[0] for c in api.call_args_list]

    def test_photo_rejected_falls_back_to_text(self):
        result, calls = self._send(_response(400), _response(200))
        self.assertTrue(result)
        self.assertEqual(calls, ["sendPhoto", "sendMessage"])

    def test_rate_limit_does_not_send_text_after_photo(self):
        result, calls = self._send(_response(429, {"parameters": {"retry_after": 30}}))
        self.assertEqual((result.status, result.retry_after), ("retry", 30.0))
        self.assertEqual(calls, ["sendPhoto"])

    def test_blocked_bot_is_permanent_without_fallback(self):
        result, calls = self._send(_response(403))
        self.assertEqual(result.status, "permanent")
        self.assertEqual(calls, ["sendPhoto"])

    def test_network_error_is_retry(self):
        with patch.object(notifier, "telegram_api", side_effect=ConnectionError("down")):
            self.assertEqual(notifier.send_telegram_alert(1, self.PRODUCT, ANOMALY).status, "retry")


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            for table in ("notification_outbox", "alerts", "products"):
                conn.execute(f"DELETE FROM {table}")
            conn.commit()
        upsert_telegram_user({"id": 42, "first_name": "Audit"})
        save_user_settings(42, {"telegram_notify_enabled": True, "price_glitch_drop_pct": 30})
        # Тихий режим после пересборки каталога в этих тестах не нужен
        patcher = patch.object(notifier, "get_bot_token", return_value="test")
        patcher.start()
        self.addCleanup(patcher.stop)

    def enqueue(self, pid="n1@astana"):
        product = {"id": pid, "shop": "Sulpak", "title": "Телевизор Samsung QE55", "url": "https://www.sulpak.kz/g/x",
                   "price": 150000, "city": "Астана", "category": "ТВ"}
        save_or_update_product(product)
        deliveries = notifier.prepare_deliveries(product, ANOMALY)
        self.assertTrue(deliveries)
        return record_alert(pid, "SUPER_DISCOUNT", 300000, 150000, 50, 150000, deliveries=deliveries)

    def outbox(self):
        with get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM notification_outbox ORDER BY id")]

    def test_dismissed_alert_is_not_sent(self):
        alert_id = self.enqueue()
        with get_connection() as conn:
            conn.execute("UPDATE alerts SET is_dismissed = 1 WHERE id = ?", (alert_id,))
            conn.commit()
        with patch.object(notifier, "send_telegram_alert") as send:
            notifier.deliver_pending()
        send.assert_not_called()
        self.assertEqual(self.outbox()[0]["status"], "cancelled")

    def test_dismiss_cancels_pending_deliveries(self):
        alert_id = self.enqueue()
        dismiss_alert(alert_id)
        self.assertEqual(self.outbox()[0]["status"], "cancelled")

    def test_deleted_alert_is_not_sent(self):
        alert_id = self.enqueue()
        with get_connection() as conn:
            conn.execute("DELETE FROM alerts WHERE id = ?", (alert_id,))
            conn.commit()
        with patch.object(notifier, "send_telegram_alert") as send:
            notifier.deliver_pending()
        send.assert_not_called()

    def test_permanent_failure_is_not_retried(self):
        self.enqueue()
        with patch.object(notifier, "send_telegram_alert", return_value=DeliveryResult("permanent", None, "Telegram 403")):
            notifier.deliver_pending()
        row = self.outbox()[0]
        self.assertEqual((row["status"], row["last_error"]), ("failed", "Telegram 403"))

    def test_rate_limit_waits_retry_after_and_stops_batch(self):
        self.enqueue("n1@astana")
        self.enqueue("n2@astana")
        before = time.time()
        with patch.object(notifier, "send_telegram_alert", return_value=DeliveryResult("retry", 40, "Telegram 429")) as send:
            self.assertEqual(notifier.deliver_pending(), 0)
        self.assertEqual(send.call_count, 1)  # после 429 остальные ждут следующего цикла
        first, second = self.outbox()
        self.assertEqual(first["status"], "pending")
        self.assertGreaterEqual(first["next_attempt_at"], before + 39)
        self.assertEqual(second["attempts"], 0)

    def test_success_still_counts(self):
        self.enqueue()
        with patch.object(notifier, "send_telegram_alert", return_value=DeliveryResult("sent")):
            self.assertEqual(notifier.deliver_pending(), 1)
        self.assertEqual(self.outbox()[0]["status"], "sent")


class DedupTest(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM alerts")
            conn.commit()

    def test_same_event_recorded_once(self):
        self.assertTrue(record_alert("d1@astana", "SUPER_DISCOUNT", 300000, 150000, 50, 150000))
        self.assertEqual(record_alert("d1@astana", "SUPER_DISCOUNT", 300000, 150000, 50, 150000), 0)
        self.assertTrue(record_alert("d1@astana", "SUPER_DISCOUNT", 300000, 140000, 53, 160000))  # новая цена — новое событие

    def test_concurrent_writers_create_one_alert(self):
        results = []

        def write():
            results.append(record_alert("race@astana", "SUPER_DISCOUNT", 300000, 150000, 50, 150000))

        threads = [threading.Thread(target=write) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with get_connection() as conn:
            count = conn.execute("SELECT count(*) FROM alerts WHERE product_id = 'race@astana'").fetchone()[0]
        self.assertEqual(count, 1)
        self.assertEqual(sum(1 for r in results if r), 1)


if __name__ == "__main__":
    unittest.main()
