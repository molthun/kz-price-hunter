import unittest
import os
import tempfile

# Тесты работают с временной базой и настройками, не трогая рабочую prices.db / settings.json
_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP_DIR.name

import config

from database import (
    init_db,
    save_or_update_product,
    save_or_update_products_batch,
    get_price_history_batch,
    was_alert_sent_recently,
    record_alert,
    get_connection,
    upsert_telegram_user,
    save_user_settings,
    set_user_blocked,
    create_session,
    get_session_user,
    delete_session,
    get_notification_recipients
)
import hmac
import hashlib
import time
from auth import verify_telegram_auth
from detector import check_anomaly, is_junk_accessory, alert_matches_user, notify_level_allows

# Фиксированные пороги детекции, независимые от пользовательских настроек
TEST_SETTINGS = config.merge_user_settings({})

class TestDNSMonitor(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM products WHERE id LIKE 'test-%' OR id LIKE 'db-test-%'")
            conn.execute("DELETE FROM alerts WHERE product_id LIKE 'test-%' OR product_id LIKE 'db-test-%'")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM users")
            conn.commit()

    def test_junk_accessory_filter(self):
        self.assertTrue(is_junk_accessory("Чехол для Apple iPhone 15 Pro Max"))
        self.assertTrue(is_junk_accessory("Защитная пленка на экран"))
        self.assertTrue(is_junk_accessory("Кабель Type-C USB"))
        self.assertFalse(is_junk_accessory("15.6\" Ноутбук ASUS TUF Gaming"))
        self.assertFalse(is_junk_accessory("Смартфон Xiaomi 14T 512GB"))

    def test_zero_glitch_detection(self):
        """
        Тест кейса пользователя:
        Товар стоил 189 990 ₸, а стал стоить 18 990 ₸ (пропущен ноль при переоценке).
        """
        product = {
            "id": "test-12345",
            "title": "Телевизор Samsung 55\" 4K Smart TV",
            "price": 18990,
            "url": "https://www.dns-shop.kz/product/test-12345/",
            "city": "Астана"
        }
        history = {
            "old_price": 189990,
            "first_seen_price": 189990
        }
        anomaly = check_anomaly(product, history, custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "ZERO_GLITCH")
        self.assertEqual(anomaly["new_price"], 18990)
        self.assertEqual(anomaly["old_price"], 189990)
        self.assertGreater(anomaly["savings"], 170000)

    def test_super_discount_detection(self):
        """Тест глубокой скидки (обвал цены на 70%)."""
        product = {
            "id": "test-67890",
            "title": "Ноутбук Lenovo IdeaPad 15",
            "price": 60000,
            "url": "https://www.dns-shop.kz/product/test-67890/",
            "city": "Астана"
        }
        history = {
            "old_price": 200000,
            "first_seen_price": 200000
        }
        anomaly = check_anomaly(product, history, custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "SUPER_DISCOUNT")
        self.assertEqual(anomaly["drop_pct"], 70.0)

    def test_database_and_alert_dedup(self):
        """Тест сохранения в базу данных и защиты от повторных алертов."""
        p_data = {
            "id": "db-test-1",
            "title": "Видеокарта GeForce RTX 4070",
            "category": "Видеокарты",
            "url": "https://www.dns-shop.kz/product/db-test-1/",
            "image_url": "https://c.dns-shop.kz/test.jpg",
            "price": 350000
        }
        res1 = save_or_update_product(p_data)
        self.assertTrue(res1["is_new"])

        # Проверяем запись алерта
        self.assertFalse(was_alert_sent_recently("db-test-1", 35000))
        record_alert("db-test-1", "ZERO_GLITCH", 350000, 35000, 90.0, 315000)
        self.assertTrue(was_alert_sent_recently("db-test-1", 35000))

        # Повторное сохранение: товар уже есть, прежняя цена возвращается как история
        res2 = save_or_update_product(dict(p_data, price=35000))
        self.assertFalse(res2["is_new"])
        self.assertEqual(res2["old_price"], 350000)

    def test_batch_history_read_before_overwrite(self):
        """История цен для больших категорий читается до пакетной перезаписи."""
        item = {
            "id": "db-test-batch",
            "title": "Смартфон Apple iPhone 16 Pro",
            "url": "https://shop.kz/offer/db-test-batch/",
            "price": 599990
        }
        save_or_update_products_batch([item])
        history = get_price_history_batch(["db-test-batch", "db-test-missing"])
        save_or_update_products_batch([dict(item, price=59999)])

        self.assertNotIn("db-test-missing", history)
        self.assertEqual(history["db-test-batch"]["old_price"], 599990)
        anomaly = check_anomaly(dict(item, price=59999), history["db-test-batch"], custom_settings=TEST_SETTINGS)
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "ZERO_GLITCH")

    def test_settings_validation(self):
        """Личные настройки: ноль допустим, неизвестные и системные ключи отбрасываются, некорректные значения отклоняются."""
        clean = config.validate_user_settings({
            "min_item_price_kzt": "0",
            "price_glitch_drop_pct": "70.5",
            "unknown_key": 1,
            "scan_interval_minutes": 5,
            "alert_shops": {"dns": False, "unknown_shop": True}
        })
        self.assertEqual(clean["min_item_price_kzt"], 0)
        self.assertEqual(clean["price_glitch_drop_pct"], 70.5)
        self.assertNotIn("unknown_key", clean)
        self.assertNotIn("scan_interval_minutes", clean)
        self.assertEqual(clean["alert_shops"], {"dns": False})

        with self.assertRaises(ValueError):
            config.validate_user_settings({"min_item_price_kzt": -1})
        with self.assertRaises(ValueError):
            config.validate_user_settings({"detect_zero_glitch": "yes"})
        with self.assertRaises(ValueError):
            config.validate_user_settings({"telegram_notify_level": "EVERYTHING"})

        # Системные настройки не принимают личные ключи
        self.assertEqual(config._validate_settings({"min_item_price_kzt": 1, "candidate_drop_pct": 40}), {"candidate_drop_pct": 40.0})

    def test_scan_interval_range(self):
        """Интервал автообновления: минуты/часы/дни в пределах 5 минут — 30 дней."""
        self.assertEqual(config._validate_settings({"scan_interval_minutes": 3 * 1440})["scan_interval_minutes"], 4320)
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": 4})
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": 31 * 1440})
        with self.assertRaises(ValueError):
            config._validate_settings({"scan_interval_minutes": None})
        self.assertEqual(config.get_scan_interval_seconds({"scan_interval_minutes": 1}), 300)
        self.assertEqual(config.get_scan_interval_seconds({"scan_interval_minutes": 120}), 7200)

    def test_telegram_auth_signature(self):
        """Подпись Telegram Login Widget: верная принимается, подделка и устаревшие данные отклоняются."""
        token = "123456:TEST-TOKEN"
        data = {"id": 42, "first_name": "Иван", "username": "ivan", "auth_date": int(time.time())}
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret = hashlib.sha256(token.encode()).digest()
        signed = dict(data, hash=hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest())

        self.assertEqual(int(verify_telegram_auth(signed, token)["id"]), 42)
        with self.assertRaises(ValueError):
            verify_telegram_auth(dict(signed, id=43), token)
        with self.assertRaises(ValueError):
            verify_telegram_auth(signed, "999:OTHER")

        old = dict(data, auth_date=int(time.time()) - 2 * 86400)
        old_check = "\n".join(f"{k}={old[k]}" for k in sorted(old))
        old["hash"] = hmac.new(secret, old_check.encode(), hashlib.sha256).hexdigest()
        with self.assertRaises(ValueError):
            verify_telegram_auth(old, token)

    def test_user_sessions_and_blocking(self):
        """Сессия находит пользователя; блокировка и выход завершают ее."""
        user = upsert_telegram_user({"id": 777001, "username": "tester", "first_name": "Тест"})
        self.assertFalse(user["is_blocked"])
        self.assertEqual(user["settings"]["min_item_price_kzt"], config.USER_DEFAULTS["min_item_price_kzt"])

        token = create_session(777001)
        self.assertEqual(get_session_user(token)["id"], 777001)
        self.assertIsNone(get_session_user("wrong-token"))

        save_user_settings(777001, {"telegram_notify_enabled": True})
        self.assertIn(777001, [u["id"] for u in get_notification_recipients()])

        set_user_blocked(777001, True)
        self.assertIsNone(get_session_user(token))
        self.assertNotIn(777001, [u["id"] for u in get_notification_recipients()])

        set_user_blocked(777001, False)
        token2 = create_session(777001)
        delete_session(token2)
        self.assertIsNone(get_session_user(token2))

    def test_alert_filtering_by_user_thresholds(self):
        """Кандидат, записанный по мягким порогам, виден только пользователям, чьи личные пороги он проходит."""
        alert = {
            "alert_type": "SUPER_DISCOUNT", "new_price": 50000, "discount_pct": 50.0, "savings_kzt": 50000,
            "shop": "Sulpak", "title": "Ноутбук ASUS", "category": "Ноутбуки", "url": "https://sulpak.kz/x"
        }
        strict = config.merge_user_settings({"price_glitch_drop_pct": 65})
        relaxed = config.merge_user_settings({"price_glitch_drop_pct": 40})
        self.assertFalse(alert_matches_user(alert, strict))
        self.assertTrue(alert_matches_user(alert, relaxed))

        no_sulpak = config.merge_user_settings({"price_glitch_drop_pct": 40, "alert_shops": {"sulpak": False}})
        self.assertFalse(alert_matches_user(alert, no_sulpak))

        used = dict(alert, title="Ноутбук ASUS (уцененный товар)")
        self.assertFalse(alert_matches_user(used, relaxed))

        glitch = {"type": "ZERO_GLITCH", "drop_pct": 90, "savings": 170000}
        big = {"type": "SUPER_DISCOUNT", "drop_pct": 50, "savings": 150000}
        self.assertTrue(notify_level_allows(glitch, "CRITICAL_ONLY"))
        self.assertFalse(notify_level_allows(big, "CRITICAL_ONLY"))
        self.assertTrue(notify_level_allows(big, "HIGH_SAVINGS"))

    def test_candidate_settings_are_relaxed(self):
        """Кандидаты записываются по мягким системным порогам, а не по личным."""
        product = {"id": "test-cand", "title": "Смартфон Samsung Galaxy", "price": 60000, "url": "https://x", "city": "Астана"}
        history = {"old_price": 100000, "first_seen_price": 100000}  # скидка 40% — ниже личного порога 65%
        self.assertIsNone(check_anomaly(product, history, custom_settings=TEST_SETTINGS))
        anomaly = check_anomaly(product, history, custom_settings=config.get_candidate_settings())
        self.assertIsNotNone(anomaly)
        self.assertEqual(anomaly["type"], "SUPER_DISCOUNT")

if __name__ == "__main__":
    unittest.main()
