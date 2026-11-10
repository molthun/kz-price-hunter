"""Тесты TG-25 (сканер штрихкодов /scan, showScanQrPopup), TG-26 (MainButton/SecondaryButton) и TG-27 (requestWriteAccess)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import patch, Mock, AsyncMock

import aiohttp

import config
import notifier
import telegram_bot
from telegram_bot import (
    get_default_reply_keyboard,
    handle_scan_command,
    process_telegram_update,
    BOT_COMMANDS,
    BOT_COMMANDS_KK,
)


class BarcodeScannerAndCommandsTests(unittest.IsolatedAsyncioTestCase):
    def test_bot_commands_include_scan(self):
        cmd_names = [c[0] for c in BOT_COMMANDS]
        self.assertIn("scan", cmd_names)
        kk_cmd_names = [c[0] for c in BOT_COMMANDS_KK]
        self.assertIn("scan", kk_cmd_names)

    def test_default_reply_keyboard_has_scan_button(self):
        kb = get_default_reply_keyboard("https://shdev.molthun.ru")
        rows = kb.get("keyboard", [])
        # В первой строке есть 🛍 Каталог и 📷 Сканер
        first_row = rows[0]
        scan_btn = next((b for b in first_row if "Сканер" in b.get("text", "")), None)
        self.assertIsNotNone(scan_btn)
        self.assertIn("scan=1", scan_btn.get("web_app", {}).get("url", ""))

    async def test_handle_scan_command_sends_webapp_button(self):
        session = Mock()
        sent_messages = []

        async def fake_send(s, t, chat_id, text, **kwargs):
            sent_messages.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})
            return {"ok": True}

        with patch("telegram_bot.send_tg_message", side_effect=fake_send), \
             patch("telegram_bot.APP_URL", "https://shdev.molthun.ru"):
            await handle_scan_command(session, "test_token", 555666)

        self.assertEqual(len(sent_messages), 1)
        msg = sent_messages[0]
        self.assertEqual(msg["chat_id"], 555666)
        self.assertIn("Сканер штрихкодов", msg["text"])
        markup = msg["kwargs"].get("reply_markup", {})
        inline = markup.get("inline_keyboard", [])
        self.assertTrue(len(inline) >= 1)
        # Кнопка с Web App со scan=1
        btn = inline[0][0]
        self.assertIn("scan=1", btn.get("web_app", {}).get("url", ""))

    async def test_process_update_routes_scan_command(self):
        session = Mock()
        called_scan = False

        async def fake_scan(s, t, chat_id, **kwargs):
            nonlocal called_scan
            called_scan = True

        update = {
            "update_id": 99911,
            "message": {
                "message_id": 42,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 12345, "first_name": "TestUser", "is_bot": False},
                "text": "/scan"
            }
        }

        with patch("telegram_bot.set_tg_message_reaction", new=AsyncMock()) as mock_reaction, \
             patch("telegram_bot.handle_scan_command", side_effect=fake_scan), \
             patch("telegram_bot.get_user", return_value={"id": 12345, "bot_blocked": False, "is_blocked": False}):
            await process_telegram_update(session, "test_token", update)

        self.assertTrue(called_scan)
        mock_reaction.assert_awaited_once_with(session, "test_token", 12345, 42, "📷")


class MiniAppTemplateContractTests(unittest.TestCase):
    def test_template_contains_tg25_tg26_tg27_methods(self):
        with open("web/templates/search_first.html", encoding="utf-8") as f:
            content = f.read()

        # TG-25: scanQrCode и showScanQrPopup
        self.assertIn("scanQrCode(preferredMode)", content)
        self.assertIn("showScanQrPopup", content)
        self.assertIn("search-scan-btn", content)

        # Выделенный сканер BarcodeScannerManager и EAN-13 видоискатель
        self.assertIn("BarcodeScannerManager", content)
        self.assertIn("extractProductFromScannedText", content)
        self.assertIn("scanner-reticle", content)
        self.assertIn("decodeEan13FromCanvas", content)
        self.assertIn("scannerModalBackdrop", content)
        self.assertIn("scannedUrlModal", content)

        # TG-26: MainButton и SecondaryButton
        self.assertIn("showProductActions(product)", content)
        self.assertIn("hideProductActions()", content)
        self.assertIn("SecondaryButton", content)

        # TG-27: requestWriteAccess
        self.assertIn("requestWriteAccess(callback)", content)


class SmartBarcodeAndQrExtractorContractTests(unittest.TestCase):
    """Проверка шаблонов и логики резолвинга штрихкодов и QR-кодов."""
    def test_barcode_and_marking_regex_patterns(self):
        import re

        # Шаблон EAN-13
        ean_pattern = re.compile(r"^\d{13}$")
        self.assertTrue(ean_pattern.match("8806091234567"))
        self.assertFalse(ean_pattern.match("https://samsung.com"))

        # Шаблон GS1 / Naqty Onim маркировки
        gs1_pattern = re.compile(r"(?:(?:^|[^\d])01|\(01\))(\d{14})")
        m1 = gs1_pattern.search("010487000123456721xyz987")
        self.assertIsNotNone(m1)
        gtin14 = m1.group(1)
        self.assertEqual(gtin14, "04870001234567")
        ean13 = gtin14[1:] if gtin14.startswith("0") else gtin14
        self.assertEqual(ean13, "4870001234567")

        # GS1 с префиксом (01)
        m2 = gs1_pattern.search("(01)04870001234567(21)xyz987")
        self.assertIsNotNone(m2)
        self.assertEqual(m2.group(1), "04870001234567")
