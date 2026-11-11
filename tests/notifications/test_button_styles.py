"""Тесты кнопок Telegram: стили (Bot API 9.4) и быстрое копирование (Bot API 7.11, TG-08)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock
import aiohttp

import notifier
from notifier import (
    button_style, url_button, callback_button, copy_button, site_button,
    send_telegram_alert, send_watch_message
)
import telegram_bot


ANOMALY = {
    "emoji": "🔥 СКИДКА",
    "old_price": 500000,
    "new_price": 400000,
    "drop_pct": 20,
    "savings": 100000,
    "reason": "Снижение цены на 20%",
}


class ButtonStylesTests(unittest.TestCase):
    def test_button_style_resolution(self):
        with patch.dict("os.environ", {"TELEGRAM_BUTTON_STYLES": "on"}):
            self.assertEqual(button_style("primary"), "primary")
            self.assertEqual(button_style("SUCCESS"), "success")
            self.assertEqual(button_style("danger"), "danger")
            self.assertIsNone(button_style("invalid"))
            self.assertIsNone(button_style(None))
            self.assertIsNone(button_style(""))

    def test_button_style_disabled_by_env(self):
        with patch.dict("os.environ", {"TELEGRAM_BUTTON_STYLES": "off"}):
            self.assertIsNone(button_style("primary"))
            self.assertIsNone(button_style("danger"))

    def test_url_button_structure(self):
        with patch.dict("os.environ", {"TELEGRAM_BUTTON_STYLES": "on"}):
            btn = url_button("Открыть", "https://example.com", style="primary")
            self.assertEqual(btn, {"text": "Открыть", "url": "https://example.com", "style": "primary"})

            btn_no_style = url_button("Открыть", "https://example.com")
            self.assertEqual(btn_no_style, {"text": "Открыть", "url": "https://example.com"})

    def test_callback_button_structure(self):
        with patch.dict("os.environ", {"TELEGRAM_BUTTON_STYLES": "on"}):
            btn = callback_button("Удалить", "unwatch:10", style="danger")
            self.assertEqual(btn, {"text": "Удалить", "callback_data": "unwatch:10", "style": "danger"})

    def test_copy_button_structure_and_limits(self):
        with patch.dict("os.environ", {"TELEGRAM_COPY_BUTTON": "on", "TELEGRAM_BUTTON_STYLES": "on"}):
            btn = copy_button("📋 Скопировать", "https://example.com/item")
            self.assertIsNotNone(btn)
            self.assertEqual(btn["text"], "📋 Скопировать")
            self.assertEqual(btn["copy_text"], {"text": "https://example.com/item"})

            # Limit: 256 characters
            long_url = "https://example.com/" + "a" * 300
            btn_long = copy_button("📋", long_url)
            self.assertIsNotNone(btn_long)
            self.assertEqual(len(btn_long["copy_text"]["text"]), 256)

            # Empty/None -> None
            self.assertIsNone(copy_button("📋", ""))
            self.assertIsNone(copy_button("📋", None))

    def test_copy_button_disabled_by_env(self):
        with patch.dict("os.environ", {"TELEGRAM_COPY_BUTTON": "off"}):
            self.assertIsNone(copy_button("📋", "https://example.com"))

    def test_site_button_supports_style(self):
        with patch.dict("os.environ", {"TELEGRAM_BUTTON_STYLES": "on", "TELEGRAM_SITE_BUTTONS": "webapp"}):
            btn = site_button("Сайт", "https://shop.example.kz", style="primary")
            self.assertEqual(btn["style"], "primary")
            self.assertEqual(btn["web_app"], {"url": "https://shop.example.kz"})

    def test_send_telegram_alert_keyboard_has_primary_and_copy(self):
        product = {"shop": "DNS", "title": "Ноутбук", "url": "https://dns.kz/p/123"}
        calls = []
        with patch.object(notifier, "APP_URL", "https://shop.example.kz"), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p, **k: calls.append(p) or Mock(status_code=200)):
            res = send_telegram_alert(12345, product, ANOMALY)

        self.assertTrue(res)
        keyboard = calls[0]["reply_markup"]["inline_keyboard"]
        # Row 0: primary url button
        self.assertEqual(keyboard[0][0]["url"], "https://dns.kz/p/123")
        self.assertEqual(keyboard[0][0]["style"], "primary")
        # Row 1: site button + copy button
        self.assertEqual(keyboard[1][0]["web_app"], {"url": "https://shop.example.kz"})
        self.assertEqual(keyboard[1][1]["copy_text"], {"text": "https://dns.kz/p/123"})

    def test_send_watch_message_keyboard_has_danger_and_copy(self):
        payload = {
            "product": {"shop": "Kaspi", "title": "iPhone 16", "url": "https://kaspi.kz/p/456"},
            "watch_id": 42,
            "price": 550000,
            "reason": "Цена снизилась на 15%",
            "description": "Наблюдение iPhone 16",
        }
        calls = []
        with patch.object(notifier, "APP_URL", "https://shop.example.kz"), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p, **k: calls.append(p) or Mock(status_code=200)):
            res = send_watch_message(12345, payload)

        self.assertTrue(res)
        keyboard = calls[0]["reply_markup"]["inline_keyboard"]
        # Row 0: open offer (primary)
        self.assertEqual(keyboard[0][0]["url"], "https://kaspi.kz/p/456")
        self.assertEqual(keyboard[0][0]["style"], "primary")
        # Row 1: unwatch (danger) + copy button
        self.assertEqual(keyboard[1][0]["callback_data"], "unwatch:42")
        self.assertEqual(keyboard[1][0]["style"], "danger")
        self.assertEqual(keyboard[1][1]["copy_text"], {"text": "https://kaspi.kz/p/456"})


class BotCommandButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_watches_command_unwatch_button_has_danger_style(self):
        session = Mock(spec=aiohttp.ClientSession)
        watches = [{"id": 7, "title": "Монитор LG", "description": "Скидка"}]
        with patch("telegram_bot.telegram_widgets.watches_widget_enabled", return_value=False), \
             patch("database.list_watches", return_value=watches), \
             patch.object(telegram_bot, "send_tg_message", new_callable=AsyncMock) as send_mock:
            await telegram_bot.handle_watches_command(session, "tok", 12345)

        send_mock.assert_called_once()
        reply_markup = send_mock.call_args[0][4]
        keyboard = reply_markup["inline_keyboard"]
        self.assertEqual(keyboard[0][0]["callback_data"], "unwatch:7")
        self.assertEqual(keyboard[0][0]["style"], "danger")

    async def test_search_command_fallback_buttons_have_style_and_copy(self):
        session = Mock(spec=aiohttp.ClientSession)
        items = [{"title": "SSD Samsung", "current_price": 45000, "shop": "Белый Ветер", "url": "https://shop.kz/p/1"}]
        with patch("telegram_bot.telegram_widgets.search_widget_enabled", return_value=False), \
             patch("telegram_bot.search_in_database", return_value=items), \
             patch.object(telegram_bot, "send_tg_message", new_callable=AsyncMock) as send_mock:
            await telegram_bot.handle_search_command(session, "tok", 12345, "SSD")

        send_mock.assert_called_once()
        reply_markup = send_mock.call_args[0][4]
        keyboard = reply_markup["inline_keyboard"]
        row0 = keyboard[0]
        self.assertEqual(row0[0]["url"], "https://shop.kz/p/1")
        self.assertEqual(row0[0]["style"], "primary")
        self.assertEqual(row0[1]["copy_text"], {"text": "https://shop.kz/p/1"})


if __name__ == "__main__":
    unittest.main()
