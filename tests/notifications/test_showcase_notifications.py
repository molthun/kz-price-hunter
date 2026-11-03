"""Тестирование уведомлений по наблюдениям за ценами при сборе витрин маркетплейсов."""
from tests import test_support  # noqa: F401
import os
import tempfile
import unittest
from unittest.mock import Mock, patch, AsyncMock

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

import database
import notifier
from database import get_connection, init_db, create_watch, upsert_telegram_user, save_user_settings
from domain.adapter import canonical_product_id
from domain.channel_sync import sync_channel_showcase, ShowcaseOffer, make_fallback_canonical_key
from model_matching import extract_canonical_key
from scrapers.showcase_scanners import KaspiShowcaseScanner
import telegram_bot


class ShowcaseNotificationsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()
        self.user_id = 999123
        with get_connection() as conn:
            conn.execute("DELETE FROM watches")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM watch_events")
            conn.execute("DELETE FROM notification_outbox")
            conn.execute("DELETE FROM offers")
            conn.execute("DELETE FROM canonical_products")
            conn.commit()

        upsert_telegram_user({
            "id": self.user_id,
            "username": "tester",
            "first_name": "Test",
            "last_name": "User",
        })
        # Включаем telegram_notify_enabled
        save_user_settings(self.user_id, {"telegram_notify_enabled": True, "telegram_notify_level": "ALL"})
        with get_connection() as conn:
            # Создаем тестового продавца и канал
            conn.execute(
                "INSERT OR REPLACE INTO sellers (id, slug, name, is_active, created_at, updated_at) VALUES ('sel_test', 'test-store', 'Test Store', 1, '2026-01-01', '2026-01-01')"
            )
            conn.execute(
                """INSERT OR REPLACE INTO channels (
                    id, seller_id, channel_type, name, external_store_id, base_url, is_active, created_at
                ) VALUES (
                    'ch_test_kaspi', 'sel_test', 'kaspi', 'Test Store Kaspi', 'merchant_123', 'https://kaspi.kz/shop/merchant/123', 1, '2026-01-01'
                )"""
            )
            conn.commit()

    async def test_showcase_sync_triggers_watch_and_delivers_via_offers(self):
        """Проверяем, что сбор витрины находит наблюдение, ставит задачу в outbox, и notifier валидирует цену по offers."""
        title = "Умный робот-пылесос"
        ck = extract_canonical_key(title) or make_fallback_canonical_key(title)
        prod_id = canonical_product_id(ck)

        # 1. Создаем наблюдение пользователя за товаром (без тихих часов для немедленной доставки)
        watch = create_watch(self.user_id, {
            "kind": "product",
            "target": prod_id,
            "condition": "target_price",
            "threshold": 60000,
            "title": title,
            "repeat": True,
            "quiet_from": "00:00",
            "quiet_to": "00:00",
            "cooldown_hours": 0,
        })
        self.assertTrue(watch.get("id"))

        # 2. Мокаем сканер витрины Kaspi, чтобы он вернул предложение по цене 55 000 ₸ (ниже порога 60 000 ₸)
        fake_offers = [
            ShowcaseOffer(
                marketplace="kaspi",
                merchant_id="merchant_123",
                external_sku="sku_vac_001",
                title=title,
                price=55000,
                old_price=65000,
                url="https://kaspi.kz/shop/p/vac-001",
                image_url="https://img.kaspi.kz/preview-medium/vac001.jpg",
                in_stock=True,
                city="Алматы",
                rating=4.9,
                reviews_count=20,
            )
        ]

        with patch.object(KaspiShowcaseScanner, "scan_merchant_showcase", new=AsyncMock(return_value=fake_offers)):
            with get_connection() as conn:
                res = await sync_channel_showcase(conn, "ch_test_kaspi", max_pages=1)
                self.assertTrue(res["ok"])
                self.assertEqual(res["offers_upserted"], 1)
                self.assertGreaterEqual(res["notifications_queued"], 1)

        # 3. Проверяем, что в notification_outbox появилась запись
        with get_connection() as conn:
            outbox_row = conn.execute(
                "SELECT id, alert_id, user_id, payload, status FROM notification_outbox WHERE user_id = ?",
                (self.user_id,)
            ).fetchone()
            self.assertIsNotNone(outbox_row)
            self.assertEqual(outbox_row["status"], "pending")

        # 4. Проверяем доставку через notifier._deliver_watch
        # В таблице products записей НЕТ (Search Platform 2.0 хранит в offers и canonical_products)
        with get_connection() as conn:
            prod_count = conn.execute("SELECT COUNT(*) FROM products WHERE id = ?", (prod_id,)).fetchone()[0]
            self.assertEqual(prod_count, 0)
            offer_row = conn.execute("SELECT price FROM offers WHERE product_id = ?", (prod_id,)).fetchone()
            self.assertIsNotNone(offer_row)
            self.assertEqual(offer_row[0], 55000)

        # Мокаем telegram_api и токен бота
        fake_tg_resp = Mock(status_code=200, json=Mock(return_value={"ok": True, "result": {"message_id": 777}}), headers={})
        with patch.object(notifier, "get_bot_token", return_value="fake_token"), \
             patch.object(notifier, "telegram_api", return_value=fake_tg_resp):
            sent = notifier.deliver_pending(limit=10)
            self.assertGreaterEqual(sent, 1)

        # 5. Проверяем статус в notification_outbox — он должен быть 'sent', а не 'cancelled'!
        with get_connection() as conn:
            outbox_row = conn.execute(
                "SELECT status FROM notification_outbox WHERE user_id = ?",
                (self.user_id,)
            ).fetchone()
            self.assertEqual(outbox_row["status"], "sent")

    async def test_telegram_bot_watches_command(self):
        """Проверяем команду /watches и отмену наблюдения через unwatch callback."""
        # Создаем наблюдение
        watch = create_watch(self.user_id, {
            "kind": "product",
            "target": "prod_phone_999",
            "condition": "any_drop",
            "title": "Смартфон Тест 999",
            "repeat": True,
        })
        watch_id = watch["id"]

        session_mock = AsyncMock()
        with patch.object(telegram_bot, "send_tg_message", new=AsyncMock()) as mock_send_msg:
            # Вызываем команду /watches
            await telegram_bot.handle_watches_command(session_mock, "fake_token", self.user_id)
            self.assertTrue(mock_send_msg.called)
            args, kwargs = mock_send_msg.call_args
            text = args[3]
            reply_markup = args[4]
            self.assertIn("Ваши активные наблюдения", text)
            self.assertIn("Смартфон Тест 999", text)
            self.assertIsNotNone(reply_markup)
            inline_buttons = reply_markup.get("inline_keyboard", [])
            has_unwatch_button = any(
                btn.get("callback_data") == f"unwatch:{watch_id}"
                for row in inline_buttons for btn in row
            )
            self.assertTrue(has_unwatch_button)

        # Проверяем обработку unwatch callback_query
        cq_update = {
            "callback_query": {
                "id": "cq_123",
                "from": {"id": self.user_id, "is_bot": False},
                "message": {"chat": {"id": self.user_id, "type": "private"}},
                "data": f"unwatch:{watch_id}",
            }
        }
        with patch.object(telegram_bot, "answer_callback_query", new=AsyncMock()) as mock_ans, \
             patch.object(telegram_bot, "send_tg_message", new=AsyncMock()) as mock_msg:
            await telegram_bot.process_telegram_update(session_mock, "fake_token", cq_update)
            self.assertTrue(mock_ans.called)
            self.assertTrue(mock_msg.called)

        # Проверяем, что в БД наблюдение удалено или не активно
        watches = database.list_watches(self.user_id, only_active=True)
        self.assertEqual(len(watches), 0)


if __name__ == "__main__":
    unittest.main()
