"""Тесты подсказки о блокировке бота (TG-04), перехода в Mini App из алертов (TG-19) и CloudStorage (TG-21)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock
from pathlib import Path

from web.server import _public_user
import notifier
from notifier import send_telegram_alert, send_watch_message


class MiniAppDeepLinksAndCloudTests(unittest.TestCase):
    def test_public_user_includes_bot_blocked_flag(self):
        # Пользователь не блокировал бота
        u_active = {
            "id": 101,
            "username": "user1",
            "first_name": "Ivan",
            "last_name": None,
            "photo_url": None,
            "is_blocked": False,
            "bot_blocked_at": None,
        }
        pub_active = _public_user(u_active)
        self.assertFalse(pub_active["bot_blocked"])

        # Пользователь заблокировал бота
        u_blocked = {
            "id": 102,
            "username": "user2",
            "first_name": "Petr",
            "last_name": None,
            "photo_url": None,
            "is_blocked": False,
            "bot_blocked_at": "2026-11-10T12:00:00",
        }
        pub_blocked = _public_user(u_blocked)
        self.assertTrue(pub_blocked["bot_blocked"])

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("notifier.APP_URL", "https://shdev.molthun.ru")
    @patch("requests.post")
    def test_alert_direct_miniapp_product_param(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 55}}
        mock_post.return_value = resp

        product = {
            "id": "prod_12345",
            "title": "MacBook Air M2",
            "current_price": 450000,
            "shop": "Kaspi",
            "url": "https://kaspi.kz/mac"
        }
        anomaly = {"emoji": "🔥", "old_price": 500000, "new_price": 450000, "drop_pct": 10, "savings": 50000, "reason": "Скидка"}

        send_telegram_alert(12345, product, anomaly)
        args, kwargs = mock_post.call_args
        reply_markup = kwargs["json"].get("reply_markup") or {}
        buttons = [b for row in reply_markup.get("inline_keyboard", []) for b in row]

        # Ищем кнопку "📊 Сравнить цены"
        compare_btn = next((b for b in buttons if "Сравнить цены" in b.get("text", "")), None)
        self.assertIsNotNone(compare_btn)
        # В режиме webapp это кнопка web_app с tgWebAppStartParam=p_prod_12345
        web_app_url = (compare_btn.get("web_app") or {}).get("url") or compare_btn.get("url") or ""
        self.assertIn("tgWebAppStartParam=p_prod_12345", web_app_url)

    @patch("notifier.get_bot_token", return_value="test_token")
    @patch("notifier.APP_URL", "https://shdev.molthun.ru")
    @patch("requests.post")
    def test_watch_message_direct_miniapp_watches_param(self, mock_post, mock_tok):
        resp = Mock(status_code=200)
        resp.json.return_value = {"ok": True, "result": {"message_id": 56}}
        mock_post.return_value = resp

        payload = {
            "watch_id": 8,
            "product": {"title": "Чайник", "shop": "Sulpak", "url": "https://sulpak.kz/kettle"},
            "price": 15000,
            "reason": "Цена упала"
        }
        send_watch_message(12345, payload)
        args, kwargs = mock_post.call_args
        reply_markup = kwargs["json"].get("reply_markup") or {}
        buttons = [b for row in reply_markup.get("inline_keyboard", []) for b in row]

        watches_btn = next((b for b in buttons if "Мои наблюдения" in b.get("text", "")), None)
        self.assertIsNotNone(watches_btn)
        web_app_url = (watches_btn.get("web_app") or {}).get("url") or watches_btn.get("url") or ""
        self.assertIn("tgWebAppStartParam=watches", web_app_url)

    def test_frontend_template_contains_tg_features(self):
        tmpl_path = Path("web/templates/search_first.html")
        content = tmpl_path.read_text(encoding="utf-8")
        # TG-04: баннер блокировки бота
        self.assertIn("profileBotBlockedBanner", content)
        # TG-19: обработка параметров перехода
        self.assertIn("watches", content)
        self.assertIn("tgWebAppStartParam", content)
        # TG-21: CloudStorage
        self.assertIn("CloudStorage", content)
        self.assertIn("syncCloudFavorites", content)


if __name__ == "__main__":
    unittest.main()
