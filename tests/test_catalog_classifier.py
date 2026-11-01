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

    def test_accessory_and_consumable_classification(self):
        cases = [
            ("Чехол Baseus Simple Series для Apple iPhone 15 Pro черный", "phone_cases", "accessory", "Baseus"),
            ("Защитное стекло Remax для Samsung Galaxy S24 Ultra", "phone_glass", "accessory", "Remax"),
            ("Кабель Baseus Type-C to Lightning 20W 1m", "cables_chargers", "accessory", "Baseus"),
            ("HEPA фильтр Filtero для пылесоса Dyson V15 Detect", "vacuum_parts", "consumable", "Filtero"),
            ("Картридж Cactus CS-CF217A для HP LaserJet Pro M102", "printer_ink", "consumable", "Cactus"),
            # Generic accessory with target brand in title must NOT be attributed to device manufacturer
            ("Чехол для Apple iPhone 15 силиконовый прозрачный", "phone_cases", "accessory", None),
        ]
        for title, expected_cat, expected_kind, expected_brand in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res)
                self.assertEqual(res.category_id, expected_cat)
                self.assertEqual(res.item_kind, expected_kind)
                self.assertEqual(res.brand, expected_brand, f"Brand mismatch for {title}: got {res.brand}, expected {expected_brand}")

    def test_clean_model_extraction(self):
        cases = [
            ("Смартфон Apple iPhone 15 Pro Max 256GB Natural Titanium (A3106)", "Apple", "iPhone 15 Pro Max"),
            ("Смартфон Samsung Galaxy S24 Ultra 12/512GB Серый титан", "Samsung", "Galaxy S24 Ultra"),
            ("Смартфон Xiaomi Redmi Note 13 Pro 8/256GB Черный", "Xiaomi", "Redmi Note 13 Pro"),
            ("Пылесос беспроводной Dyson V15 Detect Absolute желтый/никель", "Dyson", "V15 Detect"),
            ("Игровая консоль Sony PlayStation 5 Slim 1TB с дисководом", "Sony", "PlayStation 5 Slim"),
            ("Видеокарта Palit GeForce RTX 4070 Dual 12GB", "Palit", "GeForce RTX 4070"),
        ]
        for title, expected_brand, expected_model in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res)
                self.assertEqual(res.brand, expected_brand)
                self.assertEqual(res.model, expected_model, f"Model mismatch for {title}: got {res.model}, expected {expected_model}")


    def test_apple_standardized_naming(self):
        cases = [
            # iPhone generations and canonical casing
            ("Смартфон Apple iPhone 12 Pro Max, 256GB, Gold (MGDE3)", "phones", "device", "Apple", "iPhone 12 Pro Max"),
            ("Смартфон Apple iPhone Air, 256 GB, Space Black (MG2L4HX/A)", "phones", "device", "Apple", "iPhone Air"),
            ("Смартфон Apple iPhone Air, 256 GB, Sky Blue (MG2P4HX/A)", "phones", "device", "Apple", "iPhone Air"),
            ("Смартфон APPLE iPhone DUO 256GB (Star White)", "phones", "device", "Apple", "iPhone Duo"),
            ("iPhone 17 Pro Max 256Gb", "phones", "device", "Apple", "iPhone 17 Pro Max"),
            ("iPhone 18 PRO 1TB (Glacier)", "phones", "device", "Apple", "iPhone 18 Pro"),
            ("iPhone 18 PRO MAX 2TB (Burgundy)", "phones", "device", "Apple", "iPhone 18 Pro Max"),
            ("iPhone 16 Plus 128GB Pink", "phones", "device", "Apple", "iPhone 16 Plus"),
            ("iPhone 7 Rose", "phones", "device", "Apple", "iPhone 7"),
            ("iPhone 7 Plus Jet", "phones", "device", "Apple", "iPhone 7 Plus"),
            ("iPhone 6S", "phones", "device", "Apple", "iPhone 6s"),
            ("Смартфон Apple iPhone 16e 128Gb White + Адаптер питания Apple USB-C 20W", "phones", "device", "Apple", "iPhone 16e"),
            ("iPhone 13 mini 128GB Midnight", "phones", "device", "Apple", "iPhone 13 mini"),
            ("iPhone XS Max 64GB Gold", "phones", "device", "Apple", "iPhone XS Max"),
            ("iPhone SE 2022 64GB Starlight", "phones", "device", "Apple", "iPhone SE"),
            # Bundle gifts must NOT hijack primary device model
            ("Смартфон Apple iPhone 17 Pro Max 2Tb Silver + Наушники Apple AirPods 4 Белый + Адаптер Apple Power Adapter", "phones", "device", "Apple", "iPhone 17 Pro Max"),
            # Apple Watch with Aluminium Case must NOT be classified as phone case!
            ("Смарт-часы Apple Watch SE GPS 40mm Starlight Al Case with Starlight Sport Band - M/L (MXEG3QI/A)", "smartwatches", "device", "Apple", "Apple Watch SE"),
            ("Смарт-Часы Apple Watch Series 10 GPS 42mm Jet Black Aluminium Case with Black Sport Band - S/M", "smartwatches", "device", "Apple", "Apple Watch Series 10"),
            ("Смарт-часы Apple Watch Ultra 2 GPS + Cellular 49mm Titanium Case with Orange Ocean Band", "smartwatches", "device", "Apple", "Apple Watch Ultra 2"),
            # Accessory cases must NOT take Apple as brand or phone as model
            ("Чехол для Apple iPhone 17 Pro Max силикон, 1 шт.", "phone_cases", "accessory", None, None),
            ("Защитное Full Cover 9H стекло для iPhone 14 Pro", "phone_glass", "accessory", None, None),
            ("Адаптер для Iphone 12 13 14 15 20W", "cables_chargers", "accessory", None, None),
            ("Беспроводные наушники для iPhone TWS Pro", "headphones", "device", None, None),
            # Mac & MacBook canonical standardization
            ("Ноутбук Apple MacBook Air A2337 с дисплеем Retina (Z1240004J)", "laptops", "device", "Apple", "MacBook Air 13"),
            ("Ноутбук Apple MacBook Air A2941 с дисплеем Retina Liquid (MQKW3)", "laptops", "device", "Apple", "MacBook Air 15"),
            ("Ноутбук APPLE MacBook Neo 13 2026 (MHFG4)/13/Apple A18 Pro 6-core/8/512/5-Core/MacOS/Indigo", "laptops", "device", "Apple", "MacBook Neo 13"),
            ("Ноутбук Apple MacBook Pro 14 M3 Pro 18/512GB Space Black", "laptops", "device", "Apple", "MacBook Pro 14"),
            ("Ноутбук Apple 16-inch MacBook Pro M3 Max 36/1TB Silver", "laptops", "device", "Apple", "MacBook Pro 16"),
            ("Моноблок 24 inch iMac M3 8/256GB Blue", "pc_desktops", "device", "Apple", "iMac 24"),
            # iPad canonical standardization
            ("iPad 11 (A16) 128Gb WiFi", "tablets", "device", "Apple", "iPad 11"),
            ("Планшет Apple iPad Air 11 M4 128Gb WiFi", "tablets", "device", "Apple", "iPad Air 11"),
            ("Планшет APPLE 13-inch iPad Pro WiFi 256GB with Standard glass", "tablets", "device", "Apple", "iPad Pro 13"),
            ("Планшет Apple iPad mini 6 (2021) 3/64GB Wi-Fi Purple", "tablets", "device", "Apple", "iPad mini"),
            ("Планшет Apple iPad 10.2″ (2021) 3/64GB Wi-Fi Space Grey", "tablets", "device", "Apple", "iPad 9"),
            # Audio (AirPods & EarPods)
            ("Наушники Apple AirPods Max Silver", "headphones", "device", "Apple", "AirPods Max"),
            ("Наушники Apple AirPods Pro 2 USB-C White", "headphones", "device", "Apple", "AirPods Pro 2"),
            ("Наушники Apple EarPods with Lightning Connector белый", "headphones", "device", "Apple", "EarPods"),
            # Displays
            ("27\" Studio Display XDR Standard glass", "monitors", "device", "Apple", "Studio Display"),
            # Watch Nike edition
            ("Часы Watch Nike Series 7 45mm", "smartwatches", "device", "Apple", "Apple Watch Series 7"),
            # False Apple brand exclusions (food, cosmetics, gardening, cookware)
            ("Конфеты Qazaq Apple красное яблоко, 108 г", "food", "device", None, "Конфеты Qazaq Apple красное"),
            ("Тройник для воды штуцерный Green Apple GWHC20-062", "home", "device", "Green Apple", "Тройник"),
            ("Кастрюля Lara Apple LR02-31, 16 см, 2.1 л., с крышкой", "kitchen_appliances", "device", "Lara", "Apple LR02 31"),
        ]
        for title, exp_cat, exp_kind, exp_brand, exp_model in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res)
                self.assertEqual(res.category_id, exp_cat, f"Category mismatch for '{title}': got {res.category_id}, expected {exp_cat}")
                self.assertEqual(res.item_kind, exp_kind, f"Kind mismatch for '{title}': got {res.item_kind}, expected {exp_kind}")
                self.assertEqual(res.brand, exp_brand, f"Brand mismatch for '{title}': got {res.brand}, expected {exp_brand}")
                self.assertEqual(res.model, exp_model, f"Model mismatch for '{title}': got {res.model}, expected {exp_model}")


if __name__ == "__main__":
    unittest.main()
