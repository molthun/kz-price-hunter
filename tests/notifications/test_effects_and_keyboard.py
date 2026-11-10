"""Тесты визуальных эффектов (TG-14, Bot API 7.5), постоянной клавиатуры (TG-15) и профиля (ru/kk)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock

import aiohttp

import config
import notifier
from notifier import send_telegram_alert, send_watch_message, EFFECT_FIRE, EFFECT_CONFETTI
import telegram_bot
from telegram_bot import (
    get_default_reply_keyboard,
    profile_setup_calls,
    setup_bot_profile,
    process_telegram_update,
)


class MessageEffectsTests(unittest.TestCase):
    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_alert_with_high_drop_has_fire_effect(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 10}}
        mock_post.return_value = resp

        product = {"id": "p1", "title": "iPhone 15", "current_price": 350000, "shop": "Kaspi"}
        anomaly = {"emoji": "🔥", "old_price": 500000, "new_price": 350000, "drop_pct": 30, "savings": 150000, "reason": "Большая скидка"}

        with patch("config.message_effects_enabled", return_value=True):
            res = send_telegram_alert(12345, product, anomaly)
            self.assertTrue(res)
            args, kwargs = mock_post.call_args
            self.assertEqual(kwargs["json"].get("message_effect_id"), EFFECT_FIRE)

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_alert_effects_disabled(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 10}}
        mock_post.return_value = resp

        product = {"id": "p1", "title": "iPhone 15", "current_price": 350000, "shop": "Kaspi"}
        anomaly = {"emoji": "🔥", "old_price": 500000, "new_price": 350000, "drop_pct": 30, "savings": 150000, "reason": "Большая скидка"}

        with patch("config.message_effects_enabled", return_value=False):
            res = send_telegram_alert(12345, product, anomaly)
            self.assertTrue(res)
            args, kwargs = mock_post.call_args
            self.assertNotIn("message_effect_id", kwargs["json"])

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("requests.post")
    def test_watch_message_has_confetti_effect(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 11}}
        mock_post.return_value = resp

        payload = {
            "watch_id": 100,
            "product": {"title": "AirPods Pro", "shop": "DNS", "url": "https://dns.kz/a"},
            "price": 95000,
            "old_price": 120000,
            "drop_pct": 20,
            "reason": "Цена снизилась"
        }
        with patch("config.message_effects_enabled", return_value=True):
            res = send_watch_message(12345, payload)
            self.assertTrue(res)
            args, kwargs = mock_post.call_args
            self.assertEqual(kwargs["json"].get("message_effect_id"), EFFECT_CONFETTI)


class ReplyKeyboardAndProfileTests(unittest.TestCase):
    def test_default_reply_keyboard_structure(self):
        kb = get_default_reply_keyboard("https://shdev.molthun.ru")
        self.assertTrue(kb.get("is_persistent"))
        self.assertTrue(kb.get("resize_keyboard"))
        buttons = kb.get("keyboard", [])
        self.assertEqual(len(buttons), 3)
        # First row has web_app catalog and barcode scanner buttons
        first_btn = buttons[0][0]
        self.assertEqual(first_btn["text"], "🛍 Открыть каталог")
        self.assertEqual(first_btn["web_app"]["url"], "https://shdev.molthun.ru")
        self.assertEqual(buttons[0][1]["text"], "📷 Сканер штрихкода")
        self.assertIn("scan=1", buttons[0][1]["web_app"]["url"])
        self.assertEqual(buttons[1][0]["text"], "🔥 Горячие скидки")
        self.assertEqual(buttons[1][1]["text"], "❤️ Избранное")
        self.assertEqual(buttons[2][0]["text"], "🔔 Мои наблюдения")
        self.assertEqual(buttons[2][1]["text"], "🤖 AI-Консультант")

    def test_profile_setup_calls_contains_ru_and_kk(self):
        calls = profile_setup_calls()
        methods = [c[0] for c in calls]
        self.assertIn("setMyDescription", methods)
        self.assertIn("setMyShortDescription", methods)
        self.assertIn("setMyCommands", methods)

        kk_cmds = next(c[1]["commands"] for c in calls if c[0] == "setMyCommands" and c[1].get("language_code") == "kk")
        self.assertTrue(any(cmd["command"] == "search" and "Тауар" in cmd["description"] for cmd in kk_cmds))


class TelegramBotQuickActionsAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_action_watches_button(self):
        session = Mock(spec=aiohttp.ClientSession)
        update = {
            "message": {
                "message_id": 1,
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100, "is_bot": False},
                "text": "🔔 Мои наблюдения"
            }
        }
        with patch("telegram_bot.handle_watches_command", new_callable=AsyncMock) as mock_watches, \
             patch("telegram_bot.get_user", return_value=None):
            await process_telegram_update(session, "tok", update)
            mock_watches.assert_awaited_once_with(session, "tok", 100)

    async def test_quick_action_ai_consultant_button(self):
        session = Mock(spec=aiohttp.ClientSession)
        update = {
            "message": {
                "message_id": 2,
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100, "is_bot": False},
                "text": "🤖 AI-Консультант"
            }
        }
        with patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as mock_send, \
             patch("telegram_bot.get_user", return_value=None):
            await process_telegram_update(session, "tok", update)
            mock_send.assert_awaited_once()
            args, kwargs = mock_send.call_args
            self.assertIn("AI-Консультант", args[3])
