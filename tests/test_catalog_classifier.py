"""
Tests for Catalog Classifier & Normalizer (Rules + Local AI).
"""
from tests import test_support  # noqa: F401
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
            (
                "Настольная посудомоечная машина Candy CP 6F51LS-08",
                "Sulpak: 🔥 Распродажа",
                "dishwashers",
                "Candy"
            ),
            (
                "Встраиваемая посудомоечная машина Bosch SMV25AX00E",
                "Halyk: Бытовая техника",
                "dishwashers",
                "Bosch"
            ),
            (
                "Вакууматор Kitfort KT-1503-2",
                "Flip: Техника для кухни",
                "kitchen_appliances",
                "Kitfort"
            ),
            (
                "Шина для пил Greenworks 30см",
                "Kaspi: Садовая техника",
                "tools_repair",
                "Greenworks"
            ),
            (
                "Автошина Pirelli Ice Zero 2 225/45 R17 94H шипованная",
                "Sulpak: Автотовары",
                "tires",
                "Pirelli"
            ),
            (
                "Шина летняя Bridgestone Turanza T005 205/55 R16 91V",
                "Mechta: Авто",
                "tires",
                "Bridgestone"
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
            ("Капсулы для посудомоечных машин FAIRY Platinum Plus All in 1, лимон 48 шт", "household_chem", "consumable", "Fairy"),
            ("Таблетки для посудомоечной машины Finish Power 100 шт", "household_chem", "consumable", "Finish"),
            ("Соль для посудомоечных машин Finish 3 кг", "household_chem", "consumable", "Finish"),
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
            # False Apple brand exclusions (food, cosmetics, gardening, cookware, footwear)
            ("Конфеты Qazaq Apple красное яблоко, 108 г", "food", "device", None, "Конфеты Qazaq Apple яблоко"),
            ("Тройник для воды штуцерный Green Apple GWHC20-062", "home", "device", "Green Apple", "Тройник"),
            ("Кастрюля Lara Apple LR02-31, 16 см, 2.1 л., с крышкой", "kitchenware", "device", "Lara", "Apple LR02 31"),
            ("Кроссовки Imac", "shoes", "device", "IMAC", "Кроссовки"),
            ("MAC Studio Fix Pro Set + Blur Loose Powder Пудра", "beauty", "device", "M·A·C", "MAC Studio Fix Pro"),
            # Apple Watch edge cases (Nike, no-space GPS, part numbers)
            ("Смарт-часы Apple Watch Nike SE 44mm Nike Sport Band", "smartwatches", "device", "Apple", "Apple Watch SE"),
            ("Смарт часы Apple Watch Series 11GPS42mmJet Black", "smartwatches", "device", "Apple", "Apple Watch Series 11"),
            ("Смарт-Часы Apple Watch 3N492RU/A Series 10 GPS 42mm", "smartwatches", "device", "Apple", "Apple Watch Series 10"),
            # Third-party watch charger & case brand isolation
            ("Беспроводное зарядное устройство для Apple Watch Ugreen 50944", "cables_chargers", "accessory", "Ugreen", None),
            ("Чехол с цветными бортами IPhone 14 Pro Max Style Shield", "phone_cases", "accessory", "Style Shield", None),
            # Input devices and audio
            ("Клавиатура Apple Keyboard with Touch ID (MXCK3RS/A)", "peripherals", "device", "Apple", "Magic Keyboard"),
            ("Apple Magic Mouse 4 (White)", "peripherals", "device", "Apple", "Magic Mouse"),
            ("Наушники Apple MYQY3ZM/A Белый", "headphones", "device", "Apple", "EarPods"),
            ("Ноутбук Apple MacBook 12 MNYM2RU/A Rose Gold", "laptops", "device", "Apple", "MacBook 12"),
            ("Поисковый трекер для устройств Apple «MVTAG» пластик, 4×3.5×1 см", "electronics", "device", "MVTAG", "Поисковый трекер"),
            ("Наушники ViTi WSS-Iphone 07 Белые", "headphones", "device", "ViTi", "WSS Iphone 07"),
        ]
        for title, exp_cat, exp_kind, exp_brand, exp_model in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res)
                self.assertEqual(res.category_id, exp_cat, f"Category mismatch for '{title}': got {res.category_id}, expected {exp_cat}")
                self.assertEqual(res.item_kind, exp_kind, f"Kind mismatch for '{title}': got {res.item_kind}, expected {exp_kind}")
                self.assertEqual(res.brand, exp_brand, f"Brand mismatch for '{title}': got {res.brand}, expected {exp_brand}")
                self.assertEqual(res.model, exp_model, f"Model mismatch for '{title}': got {res.model}, expected {exp_model}")

    def test_baby_goods_classification(self):
        """Проверяет классификацию товаров для малышей по категориям в стиле Детский мир и Kaspi."""
        cases = [
            ("Подгузники Pampers Premium Care 4 (9-14 кг) 104 шт", "diapers_hygiene", "Pampers"),
            ("Детская молочная смесь NAN 1 Optipro 800 г", "baby_food", "NAN"),
            ("Каша молочная овсяная ФрутоНяня 200 г", "baby_food", "ФрутоНяня"),
            ("Бутылочка для кормления Philips Avent Natural 260 мл", "baby_feeding", "Philips Avent"),
            ("Пустышка силиконовая Chicco Physio Soft 0-6 мес", "baby_feeding", "Chicco"),
            ("Детская ванночка для купания со сливом", "baby_bath_care", None),
            ("Детский крем под подгузник Bubchen 150 мл", "baby_bath_care", "Bubchen"),
            ("Детская кроватка с маятником и ящиком", "nursery_sleep", None),
            ("Прогулочная коляска Cybex Balios S Lux", "strollers", "Cybex"),
            ("Погремушка-прорезыватель Tiny Love Олененок", "baby_toys", "Tiny Love"),
        ]
        for title, exp_cat, exp_brand in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res, f"Не удалось классифицировать '{title}'")
                self.assertEqual(res.category_id, exp_cat, f"Категория не совпала для '{title}': получили {res.category_id}, ожидали {exp_cat}")
                if exp_brand:
                    self.assertEqual(res.brand, exp_brand, f"Бренд не совпал для '{title}': получили {res.brand}, ожидали {exp_brand}")

    def test_diaper_pants_brands_and_series(self):
        """Проверяет классификацию трусиков-подгузников, распознавание брендов и извлечение серий."""
        cases = [
            # Title, expected category, expected brand, expected clean series/model
            ("Трусики Joonies Premium Soft M (6-11 кг) 58 шт.", "diapers_hygiene", "Joonies", "Premium Soft"),
            ("Трусики Joonies Marshmallow XL (12-17 кг) 36 шт.", "diapers_hygiene", "Joonies", "Marshmallow"),
            ("Трусики Joonies Magic Night (ночные) M, 24 шт", "diapers_hygiene", "Joonies", "Magic Night"),
            ("Huggies трусики 6 (16-22 кг) для девочек № 44 шт", "diapers_hygiene", "Huggies", None),
            ("Huggies подгузники Ultra Comfort 4 (8-14кг) для мальчиков № 19 шт", "diapers_hygiene", "Huggies", "Ultra Comfort"),
            ("Huggies подгузники Elite Soft 2 (4-6 кг) 82 шт", "diapers_hygiene", "Huggies", "Elite Soft"),
            ("Подгузники-трусики Pampers Jumbo Pack 5 12-17 кг 42 шт", "diapers_hygiene", "Pampers", "Jumbo Pack"),
            ("Подгузники Sachiko L 50 шт", "diapers_hygiene", "Sachiko", None),
            ("Подгузники Emiko Premium Soft M (6-11 кг) 64 шт.", "diapers_hygiene", "Emiko", "Premium Soft"),
            ("Подгузники-трусики Мама Знает Стандарт XXL, 6, 40 шт", "diapers_hygiene", "Мама Знает", "Стандарт"),
            ("Подгузники-трусики Manu 5 (12-17 кг) 40 шт.", "diapers_hygiene", "MANU", None),
            ("YokoSun Подгузники Premium NB (0-5 кг) 36 шт", "diapers_hygiene", "YokoSun", "Premium"),
            # Защита: женские менструальные трусики уходят в beauty, не в подгузники
            ("Менструальные трусики Joonies гигиенические для женщин, M/L 60-100 см, 3 шт", "beauty", "Joonies", None),
            # Защита: детский набор белья уходит в shoes/clothes, не в подгузники
            ("kari Набор лиф и трусики детский для девочек A99464-2", "shoes", None, None),
        ]
        for title, exp_cat, exp_brand, exp_model in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res, f"Не удалось классифицировать '{title}'")
                self.assertEqual(res.category_id, exp_cat, f"Категория не совпала для '{title}': got {res.category_id}, exp {exp_cat}")
                self.assertEqual(res.brand, exp_brand, f"Бренд не совпал для '{title}': got {res.brand}, exp {exp_brand}")
                self.assertEqual(res.model, exp_model, f"Модель не совпала для '{title}': got {res.model}, exp {exp_model}")

    def test_telephony_and_office_phones(self):
        """Проверяет классификацию IP-телефонов, радиотелефонов и стационарных телефонов в категорию telephony."""
        cases = [
            ("IP-телефон Grandstream GRP2604P", "Телефоны", "telephony", "Grandstream", "GRP2604P"),
            ("Телефон Panasonic KX-TS2350RU, White", "Телефоны", "telephony", "Panasonic", "KX TS2350RU"),
            ("Радио-телефон Panasonic KX-TG1711CAB, Black", "Телефоны", "telephony", "Panasonic", "KX TG1711CAB"),
            ("IP-телефон Yealink SIP-T31P", "IP-телефоны", "telephony", "Yealink", "SIP T31P"),
            ("Радиотелефон Gigaset A170 Black", "Радиотелефоны", "telephony", "Gigaset", "A170"),
            ("Смартфон Apple iPhone 16 Pro 256Gb", "Смартфоны", "phones", "Apple", "iPhone 16 Pro"),
        ]
        for title, raw_cat, exp_cat, exp_brand, exp_model in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title, raw_category=raw_cat)
                self.assertIsNotNone(res, f"Не удалось классифицировать '{title}'")
                self.assertEqual(res.category_id, exp_cat, f"Категория не совпала для '{title}': got {res.category_id}, exp {exp_cat}")
                self.assertEqual(res.brand, exp_brand, f"Бренд не совпал для '{title}': got {res.brand}, exp {exp_brand}")
                if exp_model:
                    self.assertEqual(res.model, exp_model, f"Модель не совпала для '{title}': got {res.model}, exp {exp_model}")

    def test_vinyl_and_source_category_fallback(self):
        """Проверяет классификацию винила, музыкальных носителей и fallback на категорию источника."""
        # 1. Винил и музыка
        res1 = self.classifier.classify_rules("Beatles Abbey Road LP", raw_category="Меломан: 🎵 Музыка и винил")
        self.assertIsNotNone(res1)
        self.assertEqual(res1.category_id, "vinyl")
        self.assertEqual(res1.brand, "Beatles")
        self.assertEqual(res1.model, "Abbey Road")

        res2 = self.classifier.classify_rules("Pink Floyd The Wall 2LP")
        self.assertIsNotNone(res2)
        self.assertEqual(res2.category_id, "vinyl")
        self.assertEqual(res2.brand, "Pink Floyd")
        self.assertEqual(res2.model, "The Wall")

        # 2. Книги
        res3 = self.classifier.classify_rules("All You Need Is Love. The Beatles: устами очевидцев", raw_category="Меломан: 📚 Книги")
        self.assertIsNotNone(res3)
        self.assertEqual(res3.category_id, "books")

        # 3. Fallback на категорию источника (если категории нет в таксономии - берем категорию источника, а не electronics)
        res4 = self.classifier.classify("Сувенирный магнит Астана", raw_category="Сувениры и подарки")
        self.assertEqual(res4.category_name, "Сувениры и подарки")
        self.assertNotEqual(res4.category_id, "electronics")

    def test_pc_components_classification_and_attributes(self):
        """Проверяет точную классификацию БП, корпусов, охлаждения и исключение ложного винила LP."""
        from domain.attribute_indexing import extract_title_attributes

        # 1. Блоки питания
        psu = self.classifier.classify_rules("Блок питания ATX 850W Asus ROG-THOR-850P")
        self.assertIsNotNone(psu)
        self.assertEqual(psu.category_id, "psu")
        self.assertEqual(psu.brand, "ASUS")
        psu_attrs = extract_title_attributes("Блок питания ATX 850W Asus ROG-THOR-850P")
        self.assertEqual(psu_attrs.get("power"), "850 Вт")
        self.assertEqual(psu_attrs.get("form_factor"), "ATX")
        self.assertNotIn("format", psu_attrs)

        # 2. Корпуса
        pc_case = self.classifier.classify_rules("Корпус Lian Li Lancool 216 RGB Black")
        self.assertIsNotNone(pc_case)
        self.assertEqual(pc_case.category_id, "pc_cases")
        self.assertEqual(pc_case.brand, "Lian Li")

        # 3. Охлаждение: кулер с суффиксом LP (Low Profile) не должен определяться как винил LP!
        cooler = self.classifier.classify_rules("Кулер be quiet! Shadow Rock LP")
        self.assertIsNotNone(cooler)
        self.assertEqual(cooler.category_id, "cooling")
        self.assertEqual(cooler.brand, "be quiet!")
        cooler_attrs = extract_title_attributes("Кулер be quiet! Shadow Rock LP")
        self.assertEqual(cooler_attrs.get("type"), "Воздушный кулер")
        self.assertNotIn("format", cooler_attrs)

        # 4. СЖО
        aio = self.classifier.classify_rules("Система водяного охлаждения Deepcool LS720 ARGB")
        self.assertIsNotNone(aio)
        self.assertEqual(aio.category_id, "cooling")
        self.assertEqual(aio.brand, "DeepCool")
        aio_attrs = extract_title_attributes("Система водяного охлаждения Deepcool LS720 ARGB")
        self.assertEqual(aio_attrs.get("type"), "СЖО (водяное)")

    def test_gpu_canonical_model_extraction(self):
        """Проверяет канонизацию моделей видеокарт: отсутствие производителей и вендоров в названии модели."""
        from catalog_classifier import canonical_gpu_model

        test_cases = [
            ("MSI GeForce RTX 5070 World of Warcraft MIDNIGHT VOID EDITION OC 12 Гб", "MSI", "GeForce RTX 5070"),
            ("Sapphire PULSE AMD Radeon RX 7600 Gaming OC 8GB", "Sapphire", "Radeon RX 7600"),
            ("PowerColor AMD Radeon RX 6600 Fighter AXRX 6600 8GBD6-3DH 8 ГБ", "PowerColor", "Radeon RX 6600"),
            ("Видеокарта ASUS RX 6900 XT TUF Gaming OC, 16GB, Radeon RX 6900 XT", "ASUS", "Radeon RX 6900 XT"),
            ("AFOX GeForce GTX 1650 AF1650-4096D6H4-V2 4 ГБ", "AFOX", "GeForce GTX 1650"),
            ("UDORE GTX1050TI-4GD5", "UDORE", "GeForce GTX 1050 Ti"),
            ("Видеокарта Afox GT 730 GeForce GT 730 4GB", "AFOX", "GeForce GT 730"),
            ("PNY GeForce RTX 5070 12GB Overclocked Triple Fan", "PNY", "GeForce RTX 5070"),
            ("Intel Arc B580 12GB Steel Legend OC", "Intel", "Arc B580"),
            ("ASUS ROG Astral GeForce RTX 5080 White OC Edition 16 Гб", "ASUS", "GeForce RTX 5080"),
            ("Видеокарта Palit GeForce RTX 5060 Ti Dual 16GB", "Palit", "GeForce RTX 5060 Ti"),
        ]

        forbidden_manufacturers = {"amd", "nvidia", "intel", "asus", "msi", "palit", "gigabyte", "udore", "afox", "pny", "sapphire"}

        for title, expected_brand, expected_model in test_cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res)
                self.assertEqual(res.brand, expected_brand, f"Brand mismatch for {title}: got {res.brand}, expected {expected_brand}")
                self.assertEqual(res.model, expected_model, f"Model mismatch for {title}: got {res.model}, expected {expected_model}")

                # Убеждаемся, что в модели нет производителя
                for bad_vendor in forbidden_manufacturers:
                    self.assertNotIn(bad_vendor, res.model.lower().split(), f"Model {res.model} should not contain vendor {bad_vendor}")

    def test_wave2_disambiguation_rules(self):
        cases = [
            ("Планшет Huawei MatePad 12 X PaperMatte 256 Гб, 12 Гб зеленый + клавиатура", "tablets"),
            ("Планшет Huawei MatePad 11.5\" TXZ-W09CK Wi-Fi 8/128GB Keyboard Gray", "tablets"),
            ("10.9\" Планшет Samsung Galaxy Tab S10 FE 5G 128 ГБ серебристый + стилус", "tablets"),
            ("kari Детская игрушка Блендер, свет/звук 6003N", "toys"),
            ("Электрический рубанок Ставр РЭ-110/1500СТ", "tools_repair"),
            ("Бумага для печати 100 листов, A4, 80 г/м²", "books"),
            ("Контроллер PCI to 4xSATA", "pc_components"),
            ("Адаптер ASUS Hyper M.2 x16 Card V2", "pc_components"),
            ("Весы Tefal Premiss PP1400V0, белый", "beauty"),
            ("Турка электрическая Centek CT-1097SS сталь", "kitchen_appliances"),
            ("Минимойка высокого давления KARCHER K 3 Power Control", "tools_repair"),
            ("Микрофон Thronmax M20 Streaming Kit", "audio"),
            ("Корм для кошек с лососем в желе «Felix Аппетитные кусочки», 75 г", "cat_food"),
            ("HD Корм для собак сух.Sensible Франция (утка) 300гр", "dog_food"),
            ("Утюг Tefal Express Steam FV2837E0", "appliances"),
            ("Коляска 3 в 1 для кукол Demi Star 9662G", "toys"),
            ("Коляска прогулочная Cybex Balios S Lux", "strollers"),
            ("ВОДА «БОРЖОМИ» МИНЕРАЛЬНАЯ ГАЗ 750 МЛ ПЭТ", "drinks"),
            ("ПАСТА ЗУБНАЯ «SPLAT» АКТИВ 100 МЛ", "beauty"),
            ("Электробритва Panasonic ES-SL41-S520", "haircare"),
            ("Сумка для ноутбука Tucano 15.6\"", "phone_cases"),
            ("Web-камера Logitech HD C922 Pro Stream", "photo_video"),
            ("Часы настенные 3D Жемчуг d=120 см", "home_textile"),
        ]
        for title, expected_cat_id in cases:
            with self.subTest(title=title):
                res = self.classifier.classify_rules(title)
                self.assertIsNotNone(res, f"Expected rule match for {title}")
                self.assertEqual(res.category_id, expected_cat_id, f"Expected {expected_cat_id} for {title}, got {res.category_id}")

        self.assertEqual(self.classifier.extract_brand("Антивирус Dr. Web Security Space, 12 мес."), "Dr.Web")
        self.assertEqual(self.classifier.extract_brand("ТПУ Бронепленка Devia, глянцевая"), "Devia")
        self.assertEqual(self.classifier.extract_brand("Электросамокат Ninebot KickScooter ES4"), "Ninebot")
        self.assertEqual(self.classifier.extract_brand("Stellox стойка стабилизатора 5672019SX"), "Stellox")

    def test_wave8_attribute_indexing_and_brands(self):
        from domain.attribute_indexing import extract_title_attributes

        # 1. Мебель (Mebel.kz cabinets/beds, gaming chairs)
        attrs_cab = extract_title_attributes("Кимбол 3-150x210 Белый/Черный")
        self.assertEqual(attrs_cab.get("type"), "Шкаф / Комод / Тумба")
        self.assertEqual(attrs_cab.get("bed_size"), "150x210 см")

        attrs_bed = extract_title_attributes("Шелтон 160 Латте")
        self.assertEqual(attrs_bed.get("type"), "Кровать / Матрас")
        self.assertEqual(attrs_bed.get("bed_size"), "160x200 см")

        attrs_chair = extract_title_attributes("Игровое компьютерное кресло E-BLUE Cobra")
        self.assertEqual(attrs_chair.get("type"), "Игровое / Компьютерное кресло")

        # 2. Аудиотехника (plural Колонки)
        attrs_spk = extract_title_attributes("Колонки Microlab B56 (2.0), Black, USB")
        self.assertEqual(attrs_spk.get("type"), "Стационарная / Компьютерная акустика")
        self.assertEqual(attrs_spk.get("wireless"), "Нет")

        # 3. Зоотовары (Корм ... для взрослых кошек)
        attrs_cat = extract_title_attributes("Беззерновой корм Farmina N&D Prime для взрослых кошек, 1.5 кг")
        self.assertEqual(attrs_cat.get("type"), "Сухой корм")
        self.assertEqual(attrs_cat.get("pet_type"), "Для кошек")
        self.assertEqual(attrs_cat.get("pet_age"), "Для взрослых (Adult)")
        self.assertEqual(attrs_cat.get("pack_weight"), "1.5 кг")

        # 4. Лекарства и витамины (Europharma trailing dosage forms)
        attrs_med = extract_title_attributes("АЦЦ Хот Дринк 3 гр № 6 пакетики")
        self.assertEqual(attrs_med.get("type"), "Порошки и гранулы (саше)")
        self.assertEqual(attrs_med.get("pieces_count"), "6 шт.")

        attrs_oint = extract_title_attributes("Капсикам д/наруж применения 30 гр мазь")
        self.assertEqual(attrs_oint.get("type"), "Мази, гели и суппозитории")

        # 5. Электроника (Smart Home & Laptop stands)
        attrs_stand = extract_title_attributes("Подставка для ноутбука Eco Estelar LPD01-12, Silver")
        self.assertEqual(attrs_stand.get("type"), "Подставка / Держатель / Кронштейн")

        attrs_hub = extract_title_attributes("Центр управления Xiaomi Smart Home Hub 2 ZNDMWG04LM")
        self.assertEqual(attrs_hub.get("type"), "Умный дом и смарт-освещение")

        # 6. Книги на казахском языке
        attrs_book = extract_title_attributes("Мұртаза Ш.: Қызыл жебе. 1-кітап")
        self.assertEqual(attrs_book.get("type"), "Книги и учебная литература")
        self.assertEqual(attrs_book.get("author"), "Мұртаза Ш.")

    def test_wave9_attribute_indexing_and_drift_prevention(self):
        from domain.attribute_indexing import extract_title_attributes
        from domain.feed_categories import section_category

        # 1. Защита от дрейфа категорий: промо-акции и слишком общие разделы отфильтровываются (None), конкретные секции мапятся
        self.assertIsNone(section_category("Распродажа"))
        self.assertIsNone(section_category("Все акции и скидки"))
        self.assertIsNone(section_category("Электроника"))
        self.assertEqual(section_category("перфораторы"), "tools_repair")
        self.assertEqual(section_category("внешние аккумуляторы"), "cables_chargers")

        # 2. Материнские платы (B550, AM4, ATX)
        attrs_mb = extract_title_attributes("Материнская плата Gigabyte B550 AORUS ELITE V2")
        self.assertEqual(attrs_mb.get("chipset"), "B550")
        self.assertEqual(attrs_mb.get("socket"), "AM4")

        # 3. Процессоры без слова 'процессор'
        attrs_cpu = extract_title_attributes("AMD Ryzen 7 7800X3D OEM")
        self.assertEqual(attrs_cpu.get("socket"), "AM5")
        self.assertEqual(attrs_cpu.get("cpu_family"), "AMD Ryzen 7")

        # 4. Видеокарты (8GD суффикс)
        attrs_gpu = extract_title_attributes("Видеокарта Gigabyte GeForce RTX 4060 EAGLE OC 8GD")
        self.assertEqual(attrs_gpu.get("chipset"), "GeForce RTX 4060")
        self.assertEqual(attrs_gpu.get("vram"), "8 ГБ")

        # 5. Блоки питания (PF750)
        attrs_psu = extract_title_attributes("Блок питания Deepcool PF750L 750W")
        self.assertEqual(attrs_psu.get("power"), "750 Вт")

        # 6. Жесткие диски HDD
        attrs_hdd = extract_title_attributes("Жесткий диск Seagate Barracuda 2TB 3.5 7200rpm")
        self.assertEqual(attrs_hdd.get("storage"), "2 ТБ")
        self.assertEqual(attrs_hdd.get("form_factor"), '3.5"')
        self.assertEqual(attrs_hdd.get("type"), "Внутренний жесткий диск (HDD)")

        # 7. Товары для малышей
        attrs_baby = extract_title_attributes("Смесь молочная с 6 месяцев 400 г")
        self.assertEqual(attrs_baby.get("type"), "Молочные смеси")
        self.assertEqual(attrs_baby.get("age_from"), "с 6 мес.")
        self.assertEqual(attrs_baby.get("pack_weight"), "400 г")

        # 8. Обувь (Ботфорты)
        attrs_boots = extract_title_attributes("Ботфорты женские зимние")
        self.assertEqual(attrs_boots.get("type"), "Сапоги")
        self.assertEqual(attrs_boots.get("gender"), "Женские")
        self.assertEqual(attrs_boots.get("season"), "Зимние")

        # 9. Зоотовары (Диспенсер)
        attrs_pet = extract_title_attributes("Диспенсер воды для домашних животных")
        self.assertEqual(attrs_pet.get("type"), "Аксессуары и уход")

        # 10. Кабели и переходники
        attrs_adapter = extract_title_attributes("Адаптер HDMI - VGA")
        self.assertEqual(attrs_adapter.get("type"), "Переходник / Концентратор (Hub)")
        self.assertEqual(attrs_adapter.get("connector"), "HDMI")


if __name__ == "__main__":
    unittest.main()
