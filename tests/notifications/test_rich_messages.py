"""Тесты форматированных Rich Messages (Bot API 10.1+, TG-03) и нативного времени <tg-time> (Bot API 9.5, TG-13)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock
import aiohttp

import notifier
from notifier import (
    tg_time_tag, expandable_quote, send_telegram_rich_message,
    send_telegram_alert, send_watch_message
)
import telegram_bot
from telegram_bot import send_tg_rich_message, build_price_comparison_markdown


ANOMALY = {
    "emoji": "🔥 СКИДКА",
    "old_price": 500000,
    "new_price": 400000,
    "drop_pct": 20,
    "savings": 100000,
    "reason": "Большое снижение цены по мониторингу сетей",
}


class NativeTimeAndQuoteTests(unittest.TestCase):
    def test_tg_time_tag_enabled(self):
        with patch.dict("os.environ", {"TELEGRAM_NATIVE_TIME": "on"}):
            tag = tg_time_tag(unix_ts=1731258000, format_spec="wDT", fallback="22:45")
            self.assertEqual(tag, '<tg-time unix="1731258000" format="wDT">22:45</tg-time>')

            rel_tag = tg_time_tag(unix_ts=1731258000, format_spec="r", fallback="только что")
            self.assertEqual(rel_tag, '<tg-time unix="1731258000" format="r">только что</tg-time>')

    def test_tg_time_tag_disabled(self):
        with patch.dict("os.environ", {"TELEGRAM_NATIVE_TIME": "off"}):
            tag = tg_time_tag(unix_ts=1731258000, format_spec="wDT", fallback="22:45")
            self.assertEqual(tag, "22:45")

    def test_expandable_quote(self):
        self.assertEqual(expandable_quote("Длинный текст"), "<blockquote expandable>Длинный текст</blockquote>")
        self.assertEqual(expandable_quote(""), "")

    def test_alert_contains_tg_time_tag(self):
        product = {"shop": "DNS", "title": "Ноутбук", "url": "https://dns.kz/p/123"}
        calls = []
        with patch.dict("os.environ", {"TELEGRAM_NATIVE_TIME": "on"}), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p, **k: calls.append(p) or Mock(status_code=200)):
            res = send_telegram_alert(12345, product, ANOMALY)

        self.assertTrue(res)
        text = calls[0].get("caption") or calls[0].get("text") or ""
        self.assertIn("<tg-time", text)
        self.assertIn('format="r"', text)


class RichMessagesTests(unittest.TestCase):
    def test_sync_send_telegram_rich_message(self):
        calls = []
        with patch.dict("os.environ", {"TELEGRAM_RICH_MESSAGES": "on"}), \
             patch.object(notifier, "telegram_api", side_effect=lambda m, p, **k: calls.append((m, p)) or Mock(status_code=200)):
            res = send_telegram_rich_message(12345, "# Заголовок\n\nТаблица:\n| A | B |\n|---|---|\n| 1 | 2 |")

        self.assertTrue(res)
        method, payload = calls[0]
        self.assertEqual(method, "sendRichMessage")
        self.assertEqual(payload["chat_id"], 12345)
        self.assertIn("| A | B |", payload["rich_message"]["markdown"])

    def test_sync_send_rich_message_disabled(self):
        with patch.dict("os.environ", {"TELEGRAM_RICH_MESSAGES": "off"}):
            res = send_telegram_rich_message(12345, "# Markdown")
            self.assertFalse(res)
            self.assertEqual(res.error, "rich_messages_disabled")

    def test_build_price_comparison_markdown_table(self):
        items = [
            {"shop": "Kaspi", "current_price": 500000, "title": "iPhone 16 Pro"},
            {"shop": "DNS", "current_price": 480000, "title": "iPhone 16 Pro"},
        ]
        table_md = build_price_comparison_markdown("iPhone 16", items)
        self.assertIn("| Магазин | Цена | Товар |", table_md)
        self.assertIn("|---|---|---|", table_md)
        self.assertIn("| Kaspi | **500 000 ₸** | iPhone 16 Pro |", table_md)
        self.assertIn("| DNS | **480 000 ₸** | iPhone 16 Pro |", table_md)


class AsyncRichMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_send_tg_rich_message_success(self):
        session = Mock(spec=aiohttp.ClientSession)
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value={"ok": True, "result": {"message_id": 999}})
        cm = AsyncMock()
        cm.__aenter__.return_value = resp_mock
        session.post = Mock(return_value=cm)

        with patch.dict("os.environ", {"TELEGRAM_RICH_MESSAGES": "on"}):
            ok = await send_tg_rich_message(session, "tok", 12345, "# Hello")
            self.assertTrue(ok)
            args, kwargs = session.post.call_args
            self.assertIn("sendRichMessage", args[0])
            self.assertEqual(kwargs["json"]["rich_message"]["markdown"], "# Hello")

    async def test_async_send_tg_rich_message_disabled(self):
        session = Mock(spec=aiohttp.ClientSession)
        with patch.dict("os.environ", {"TELEGRAM_RICH_MESSAGES": "off"}):
            ok = await send_tg_rich_message(session, "tok", 12345, "# Hello")
            self.assertFalse(ok)
            session.post.assert_not_called()

    async def test_search_command_prefers_rich_table_over_html(self):
        session = Mock(spec=aiohttp.ClientSession)
        items = [{"title": "MacBook Air", "current_price": 550000, "shop": "DNS", "url": "https://dns.kz/mac"}]
        with patch("telegram_bot.telegram_widgets.search_widget_enabled", return_value=False), \
             patch("telegram_bot.search_in_database", return_value=items), \
             patch("telegram_bot.send_tg_rich_message", new_callable=AsyncMock, return_value=True) as rich_mock, \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as text_mock:
            await telegram_bot.handle_search_command(session, "tok", 12345, "MacBook")

        rich_mock.assert_called_once()
        table_sent = rich_mock.call_args[0][3]
        self.assertIn("| Магазин | Цена | Товар |", table_sent)
        # When rich succeeds, plain text is not sent
        text_mock.assert_not_called()

    async def test_search_command_falls_back_when_rich_fails(self):
        session = Mock(spec=aiohttp.ClientSession)
        items = [{"title": "MacBook Air", "current_price": 550000, "shop": "DNS", "url": "https://dns.kz/mac"}]
        with patch("telegram_bot.telegram_widgets.search_widget_enabled", return_value=False), \
             patch("telegram_bot.search_in_database", return_value=items), \
             patch("telegram_bot.send_tg_rich_message", new_callable=AsyncMock, return_value=False) as rich_mock, \
             patch("telegram_bot.send_tg_message", new_callable=AsyncMock) as text_mock:
            await telegram_bot.handle_search_command(session, "tok", 12345, "MacBook")

        rich_mock.assert_called_once()
        text_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
