"""Тесты тем (форум-топиков) в личном чате пользователя (TG-11, Bot API 9.3/9.4/10.4)."""
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch, Mock, AsyncMock

import aiohttp

import config
import database
from database import get_user_topic, save_user_topic, get_all_user_topics, init_db
import notifier
from notifier import get_or_create_forum_topic, TOPIC_METADATA
import telegram_bot
from telegram_bot import get_or_create_tg_topic, handle_search_command, handle_ai_consultant_message, process_telegram_update


class TopicsDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_patch = patch.object(database, "DB_PATH", Path(self.tmp.name))
        self.db_patch.start()
        init_db()

    def tearDown(self):
        self.db_patch.stop()
        if os.path.exists(self.tmp.name):
            os.remove(self.tmp.name)

    def test_save_and_get_user_topic(self):
        self.assertIsNone(get_user_topic(12345, "alerts"))
        save_user_topic(12345, "alerts", 42)
        self.assertEqual(get_user_topic(12345, "alerts"), 42)

        # Update / replace
        save_user_topic(12345, "alerts", 99)
        self.assertEqual(get_user_topic(12345, "alerts"), 99)

        # Multiple topics for user
        save_user_topic(12345, "watches", 101)
        save_user_topic(12345, "ai", 102)

        all_topics = get_all_user_topics(12345)
        self.assertEqual(all_topics, {"alerts": 99, "watches": 101, "ai": 102})


class NotifierTopicsTests(unittest.TestCase):
    def setUp(self):
        notifier._unsupported_topics_chats.clear()

    def test_topics_mode_disabled(self):
        with patch("config.topics_mode_enabled", return_value=False):
            tid = get_or_create_forum_topic("12345", "alerts")
            self.assertIsNone(tid)

    def test_unsupported_chat_cached(self):
        notifier._unsupported_topics_chats.add(12345)
        with patch("config.topics_mode_enabled", return_value=True):
            tid = get_or_create_forum_topic("12345", "alerts")
            self.assertIsNone(tid)

    @patch("config.topics_mode_enabled", return_value=True)
    @patch("database.get_user_topic", return_value=55)
    def test_get_existing_topic(self, mock_get_topic, mock_enabled):
        tid = get_or_create_forum_topic(12345, "alerts")
        self.assertEqual(tid, 55)

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("config.topics_mode_enabled", return_value=True)
    @patch("database.get_user_topic", return_value=None)
    @patch("database.save_user_topic")
    @patch("requests.post")
    def test_create_topic_success(self, mock_post, mock_save, mock_get, mock_enabled, mock_tok):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, "result": {"message_thread_id": 77}}
        mock_post.return_value = resp

        tid = get_or_create_forum_topic(12345, "watches")
        self.assertEqual(tid, 77)
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertIn("createForumTopic", args[0])
        self.assertEqual(kwargs["json"]["chat_id"], 12345)
        self.assertEqual(kwargs["json"]["name"], TOPIC_METADATA["watches"]["name"])
        mock_save.assert_called_once_with(12345, "watches", 77)

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("config.topics_mode_enabled", return_value=True)
    @patch("database.get_user_topic", return_value=None)
    @patch("requests.post")
    def test_create_topic_unsupported_bad_request(self, mock_post, mock_get, mock_enabled, mock_tok):
        resp = Mock()
        resp.status_code = 400
        resp.json.return_value = {"ok": False, "description": "Bad Request: chat has no forum topics"}
        mock_post.return_value = resp

        tid = get_or_create_forum_topic(99999, "alerts")
        self.assertIsNone(tid)
        self.assertIn(99999, notifier._unsupported_topics_chats)


    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("notifier.get_or_create_forum_topic", return_value=333)
    @patch("requests.post")
    def test_send_telegram_alert_with_topic(self, mock_post, mock_topic, mock_tok):
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, "result": {"message_id": 11}}
        mock_post.return_value = resp

        product = {"id": "p1", "title": "Тестовый товар", "current_price": 50000, "shop": "Kaspi", "url": "https://kaspi.kz/p1"}
        anomaly = {"emoji": "🔥", "old_price": 70000, "new_price": 50000, "drop_pct": 28, "savings": 20000, "reason": "Скидка"}

        res = notifier.send_telegram_alert(12345, product, anomaly)
        self.assertTrue(res)
        mock_topic.assert_called_once_with(12345, "alerts")
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs["json"]["message_thread_id"], 333)


class TelegramBotTopicsAsyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        telegram_bot._unsupported_topics_chats.clear()

    async def test_get_or_create_tg_topic_success(self):
        session = Mock(spec=aiohttp.ClientSession)
        post_cm = AsyncMock()
        post_cm.status = 200
        post_cm.json = AsyncMock(return_value={"ok": True, "result": {"message_thread_id": 88}})
        cm = AsyncMock()
        cm.__aenter__.return_value = post_cm
        session.post = Mock(return_value=cm)

        with patch("config.topics_mode_enabled", return_value=True), \
             patch("database.get_user_topic", return_value=None), \
             patch("database.save_user_topic") as mock_save:
            tid = await get_or_create_tg_topic(session, "tok", 12345, "ai")
            self.assertEqual(tid, 88)
            session.post.assert_called_once()
            args, kwargs = session.post.call_args
            self.assertIn("createForumTopic", args[0])
            self.assertEqual(kwargs["json"]["chat_id"], 12345)
            self.assertEqual(kwargs["json"]["name"], TOPIC_METADATA["ai"]["name"])

    async def test_handle_search_command_routes_to_topic(self):
        session = Mock(spec=aiohttp.ClientSession)
        post_cm = AsyncMock()
        post_cm.status = 200
        post_cm.json = AsyncMock(return_value={"ok": True})
        cm = AsyncMock()
        cm.__aenter__.return_value = post_cm
        session.post = Mock(return_value=cm)

        items = [{"id": "p1", "title": "MacBook Air", "price": 450000, "shop": "Белый Ветер", "url": "https://shop.kz/p1"}]
        with patch("telegram_bot.get_or_create_tg_topic", new_callable=AsyncMock, return_value=123) as mock_get_topic, \
             patch("telegram_bot.search_in_database", return_value=items), \
             patch("telegram_bot.send_tg_chat_action", new_callable=AsyncMock), \
             patch("telegram_bot.send_tg_rich_message", new_callable=AsyncMock, return_value=True) as mock_rich:
            await handle_search_command(session, "tok", 777, "MacBook")
            mock_get_topic.assert_called_once_with(session, "tok", 777, "search")
            mock_rich.assert_called_once()
            self.assertEqual(mock_rich.call_args.kwargs.get("message_thread_id"), 123)

    async def test_handle_ai_consultant_message_routes_to_topic(self):
        session = Mock(spec=aiohttp.ClientSession)
        with patch("telegram_bot.get_or_create_tg_topic", new_callable=AsyncMock, return_value=999) as mock_get_topic, \
             patch("telegram_bot._consult", new_callable=AsyncMock, return_value={"answer": "Рекомендую iPad", "products": []}) as mock_consult, \
             patch("telegram_bot.send_tg_rich_message", new_callable=AsyncMock, return_value=True) as mock_rich:
            await handle_ai_consultant_message(session, "tok", 888, "Какой планшет купить?")
            mock_get_topic.assert_called_once_with(session, "tok", 888, "ai")
            mock_consult.assert_called_once_with(session, "tok", 888, "Какой планшет купить?", message_thread_id=999)
            mock_rich.assert_called_once()
            self.assertEqual(mock_rich.call_args.kwargs.get("message_thread_id"), 999)

    async def test_process_telegram_update_passes_thread_id(self):
        session = Mock(spec=aiohttp.ClientSession)
        update = {
            "message": {
                "message_id": 1,
                "message_thread_id": 456,
                "chat": {"id": 100, "type": "private"},
                "from": {"id": 100, "is_bot": False, "first_name": "Test"},
                "text": "/search iPad"
            }
        }
        with patch("telegram_bot.handle_search_command", new_callable=AsyncMock) as mock_search, \
             patch("telegram_bot.get_user", return_value=None):
            await process_telegram_update(session, "tok", update)
            mock_search.assert_called_once_with(session, "tok", 100, "iPad", message_thread_id=456)
