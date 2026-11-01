"""
Tests for Catalog Classifier & Normalizer (Rules + Local AI).
"""
import json
import unittest
from unittest.mock import patch, MagicMock

from catalog_classifier import CatalogClassifier, KNOWN_BRANDS


class TestCatalogClassifier(unittest.TestCase):
    def setUp(self):
        self.classifier = CatalogClassifier()

    def test_brand_extraction_major_brands(self):
        cases = [
            ("Смартфон Apple iPhone 15 Pro 128GB Natural Titanium", "Apple"),
            ("Смартфон Samsung Galaxy S24 Ultra 12/256GB Titanium Gray", "Samsung"),
            ("Смартфон Xiaomi Redmi Note 13 8/256GB Midnight Black", "Xiaomi"),
            ("Смартфон Honor X9b 12/256GB Sunrise Orange", "Honor"),
            ("Смартфон OnePlus 12 16/512GB Silky Black", "OnePlus"),
            ("Смартфон Tecno Spark 20 Pro 8/256GB Moonlit Black", "Tecno"),
            ("Ноутбук ASUS ROG Strix G16 G614JVR-N4003", "ASUS"),
            ("Ноутбук Acer Predator Helios Neo 16 PHN16-71", "Acer"),
            ("Ноутбук Lenovo Legion Pro 5 16IRX8", "Lenovo"),
            ("Стиральная машина LG F2 V5HS5W", "LG"),
            ("Стиральная машина Bosch WAN28200ME", "Bosch"),
            ("Пылесос Dyson V15 Detect Absolute", "Dyson"),
            ("Робот-пылесос Roborock S8 Pro Ultra", "Roborock"),
            ("Телевизор TCL 55C745 4K QLED", "TCL"),
            ("Беспроводные наушники Marshall Major IV Black", "Marshall"),
        ]
        for title, expected_brand in cases:
            with self.subTest(title=title):
                brand = self.classifier.extract_brand(title)
                self.assertEqual(brand, expected_brand, f"Failed to extract {expected_brand} from '{title}'")

    def test_category_mapping_from_store_categories(self):
        cases = [
            ("Forte: 📱 Смартфоны", "phones", "Смартфоны"),
            ("Halyk: Ноутбуки", "laptops", "Ноутбуки"),
            ("Мечта: 🧺 Стиральные машины", "washing_machines", "Стиральные машины"),
            ("ITMag: 🖥 Мониторы", "monitors", "Мониторы"),
            ("Forte: 🧹 Пылесосы", "vacuums", "Пылесосы"),
            ("Halyk: Телевизоры", "tv", "Телевизоры"),
            ("Forte: 🎧 Наушники и гарнитуры", "headphones", "Наушники"),
            ("Forte: ⌚️ Смарт-часы", "smartwatches", "Смарт-часы"),
        ]
        for raw_cat, expected_id, expected_name in cases:
            with self.subTest(raw_cat=raw_cat):
                res = self.classifier.classify("Товар тестовый", raw_category=raw_cat)
                self.assertEqual(res.category_id, expected_id)
                self.assertEqual(res.category_name, expected_name)

    def test_category_and_brand_extraction_from_title_when_category_is_fuzzy(self):
        # Even if raw category is "Flip: Электроника" or "Sulpak: 🔥 Распродажа",
        # the title keywords must accurately classify into specific category
        cases = [
            (
                "Смартфон Xiaomi Redmi 10 4/128GB Sea Blue",
                "Flip: Электроника",
                "phones",
                "Xiaomi"
            ),
            (
                "Ноутбук Dell XPS 13 9300 Intel Core i7",
                "Sulpak: 🔥 Распродажа",
                "laptops",
                "Dell"
            ),
            (
                "Стиральная машина LG F2 Y1NS3W 6 кг",
                "Каталог товаров",
                "washing_machines",
                "LG"
            ),
            (
                "Робот-пылесос Scarlett SC-VC80R10 Black",
                "",
                "vacuums",
                "Scarlett"
            ),
            (
                "Монитор 27\" ASUS ROG Swift PG278QR Black 165Hz",
                "Flip: Электроника",
                "monitors",
                "ASUS"
            ),
        ]
        for title, raw_cat, expected_cat, expected_brand in cases:
            with self.subTest(title=title):
                res = self.classifier.classify(title, raw_category=raw_cat)
                self.assertEqual(res.category_id, expected_cat)
                self.assertEqual(res.brand, expected_brand)

    def test_local_ai_fallback_called_when_rules_uncertain(self):
        fake_response = '{"brand": "CustomBrand", "category_id": "phones"}'
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps({"response": fake_response}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = self.classifier.classify_with_local_ai("Необычное устройство коммуникации Alpha", "Разное")
            self.assertIsNotNone(res)
            self.assertEqual(res.brand, "CustomBrand")
            self.assertEqual(res.category_id, "phones")
            self.assertEqual(res.source, "local_ai")


if __name__ == "__main__":
    unittest.main()
