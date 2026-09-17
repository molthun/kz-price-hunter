import unittest
import os
from database import (
    init_db,
    save_or_update_product,
    was_alert_sent_recently,
    record_alert,
    get_connection
)
from detector import check_anomaly, is_junk_accessory

class TestDNSMonitor(unittest.TestCase):
    def setUp(self):
        init_db()
        with get_connection() as conn:
            conn.execute("DELETE FROM products WHERE id LIKE 'test-%' OR id LIKE 'db-test-%'")
            conn.execute("DELETE FROM alerts WHERE product_id LIKE 'test-%' OR product_id LIKE 'db-test-%'")
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
        anomaly = check_anomaly(product, history)
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
        anomaly = check_anomaly(product, history)
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

if __name__ == "__main__":
    unittest.main()
