"""Тесты LinkPreviewOptions (TG-16), Mini App 8.0 (TG-17) и AI-фидбэка (TG-20)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock

import database
import notifier
from notifier import (
    make_link_preview_options,
    send_telegram_alert,
    send_watch_message,
    send_watch_digest,
    send_daily_digest,
    save_prepared_inline_message,
)
import telegram_bot
from telegram_bot import (
    process_telegram_update,
    handle_ai_consultant_message,
    send_tg_message,
)


class LinkPreviewOptionsTests(unittest.TestCase):
    def test_make_link_preview_options_disabled(self):
        opts = make_link_preview_options(is_disabled=True)
        self.assertEqual(opts, {"is_disabled": True})

    def test_make_link_preview_options_compact_and_above(self):
        opts = make_link_preview_options(
            url="https://kaspi.kz/p/123",
            prefer_small_media=True,
            show_above_text=True,
        )
        self.assertEqual(opts, {
            "url": "https://kaspi.kz/p/123",
            "prefer_small_media": True,
            "show_above_text": True,
        })

    def test_make_link_preview_options_empty_defaults(self):
        opts = make_link_preview_options()
        self.assertEqual(opts, {})

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_alert_link_preview_options(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 100}}
        mock_post.return_value = resp

        product = {"id": "p1", "title": "Ноутбук", "current_price": 200000, "shop": "Kaspi", "url": "https://kaspi.kz/laptop"}
        anomaly = {"emoji": "🔥", "old_price": 250000, "new_price": 200000, "drop_pct": 20, "savings": 50000, "reason": "Скидка"}

        send_telegram_alert(12345, product, anomaly)
        args, kwargs = mock_post.call_args
        payload = kwargs["json"]
        self.assertIn("link_preview_options", payload)
        self.assertEqual(payload["link_preview_options"].get("prefer_small_media"), True)
        self.assertEqual(payload["link_preview_options"].get("show_above_text"), True)
        self.assertEqual(payload["link_preview_options"].get("url"), "https://kaspi.kz/laptop")

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_watch_message_link_preview_options(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 101}}
        mock_post.return_value = resp

        payload = {
            "watch_id": 5,
            "product": {"title": "Кофемашина", "shop": "Technodom", "url": "https://technodom.kz/coffee"},
            "price": 120000,
            "reason": "Цена снизилась"
        }
        send_watch_message(12345, payload)
        args, kwargs = mock_post.call_args
        sent = kwargs["json"]
        self.assertIn("link_preview_options", sent)
        self.assertEqual(sent["link_preview_options"].get("prefer_small_media"), True)
        self.assertEqual(sent["link_preview_options"].get("url"), "https://technodom.kz/coffee")

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_digests_link_preview_disabled(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 102}}
        mock_post.return_value = resp

        send_watch_digest(12345, [{"product": {"title": "A", "shop": "S"}, "price": 1000}])
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["link_preview_options"], {"is_disabled": True})

        send_daily_digest(12345, {"lines": ["Line 1", "Line 2"]})
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["link_preview_options"], {"is_disabled": True})


class AiFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Гарантируем наличие таблицы и очищаем перед тестом
        database.save_ai_feedback(0, "init")
        with database.get_connection() as conn:
            conn.execute("DELETE FROM ai_feedback")
            conn.commit()

    def test_save_and_get_ai_feedback(self):
        database.save_ai_feedback(12345, "good")
        database.save_ai_feedback(12345, "good")
        database.save_ai_feedback(67890, "bad")

        stats = database.get_ai_feedback_stats()
        self.assertEqual(stats.get("good"), 2)
        self.assertEqual(stats.get("bad"), 1)

    async def test_process_telegram_update_aifb_good(self):
        session = AsyncMock()
        token = "test_token"

        update = {
            "callback_query": {
                "id": "cq_100",
                "from": {"id": 99901, "is_bot": False},
                "message": {"chat": {"id": 99901, "type": "private"}},
                "data": "aifb:good"
            }
        }

        with patch("telegram_bot.answer_callback_query", new_callable=AsyncMock) as mock_answer:
            await process_telegram_update(session, token, update)
            mock_answer.assert_called_once_with(session, token, "cq_100", text="👍 Спасибо! Рад помочь.")

        stats = database.get_ai_feedback_stats()
        self.assertEqual(stats.get("good"), 1)

    async def test_process_telegram_update_aifb_bad(self):
        session = AsyncMock()
        token = "test_token"

        update = {
            "callback_query": {
                "id": "cq_101",
                "from": {"id": 99902, "is_bot": False},
                "message": {"chat": {"id": 99902, "type": "private"}},
                "data": "aifb:bad"
            }
        }

        with patch("telegram_bot.answer_callback_query", new_callable=AsyncMock) as mock_answer:
            await process_telegram_update(session, token, update)
            mock_answer.assert_called_once_with(session, token, "cq_101", text="🙏 Спасибо за отзыв! Будем улучшать ответы.")

        stats = database.get_ai_feedback_stats()
        self.assertEqual(stats.get("bad"), 1)

    async def test_ai_consultant_keyboard_has_feedback_buttons(self):
        session = AsyncMock()
        token = "test_token"

        fake_result = {
            "answer": "Рекомендую наушники Sony WH-1000XM5.",
            "products": [{"shop": "Kaspi", "price": 180000, "url": "https://kaspi.kz/sony"}],
            "query_used": "наушники",
        }

        with patch("telegram_bot._consult", new_callable=AsyncMock, return_value=fake_result), \
             patch("telegram_bot.send_tg_rich_message", new_callable=AsyncMock, return_value=False), \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as mock_send:
            await handle_ai_consultant_message(session, token, 12345, "посоветуй наушники")
            self.assertTrue(mock_send.called)
            args, kwargs = mock_send.call_args
            reply_markup = args[4] if len(args) > 4 else kwargs.get("reply_markup")
            buttons = [b for row in reply_markup["inline_keyboard"] for b in row]
            callback_datas = [b.get("callback_data") for b in buttons if "callback_data" in b]
            self.assertIn("aifb:good", callback_datas)
            self.assertIn("aifb:bad", callback_datas)


if __name__ == "__main__":
    unittest.main()
