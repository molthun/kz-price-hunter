"""Тесты inline-режима бота (@бот <запрос>, Bot API 8.0, TG-12) и подготовленных сообщений savePreparedInlineMessage."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock
import aiohttp
from aiohttp.test_utils import make_mocked_request

import notifier
from notifier import save_prepared_inline_message
import telegram_bot
from telegram_bot import handle_inline_query, process_telegram_update


class InlineModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_inline_query_with_search_query(self):
        session = Mock(spec=aiohttp.ClientSession)
        post_cm = AsyncMock()
        post_cm.status = 200
        cm = AsyncMock()
        cm.__aenter__.return_value = post_cm
        session.post = Mock(return_value=cm)

        items = [
            {"id": "p1", "title": "Apple iPhone 16", "current_price": 499990, "shop": "DNS", "url": "https://dns.kz/p1", "city": "Астана"}
        ]
        iq = {
            "id": "query_123",
            "from": {"id": 1001, "username": "shopper", "first_name": "Shopper"},
            "query": "iPhone 16"
        }

        with patch("telegram_bot.search_in_database", return_value=items), \
             patch("telegram_bot.get_bot_username", new_callable=AsyncMock, return_value="kzph_test_bot"), \
             patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}):
            await handle_inline_query(session, "tok", iq)

        session.post.assert_called_once()
        args, kwargs = session.post.call_args
        self.assertIn("answerInlineQuery", args[0])
        payload = kwargs["json"]
        self.assertEqual(payload["inline_query_id"], "query_123")
        results = payload["results"]
        self.assertEqual(len(results), 1)
        res = results[0]
        self.assertEqual(res["type"], "article")
        self.assertIn("499 990 ₸", res["title"])
        self.assertIn("Apple iPhone 16", res["title"])
        msg_text = res["input_message_content"]["message_text"]
        self.assertIn("Apple iPhone 16", msg_text)
        self.assertIn("499 990 ₸", msg_text)
        self.assertIn("@kzph_test_bot", msg_text)
        # Inline keyboard buttons
        kb = res["reply_markup"]["inline_keyboard"]
        self.assertEqual(kb[0][0]["style"], "primary")
        self.assertEqual(kb[0][1]["copy_text"]["text"], "https://dns.kz/p1")

    async def test_handle_inline_query_empty_query_shows_hot_deals(self):
        session = Mock(spec=aiohttp.ClientSession)
        post_cm = AsyncMock()
        post_cm.status = 200
        cm = AsyncMock()
        cm.__aenter__.return_value = post_cm
        session.post = Mock(return_value=cm)

        deals = [
            {"id": "d1", "title": "Наушники Sony", "price": 120000, "old_price": 160000, "drop_pct": 25, "shop": "Kaspi", "url": "https://kaspi.kz/d1", "city": "Алматы"}
        ]
        iq = {
            "id": "query_empty",
            "from": {"id": 1002, "username": "user2"},
            "query": ""
        }

        with patch("database.get_alerts", return_value=deals), \
             patch("telegram_bot.get_bot_username", new_callable=AsyncMock, return_value="kzph_test_bot"), \
             patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}):
            await handle_inline_query(session, "tok", iq)

        session.post.assert_called_once()
        payload = session.post.call_args[1]["json"]
        res = payload["results"][0]
        self.assertIn("-25%", res["title"])
        self.assertIn("120 000 ₸", res["title"])

    async def test_handle_inline_query_disabled(self):
        session = Mock(spec=aiohttp.ClientSession)
        iq = {"id": "q1", "query": "test"}
        with patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "off"}):
            await handle_inline_query(session, "tok", iq)
        session.post.assert_not_called()

    async def test_process_telegram_update_routes_inline_query(self):
        session = Mock(spec=aiohttp.ClientSession)
        update = {"inline_query": {"id": "iq_999", "query": "macbook"}}
        with patch("telegram_bot.handle_inline_query", new_callable=AsyncMock) as mock_handle:
            await process_telegram_update(session, "tok", update)
        mock_handle.assert_called_once()


class PreparedInlineMessageTests(unittest.IsolatedAsyncioTestCase):
    def test_save_prepared_inline_message_helper(self):
        calls = []
        mock_resp = Mock(status_code=200)
        mock_resp.json.return_value = {"ok": True, "result": {"id": "prep_xyz123"}}

        with patch("notifier.get_bot_token", return_value="tok"), \
             patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on"}), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p: calls.append((m, p)) or mock_resp):
            prep_id = save_prepared_inline_message(
                user_id=777,
                title="Игровой монитор 144Hz",
                price=85000,
                shop="Белый Ветер",
                url="https://shop.kz/mon",
                city="Астана"
            )

        self.assertEqual(prep_id, "prep_xyz123")
        self.assertEqual(len(calls), 1)
        method, payload = calls[0]
        self.assertEqual(method, "savePreparedInlineMessage")
        self.assertEqual(payload["user_id"], 777)
        self.assertTrue(payload["allow_user_chats"])
        self.assertTrue(payload["allow_group_chats"])
        article = payload["result"]
        self.assertEqual(article["type"], "article")
        self.assertIn("85 000 ₸", article["title"])

    def test_prepared_message_has_no_web_app_buttons(self):
        """В пересылаемых сообщениях web_app-кнопки запрещены: Telegram отвечает BUTTON_TYPE_INVALID."""
        calls = []
        mock_resp = Mock(status_code=200)
        mock_resp.json.return_value = {"ok": True, "result": {"id": "prep_1"}}
        with patch("notifier.get_bot_token", return_value="tok"), \
             patch.object(notifier, "APP_URL", "https://shdev.example"), \
             patch.dict("os.environ", {"TELEGRAM_INLINE_MODE": "on", "TELEGRAM_SITE_BUTTONS": "webapp"}), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p: calls.append((m, p)) or mock_resp):
            save_prepared_inline_message(777, "Монитор", 85000, "Белый Ветер", "https://shop.kz/mon", "Астана")
        buttons = [b for row in calls[0][1]["result"]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertTrue(any(b.get("url") == "https://shdev.example" for b in buttons))
        self.assertFalse(any("web_app" in b for b in buttons))

    async def test_prepared_message_endpoint_validation_and_response(self):
        from web.server import telegram_prepared_message_handler

        # A03: без сессии и подписанного initData — 401, user_id из тела игнорируется
        req_guest = make_mocked_request("POST", "/api/telegram/prepared-message")
        req_guest._read_bytes = b'{"user_id": 100, "title": "iPhone", "price": 500000}'
        with patch("notifier.save_prepared_inline_message", return_value="prep_mock_id") as send:
            res_401 = await telegram_prepared_message_handler(req_guest)
        self.assertEqual(res_401.status, 401)
        send.assert_not_called()

        # Сессия без ID товара -> 400
        req_missing = make_mocked_request("POST", "/api/telegram/prepared-message")
        req_missing["user"] = {"id": 100}
        req_missing._read_bytes = b'{"title": "iPhone"}'
        res_400 = await telegram_prepared_message_handler(req_missing)
        self.assertEqual(res_400.status, 400)

        # Карточка из каталога -> 200, получатель — пользователь сессии
        req_ok = make_mocked_request("POST", "/api/telegram/prepared-message")
        req_ok["user"] = {"id": 100}
        req_ok._read_bytes = b'{"user_id": 555, "product_id": "p1"}'
        card = {"title": "iPhone", "price": 500000, "shop": "DNS", "url": "https://dns.kz/p1", "image_url": "", "city": ""}
        with patch("web.server._trusted_share_card_sync", return_value=card), \
             patch("notifier.save_prepared_inline_message", return_value="prep_mock_id") as send:
            res_ok = await telegram_prepared_message_handler(req_ok)
            self.assertEqual(res_ok.status, 200)
            self.assertIn("prep_mock_id", res_ok.body.decode("utf-8"))
            self.assertEqual(send.call_args.args[0], 100)


if __name__ == "__main__":
    unittest.main()
