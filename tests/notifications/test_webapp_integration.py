"""Тесты серверной интеграции сайта как Telegram Mini App (TG-10)."""
import unittest
from pathlib import Path


class WebAppIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.template_path = Path(__file__).resolve().parent.parent.parent / "web" / "templates" / "search_first.html"
        self.assertTrue(self.template_path.exists(), "search_first.html must exist")
        self.html = self.template_path.read_text(encoding="utf-8")

    def test_telegram_webapp_script_is_loaded(self):
        """Проверяем подключение официального SDK Telegram Web App."""
        self.assertIn("telegram.org/js/telegram-web-app.js", self.html)

    def test_telegram_app_module_defined(self):
        """Проверяем наличие клиентского модуля TelegramApp."""
        self.assertIn("const TelegramApp = {", self.html)
        self.assertIn("ready()", self.html)
        self.assertIn("expand()", self.html)
        self.assertIn("enableClosingConfirmation()", self.html)
        self.assertIn("syncTheme()", self.html)
        self.assertIn("updateBackButton()", self.html)
        self.assertIn("HapticFeedback", self.html)
        self.assertIn("shareMessage", self.html)

    def test_safe_area_styles_present(self):
        """Проверяем наличие адаптивных отступов под Safe Area Telegram шторки."""
        self.assertIn("--tg-content-safe-area-inset-top", self.html)
        self.assertIn("--tg-content-safe-area-inset-bottom", self.html)

    def test_product_detail_has_share_button(self):
        """Проверяем наличие кнопки шеринга в карточке товара."""
        self.assertIn('id="detailShareBtn"', self.html)
        self.assertIn('shareCurrentProduct()', self.html)

    def test_share_deal_supports_prepared_message(self):
        """Проверяем интеграцию шеринга с подготовленными сообщениями Mini App."""
        self.assertIn("TelegramApp.shareProduct", self.html)
        self.assertIn("/api/telegram/prepared-message", self.html)


if __name__ == "__main__":
    unittest.main()
