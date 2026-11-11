"""Тесты нативных реакций бота (TG-22), команд /deals и /favorites (TG-23) и шеринга (TG-24)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock

import config
import notifier
from notifier import choose_chat_button
import telegram_bot
from telegram_bot import (
    set_tg_message_reaction,
    handle_deals_command,
    handle_favorites_command,
    profile_setup_calls,
    process_telegram_update,
)


class BotReactionsAndCommandsTests(unittest.IsolatedAsyncioTestCase):
    async def test_set_tg_message_reaction_enabled(self):
        session = Mock()
        resp_mock = AsyncMock()
        resp_mock.status = 200
        cm = AsyncMock()
        cm.__aenter__.return_value = resp_mock
        session.post = Mock(return_value=cm)

        with patch("config.reactions_enabled", return_value=True):
            res = await set_tg_message_reaction(session, "token", 12345, 999, "🔥")
            self.assertTrue(res)
            self.assertTrue(session.post.called)
            url, kwargs = session.post.call_args[0][0], session.post.call_args[1]
            self.assertIn("setMessageReaction", url)
            self.assertEqual(kwargs["json"]["reaction"], [{"type": "emoji", "emoji": "🔥"}])
            self.assertEqual(kwargs["json"]["message_id"], 999)

    async def test_set_tg_message_reaction_disabled(self):
        session = Mock()
        with patch("config.reactions_enabled", return_value=False):
            res = await set_tg_message_reaction(session, "token", 12345, 999, "🔥")
            self.assertFalse(res)
            self.assertFalse(session.post.called)

    def test_choose_chat_button_structure(self):
        btn = choose_chat_button("Поделиться", query="ноутбук", style="success")
        self.assertEqual(btn["text"], "Поделиться")
        self.assertIn("switch_inline_query_chosen_chat", btn)
        self.assertEqual(btn["switch_inline_query_chosen_chat"]["query"], "ноутбук")
        self.assertTrue(btn["switch_inline_query_chosen_chat"]["allow_user_chats"])

    async def test_handle_deals_command_with_deals(self):
        session = AsyncMock()
        fake_deals = [
            {"title": "iPhone 15", "current_price": 350000, "old_price": 400000, "discount_pct": 12, "shop": "Kaspi", "url": "https://kaspi.kz/iphone"},
            {"title": "Sony WH-1000XM5", "current_price": 160000, "old_price": 200000, "discount_pct": 20, "shop": "DNS", "url": "https://dns.kz/sony"},
        ]

        with patch("database.get_alerts", return_value=fake_deals), \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as mock_send:
            await handle_deals_command(session, "token", 12345)
            self.assertTrue(mock_send.called)
            args, kwargs = mock_send.call_args
            text = args[3] if len(args) > 3 else kwargs.get("text")
            self.assertIn("iPhone 15", text)
            self.assertIn("Sony WH-1000XM5", text)
            reply_markup = kwargs.get("reply_markup") or {}
            all_buttons = [b for row in reply_markup.get("inline_keyboard", []) for b in row]
            self.assertTrue(any("switch_inline_query_chosen_chat" in b for b in all_buttons))

    async def test_handle_favorites_command_empty_and_filled(self):
        session = AsyncMock()

        # Пустой список
        with patch("database.list_user_favorites", return_value=[]), \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as mock_send:
            await handle_favorites_command(session, "token", 12345, 100)
            args, kwargs = mock_send.call_args
            text = args[3] if len(args) > 3 else kwargs.get("text")
            self.assertIn("пусто", text)

        # Заполненный список
        fake_favs = [
            {"title": "Наушники AirPods", "new_price": 95000, "shop": "Technodom", "has_price_drop": True, "url": "https://technodom.kz/airpods"}
        ]
        with patch("database.list_user_favorites", return_value=fake_favs), \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as mock_send:
            await handle_favorites_command(session, "token", 12345, 100)
            args, kwargs = mock_send.call_args
            text = args[3] if len(args) > 3 else kwargs.get("text")
            self.assertIn("AirPods", text)
            self.assertIn("Снизилась", text)

    def test_profile_setup_calls_include_deals_and_favorites(self):
        calls = profile_setup_calls()
        cmd_ru_call = next(c for c in calls if c[0] == "setMyCommands" and c[1].get("language_code") == "ru")
        cmds_ru = [c["command"] for c in cmd_ru_call[1]["commands"]]
        self.assertIn("deals", cmds_ru)
        self.assertIn("favorites", cmds_ru)

        cmd_kk_call = next(c for c in calls if c[0] == "setMyCommands" and c[1].get("language_code") == "kk")
        cmds_kk = [c["command"] for c in cmd_kk_call[1]["commands"]]
        self.assertIn("deals", cmds_kk)
        self.assertIn("favorites", cmds_kk)


if __name__ == "__main__":
    unittest.main()
