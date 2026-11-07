from tests import test_support  # noqa: F401
import unittest
import sqlite3
from domain.attribute_indexing import (
    index_product_facts,
    sync_all_source_attributes,
    normalize_attribute_value,
    ATTRIBUTE_LABELS,
)
from domain.search_core import SearchCore
from repositories import init_schema_v2
from repositories.search_repo import SearchRepository
from repositories.catalog_expansion_repo import DDL as EXPANSION_DDL


def create_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_v2(conn)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS product_source_evidence (
            product_id TEXT PRIMARY KEY,
            signature TEXT,
            facts_hash TEXT,
            facts_json TEXT,
            images_json TEXT,
            sources_json TEXT,
            status TEXT,
            checked_at TEXT,
            next_attempt TEXT
        );
    ''')
    conn.executescript(EXPANSION_DDL)
    return conn


class AttributeFacetsFilterTests(unittest.TestCase):
    def test_normalize_attribute_value(self):
        assert normalize_attribute_value('storage', '256 gb') == '256 ГБ'
        assert normalize_attribute_value('storage', '1 tb') == '1 ТБ'
        assert normalize_attribute_value('color', 'space gray') == 'Space gray'
        assert normalize_attribute_value('screen_size', '6.7 дюйм') == '6.7"'
        assert normalize_attribute_value('refresh_rate', '120 hz') == '120 Гц'
        assert normalize_attribute_value('5g', 'Да') == 'Да'
        assert normalize_attribute_value('nfc', 'true') == 'Да'
        assert normalize_attribute_value('panel_type', 'ips') == 'IPS'
        assert normalize_attribute_value('resolution', 'fhd') == '1920x1080 (Full HD)'
        assert normalize_attribute_value('certificate', 'gold') == '80 PLUS Gold'

    def test_extract_title_attributes_pc_hardware_and_monitors(self):
        from domain.attribute_indexing import extract_title_attributes

        # GPU
        gpu_attrs = extract_title_attributes('Видеокарта ASUS Dual GeForce RTX 4060 V2 OC 8GB')
        assert gpu_attrs.get('vram') == '8 ГБ'
        assert gpu_attrs.get('chipset') == 'GeForce RTX 4060'

        # Monitor
        mon_attrs = extract_title_attributes('Монитор 27" LG UltraGear 27GN60R-B 144Hz IPS FHD')
        assert mon_attrs.get('screen_size') == '27"'
        assert mon_attrs.get('refresh_rate') == '144 Гц'
        assert mon_attrs.get('panel_type') == 'IPS'
        assert mon_attrs.get('resolution') == '1920x1080 (Full HD)'

        # RAM
        ram_attrs = extract_title_attributes('Оперативная память Kingston FURY Beast Black DDR5 6000MHz 32GB (2x16GB)')
        assert ram_attrs.get('ram') == '32 ГБ'
        assert ram_attrs.get('memory_type') == 'DDR5'
        assert ram_attrs.get('clock_speed') == '6000 МГц'

        # Motherboard
        mb_attrs = extract_title_attributes('Материнская плата MSI MAG B650 TOMAHAWK WIFI AM5 DDR5 ATX')
        assert mb_attrs.get('chipset') == 'B650'
        assert mb_attrs.get('socket') == 'AM5'
        assert mb_attrs.get('memory_type') == 'DDR5'
        assert mb_attrs.get('form_factor') == 'ATX'

        # SSD
        ssd_attrs = extract_title_attributes('SSD накопитель 1TB Samsung 990 PRO M.2 2280 PCIe 4.0 NVMe')
        assert ssd_attrs.get('storage') == '1 ТБ'
        assert ssd_attrs.get('form_factor') == 'M.2 2280'
        assert ssd_attrs.get('interface') == 'PCIe 4.0'

        # PSU
        psu_attrs = extract_title_attributes('Блок питания ATX 850W ASUS ROG Thor Platinum')
        assert psu_attrs.get('power') == '850 Вт'
        assert psu_attrs.get('certificate') == '80 PLUS Platinum'
        assert psu_attrs.get('form_factor') == 'ATX'

    def test_index_and_search_attributes(self):
        conn = create_test_db()
        # Insert seller and channel
        conn.execute("INSERT INTO sellers (id, name, slug, created_at, updated_at) VALUES ('s1', 'Kaspi Seller', 'kaspi-seller', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('ch1', 's1', 'ONLINE', 'Kaspi', '2026-01-01')")

        # Insert canonical products
        conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, attributes_json, created_at, updated_at) VALUES ('p1', 'k1', 'Apple iPhone 15 128GB Black', 'Смартфоны', 'Apple', 'iPhone 15', '{}', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, attributes_json, created_at, updated_at) VALUES ('p2', 'k2', 'Apple iPhone 15 256GB Blue', 'Смартфоны', 'Apple', 'iPhone 15', '{}', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, attributes_json, created_at, updated_at) VALUES ('p3', 'k3', 'Samsung Galaxy S24 256GB Gray', 'Смартфоны', 'Samsung', 'Galaxy S24', '{}', '2026-01-01', '2026-01-01')")

        # Insert offers
        conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, city, availability, observed_at, created_at, updated_at) VALUES ('o1', 'p1', 's1', 'ch1', 'sku1', 'http://1', 400000, 'Алматы', 'in_stock', '2026-01-01', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, city, availability, observed_at, created_at, updated_at) VALUES ('o2', 'p2', 's1', 'ch1', 'sku2', 'http://2', 450000, 'Алматы', 'in_stock', '2026-01-01', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, city, availability, observed_at, created_at, updated_at) VALUES ('o3', 'p3', 's1', 'ch1', 'sku3', 'http://3', 420000, 'Алматы', 'in_stock', '2026-01-01', '2026-01-01', '2026-01-01')")

        # Index facts
        facts_p1 = [
            {'name': 'Объем встроенной памяти', 'value': '128 ГБ'},
            {'name': 'Цвет', 'value': 'черный'},
        ]
        facts_p2 = [
            {'name': 'Объем встроенной памяти', 'value': '256 ГБ'},
            {'name': 'Цвет', 'value': 'синий'},
        ]
        facts_p3 = [
            {'name': 'Встроенная память', 'value': '256 ГБ'},
            {'name': 'Цвет', 'value': 'серый'},
        ]

        index_product_facts(conn, 'p1', facts_p1, 'Apple iPhone 15 128GB Black')
        index_product_facts(conn, 'p2', facts_p2, 'Apple iPhone 15 256GB Blue')
        index_product_facts(conn, 'p3', facts_p3, 'Samsung Galaxy S24 256GB Gray')
        conn.commit()

        repo = SearchRepository(conn)
        core = SearchCore(repo)

        # 1. Search all without attribute filter: returns 2 iPhones
        resp1 = core.search(query='iPhone')
        assert resp1.total_products == 2
        assert len(resp1.attribute_facets) > 0
        # Facets should include storage and color
        storage_facet = next((f for f in resp1.attribute_facets if f['key'] == 'storage'), None)
        assert storage_facet is not None
        assert storage_facet['label'] == ATTRIBUTE_LABELS['storage']
        values_dict = {v['value']: v['count'] for v in storage_facet['values']}
        assert values_dict.get('128 ГБ') == 1
        assert values_dict.get('256 ГБ') == 1

        # 2. Search with attribute filter storage="256 ГБ"
        resp2 = core.search(query='iPhone', attributes={'storage': '256 ГБ'})
        assert resp2.total_products == 1
        assert resp2.items[0].product.id == 'p2'

    def test_sync_all_source_attributes(self):
        conn = create_test_db()
        conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, created_at, updated_at) VALUES ('p1', 'k1', 'Смартфон Xiaomi 14 512GB', 'Смартфоны', 'Xiaomi', '2026-01-01', '2026-01-01')")
        facts_json = '[{"name": "Встроенная память", "value": "512 ГБ"}, {"name": "Оперативная память", "value": "12 ГБ"}]'
        conn.execute("INSERT INTO product_source_evidence (product_id, signature, facts_hash, facts_json, images_json, sources_json, status, checked_at, next_attempt) VALUES ('p1', 'sig1', 'hash1', ?, '[]', '[]', 'ready', '2026-01-01', '2026-01-02')", (facts_json,))
        conn.commit()

        indexed = sync_all_source_attributes(conn)
        assert indexed == 2

        rows = conn.execute("SELECT key, value FROM product_search_attributes WHERE product_id='p1' ORDER BY key").fetchall()
        assert len(rows) == 2
        assert rows[0]['key'] == 'ram' and rows[0]['value'] == '12 ГБ'
        assert rows[1]['key'] == 'storage' and rows[1]['value'] == '512 ГБ'

    def test_sync_all_source_attributes_batch_commits(self):
        """Проверяет, что при массовой синхронизации коммиты выполняются короткими пачками (BATCH_SIZE=200)."""
        conn = create_test_db()
        for i in range(250):
            conn.execute(
                "INSERT INTO canonical_products (id, canonical_key, title, category, brand, created_at, updated_at) "
                "VALUES (?, ?, ?, 'Смартфоны', 'Xiaomi', '2026-01-01', '2026-01-01')",
                (f"p_{i}", f"k_{i}", f"Смартфон Xiaomi {i} 128GB Black")
            )
        conn.commit()

        class TrackingConn:
            def __init__(self, real):
                self._real = real
                self.commits = 0
            def commit(self):
                self.commits += 1
                return self._real.commit()
            def __getattr__(self, item):
                return getattr(self._real, item)

        tracker = TrackingConn(conn)
        indexed = sync_all_source_attributes(tracker, limit=500)
        assert indexed > 0
        # Должен быть минимум 1 промежуточный коммит на 200 товарах + 1 финальный
        assert tracker.commits >= 2

    def test_search_repo_build_model_facets_aggregation(self):
        """Проверяет агрегацию моделей видеокарт и исключение мусора/производителей из фасетов."""
        raw = [
            {"value": "GeForce RTX 5060 Ti", "count": 39},
            {"value": "RTX 5060 Ti Dual", "count": 4},
            {"value": "RTX 5060 Ti OC", "count": 4},
            {"value": "Palit", "count": 12},  # Производитель — должен быть исключен
            {"value": "Inno3D", "count": 8},  # Производитель — должен быть исключен
            {"value": "V8", "count": 6},  # Мусор — должен быть исключен
            {"value": "GeForce RTX 5070", "count": 25},
            {"value": "Radeon RX 7600", "count": 10},
            {"value": "PULSE AMD Radeon RX 7600", "count": 5},
        ]

        res = SearchRepository._build_model_facets(raw, category="Видеокарты")
        models_dict = {f["model"]: f["count"] for f in res}

        # RTX 5060 Ti должен объединить (39 + 4 + 4 = 47)
        assert models_dict.get("GeForce RTX 5060 Ti") == 47

        # Radeon RX 7600 должен объединить (10 + 5 = 15)
        assert models_dict.get("Radeon RX 7600") == 15

        # GeForce RTX 5070
        assert models_dict.get("GeForce RTX 5070") == 25

        # Производители и мусор не должны попасть в список моделей
        assert "Palit" not in models_dict
        assert "Inno3D" not in models_dict
        assert "V8" not in models_dict

    def test_extract_title_attributes_tv_ac_headphones_smartwatches(self):
        from domain.attribute_indexing import extract_title_attributes

        # 1. Телевизор (TV)
        tv1 = extract_title_attributes('Телевизор LG OLED55C3RLA 55" 4K UHD Smart TV 120Hz')
        self.assertEqual(tv1.get('screen_diagonal'), '55"')
        self.assertEqual(tv1.get('resolution'), '4K UHD')
        self.assertEqual(tv1.get('screen_tech'), 'OLED')
        self.assertEqual(tv1.get('smart_tv'), 'Да')
        self.assertEqual(tv1.get('refresh_rate'), '120 Гц')

        tv2 = extract_title_attributes('Телевизор Samsung 65" QLED 4K Smart TV черный')
        self.assertEqual(tv2.get('screen_diagonal'), '65"')
        self.assertEqual(tv2.get('screen_tech'), 'QLED')
        self.assertEqual(tv2.get('color'), 'Черный')

        # 2. Наушники (Headphones)
        hp1 = extract_title_attributes('Беспроводные наушники Apple AirPods Pro 2 TWS с ANC белый')
        self.assertEqual(hp1.get('wireless'), 'Да')
        self.assertEqual(hp1.get('type'), 'Внутриканальные / TWS')
        self.assertEqual(hp1.get('anc'), 'Да')
        self.assertEqual(hp1.get('color'), 'Белый')

        hp2 = extract_title_attributes('Проводные полноразмерные наушники Sony MDR-7506 черный')
        self.assertEqual(hp2.get('wireless'), 'Нет')
        self.assertEqual(hp2.get('type'), 'Полноразмерные')

        # 3. Кондиционеры (Air Conditioners)
        ac1 = extract_title_attributes('Сплит-система инверторный кондиционер Midea 09 (до 25 м²) белый')
        self.assertEqual(ac1.get('type'), 'Сплит-система')
        self.assertEqual(ac1.get('inverter'), 'Да')
        self.assertEqual(ac1.get('power_btu'), '09 (до 25 м²)')
        self.assertEqual(ac1.get('area_sqm'), 'до 25 м²')

        ac2 = extract_title_attributes('Мобильный кондиционер Zanussi 12 BTU белый')
        self.assertEqual(ac2.get('type'), 'Мобильный')
        self.assertEqual(ac2.get('power_btu'), '12 (до 35 м²)')

        # 4. Смарт-часы (Smartwatches)
        sw1 = extract_title_attributes('Смарт-часы Apple Watch Series 9 45mm Cellular темная ночь')
        self.assertEqual(sw1.get('case_size'), '45 мм')
        self.assertEqual(sw1.get('cellular'), 'Да')
        self.assertEqual(sw1.get('color'), 'Черный')

        sw2 = extract_title_attributes('Умные часы Samsung Galaxy Watch 6 40mm AMOLED серебристый')
        self.assertEqual(sw2.get('case_size'), '40 мм')
        self.assertEqual(sw2.get('screen_tech'), 'AMOLED')
        self.assertEqual(sw2.get('color'), 'Серебристый')

        # 5. Холодильники (Refrigerators)
        ref1 = extract_title_attributes('Холодильник LG DoorCooling+ GA-B509CLCL NoFrost Инверторный 203 см серебристый')
        self.assertEqual(ref1.get('refrigeration_defrost'), 'No Frost')
        self.assertEqual(ref1.get('compressor_type'), 'Инверторный')
        self.assertEqual(ref1.get('height_cm'), '203 см')
        self.assertEqual(ref1.get('color'), 'Серебристый')

        ref2 = extract_title_attributes('Холодильник Side-by-Side Midea MDRS710FGF02 Total No Frost белый')
        self.assertEqual(ref2.get('doors_type'), 'Side-by-Side')
        self.assertEqual(ref2.get('freezer_location'), 'Сбоку (Side-by-Side)')
        self.assertEqual(ref2.get('refrigeration_defrost'), 'No Frost')
        self.assertEqual(ref2.get('color'), 'Белый')

        # 6. Стиральные машины (Washing Machines)
        wm1 = extract_title_attributes('Стиральная машина с сушкой Haier HWD70-BP14929A 7 кг 1400 об/мин инверторная')
        self.assertEqual(wm1.get('drying'), 'С сушкой')
        self.assertEqual(wm1.get('load_capacity'), '7 кг')
        self.assertEqual(wm1.get('spin_speed'), '1400 об/мин')
        self.assertEqual(wm1.get('motor_type'), 'Инверторный')

        wm2 = extract_title_attributes('Стиральная машина LG F2V5HS9B 6.5 кг 1200 об/мин узкая белый')
        self.assertEqual(wm2.get('drying'), 'Без сушки')
        self.assertEqual(wm2.get('load_capacity'), '6.5 кг')
        self.assertEqual(wm2.get('spin_speed'), '1200 об/мин')
        self.assertEqual(wm2.get('depth_type'), 'Узкая (до 45 см)')
        self.assertEqual(wm2.get('color'), 'Белый')

        # 7. Пылесосы (Vacuums)
        vac1 = extract_title_attributes('Робот-пылесос Xiaomi Mi Robot Vacuum-Mop P с влажной уборкой 6000 Па белый')
        self.assertEqual(vac1.get('type'), 'Робот-пылесос')
        self.assertEqual(vac1.get('cleaning_type'), 'Сухая и влажная')
        self.assertEqual(vac1.get('suction_power'), '6000 Па')
        self.assertEqual(vac1.get('color'), 'Белый')

        vac2 = extract_title_attributes('Вертикальный беспроводной пылесос Dyson V15 240 аВт с контейнером')
        self.assertEqual(vac2.get('type'), 'Вертикальный беспроводной')
        self.assertEqual(vac2.get('dust_collector_type'), 'Контейнер (циклон)')
        self.assertEqual(vac2.get('suction_power'), '240 аВт')

        # 8. Автомобильные шины (Tires)
        tire1 = extract_title_attributes('Автошина Pirelli Ice Zero 2 225/45 R17 94H шипованная')
        self.assertEqual(tire1.get('diameter'), 'R17')
        self.assertEqual(tire1.get('tire_width'), '225 мм')
        self.assertEqual(tire1.get('tire_profile'), '45%')
        self.assertEqual(tire1.get('tire_size'), '225/45 R17')
        self.assertEqual(tire1.get('season'), 'Зимние шипованные')

        tire2 = extract_title_attributes('Шина летняя Bridgestone 205/55 R16 91V')
        self.assertEqual(tire2.get('diameter'), 'R16')
        self.assertEqual(tire2.get('tire_size'), '205/55 R16')
        self.assertEqual(tire2.get('season'), 'Летние')

        # Защита от ложного срабатывания на стиральные машины
        not_tire = extract_title_attributes('Стиральная машина PANASONIC NA-21CER1WKZ белый')
        self.assertIsNone(not_tire.get('diameter'))
        self.assertIsNone(not_tire.get('tire_size'))

        # 9. Мониторы (Monitors)
        mon1 = extract_title_attributes('Монитор 34" Xiaomi Curved Gaming Monitor G34WQi 3440x1440 180 Гц VA черный')
        self.assertEqual(mon1.get('screen_size'), '34"')
        self.assertEqual(mon1.get('resolution'), '3440x1440 (UWQHD)')
        self.assertEqual(mon1.get('aspect_ratio'), '21:9 (UltraWide)')
        self.assertEqual(mon1.get('refresh_rate'), '180 Гц')
        self.assertEqual(mon1.get('panel_type'), 'VA')
        self.assertEqual(mon1.get('curved'), 'Да')

        # 10. Клавиатуры и мыши (Peripherals)
        kb1 = extract_title_attributes('Клавиатура механическая беспроводная Keychron K2 Pro Bluetooth Черный')
        self.assertEqual(kb1.get('type'), 'Клавиатура')
        self.assertEqual(kb1.get('keyboard_type'), 'Механическая')
        self.assertEqual(kb1.get('connection_type'), 'Беспроводное')

        ms1 = extract_title_attributes('Мышь проводная Logitech G102 Lightsync 8000 DPI белый')
        self.assertEqual(ms1.get('type'), 'Мышь')
        self.assertEqual(ms1.get('connection_type'), 'Проводное')
        self.assertEqual(ms1.get('sensor_dpi'), '8000 DPI')

        # 11. Техника для кухни (Kitchen Appliances)
        ka1 = extract_title_attributes('Кофемашина DeLonghi Magnifica Start ECAM 220.22.GB 1450 Вт 1.8 л черный')
        self.assertEqual(ka1.get('type'), 'Кофемашина')
        self.assertEqual(ka1.get('power'), '1450 Вт')
        self.assertEqual(ka1.get('capacity'), '1.8 л')

        # 12. Сетевое оборудование (Network)
        net1 = extract_title_attributes('Маршрутизатор TP-Link Archer AX55 Wi-Fi 6 AX3000 Гигабитный')
        self.assertEqual(net1.get('type'), 'Wi-Fi роутер')
        self.assertEqual(net1.get('wifi_standard'), 'Wi-Fi 6 (802.11ax)')
        self.assertEqual(net1.get('lan_speed'), '1 Гбит/с')

        net2 = extract_title_attributes('Коммутатор TP-Link TL-SG1008P 8-портовый гигабитный с PoE')
        self.assertEqual(net2.get('type'), 'Коммутатор (Switch)')
        self.assertEqual(net2.get('ports_count'), '8 портов')
        self.assertEqual(net2.get('lan_speed'), '1 Гбит/с')
        self.assertEqual(net2.get('poe'), 'С поддержкой PoE')

        # 13. Принтеры и МФУ (Printers)
        pr1 = extract_title_attributes('МФУ Epson EcoTank L3250 СНПЧ цветной A4 Wi-Fi черный')
        self.assertEqual(pr1.get('type'), 'МФУ (принтер-сканер-копир)')
        self.assertEqual(pr1.get('print_tech'), 'Струйная с СНПЧ')
        self.assertEqual(pr1.get('print_color'), 'Цветная')
        self.assertEqual(pr1.get('paper_format'), 'A4')
        self.assertEqual(pr1.get('connectivity'), 'Wi-Fi')

        # 14. Защита от утечки diaper_type на не-детские товары
        from domain.attribute_indexing import normalize_attribute_value, FACT_KEY_MAP
        self.assertEqual(FACT_KEY_MAP.get('тип'), ('type', 'Тип устройства'))
        self.assertIsNone(normalize_attribute_value('diaper_type', 'Настенная сплит-система'))

        # 15. Волна 3: Процессоры, Корпуса, Приставки, Power Bank, ИБП, Обувь, Одежда, Мебель, Инструменты, Парфюм, Игрушки
        cpu1 = extract_title_attributes('Процессор Intel Core i5 10500, LGA1200, OEM')
        self.assertEqual(cpu1.get('cpu_family'), 'Intel Core i5')
        self.assertEqual(cpu1.get('socket'), 'LGA 1200')
        self.assertEqual(cpu1.get('delivery_type'), 'OEM / Tray')

        case1 = extract_title_attributes('Корпус Thermaltake View 21 TG RGB Plus, Black')
        self.assertEqual(case1.get('side_window'), 'Закаленное стекло (TG)')
        self.assertEqual(case1.get('backlight'), 'RGB')

        con1 = extract_title_attributes('Игровая приставка PS 5 Slim 1TB Digital Edition')
        self.assertEqual(con1.get('type'), 'Стационарная консоль')
        self.assertEqual(con1.get('platform'), 'PlayStation 5')
        self.assertEqual(con1.get('storage'), '1 ТБ')
        self.assertEqual(con1.get('disc_drive'), 'Digital Edition (без дисковода)')

        pb1 = extract_title_attributes('Мобильный аккумулятор Xiaomi 33W Pocket Edition Pro 10000 (PB1030ZM), Blue')
        self.assertEqual(pb1.get('type'), 'Внешний аккумулятор (Power Bank)')
        self.assertEqual(pb1.get('battery'), '10000 мАч')
        self.assertEqual(pb1.get('power'), '33 Вт')

        ups1 = extract_title_attributes('UPS SVC V1500-F-LCD')
        self.assertEqual(ups1.get('type'), 'ИБП (UPS)')
        self.assertEqual(ups1.get('power_va'), '1500 ВА')

        sh1 = extract_title_attributes('T.TACCARDI Ботинки мужские зимние для активного отдыха K2394-35B')
        self.assertEqual(sh1.get('type'), 'Ботинки')
        self.assertEqual(sh1.get('gender'), 'Мужские')
        self.assertEqual(sh1.get('season'), 'Зимние')

        fur1 = extract_title_attributes('Кровать Стелла 4-160x200 Велюр Белый')
        self.assertEqual(fur1.get('bed_size'), '160x200 см')
        self.assertEqual(fur1.get('material'), 'Велюр')

        tl1 = extract_title_attributes('Батарея аккумуляторная Makita BL1830B')
        self.assertEqual(tl1.get('type'), 'Аккумуляторы для инструмента')
        self.assertEqual(tl1.get('power_supply'), 'Аккумуляторный')
        self.assertEqual(tl1.get('voltage'), '18 В')

        pf1 = extract_title_attributes('L\'EAU KENZO POUR FEMME GLACEE Туалетная вода 50 мл')
        self.assertEqual(pf1.get('type'), 'Туалетная вода (EDT)')
        self.assertEqual(pf1.get('gender'), 'Женский')
        self.assertEqual(pf1.get('volume_ml'), '50 мл')

        toy1 = extract_title_attributes('LEGO: Инерционный Digatron Monster Jam Technic 42199 218 дет.')
        self.assertEqual(toy1.get('type'), 'Конструктор')
        self.assertEqual(toy1.get('lego_series'), 'LEGO Technic')
        self.assertEqual(toy1.get('pieces_count'), '218 дет.')

        # 16. Волна 4: Наушники, Смарт-часы, Компьютеры, Материнские платы, Охлаждение, Аудио, Книги и канцелярия, Зоотовары, Посуда, Бытовая техника
        hp1 = extract_title_attributes('Гарнитура A4Tech HS-28, серый-черный')
        self.assertEqual(hp1.get('type'), 'Гарнитура (с микрофоном)')
        self.assertEqual(hp1.get('microphone'), 'Со встроенным микрофоном')
        self.assertEqual(hp1.get('wireless'), 'Нет')

        sw1 = extract_title_attributes('Смарт-часы Apple Watch Series 8 GPS 45mm Aluminum Case with Sport Band')
        self.assertEqual(sw1.get('type'), 'Смарт-часы')
        self.assertEqual(sw1.get('case_size'), '45 мм')
        self.assertEqual(sw1.get('strap_material'), 'Силикон')

        pc1 = extract_title_attributes('Моноблок 23.8" Asus A3402WBAK, Core i5 1235U, 8ГБ, 512ГБ SSD, FreeDOS')
        self.assertEqual(pc1.get('type'), 'Моноблок (All-in-One)')
        self.assertEqual(pc1.get('screen_size'), '23.8"')
        self.assertEqual(pc1.get('cpu'), 'Intel Core i5')
        self.assertEqual(pc1.get('ram'), '8 ГБ')
        self.assertEqual(pc1.get('storage'), '512 ГБ')
        self.assertEqual(pc1.get('os'), 'Без ОС')

        mb1 = extract_title_attributes('Материнская плата Asus PRIME B550M-K')
        self.assertEqual(mb1.get('chipset'), 'B550')
        self.assertEqual(mb1.get('socket'), 'AM4')
        self.assertEqual(mb1.get('form_factor'), 'Micro-ATX')

        cool1 = extract_title_attributes('Вентилятор для корпуса Deepcool RF120 FS, 120мм, 3-color LED')
        self.assertEqual(cool1.get('type'), 'Вентилятор для корпуса')
        self.assertEqual(cool1.get('fan_size'), '120 мм')
        self.assertEqual(cool1.get('backlight'), 'LED')

        aud1 = extract_title_attributes('Микрофон Thronmax M20 Streaming Kit USB')
        self.assertEqual(aud1.get('type'), 'Микрофон / Радиосистема')
        self.assertEqual(aud1.get('wireless'), 'Нет')

        bk1 = extract_title_attributes('Фотобумага Lomond глянцевая 230г/м2, A4, 50л.')
        self.assertEqual(bk1.get('type'), 'Бумага и фотобумага')
        self.assertEqual(bk1.get('paper_format'), 'A4')
        self.assertEqual(bk1.get('pieces_count'), '50 шт.')

        pet1 = extract_title_attributes('Корм для кошек с лососем в желе «Felix Аппетитные кусочки», 75 г')
        self.assertEqual(pet1.get('type'), 'Влажный корм / Паучи')
        self.assertEqual(pet1.get('pet_type'), 'Для кошек')
        self.assertEqual(pet1.get('pack_weight'), '75 г')

        kw1 = extract_title_attributes('Сковорода Tefal Традиция, 24 см, чугун, черный')
        self.assertEqual(kw1.get('type'), 'Сковороды, воки и сотейники')
        self.assertEqual(kw1.get('diameter_cm'), '24 см')
        self.assertEqual(kw1.get('material'), 'Чугун')

        ap1 = extract_title_attributes('Утюг Tefal Express Steam FV2837E0 2400 Вт')
        self.assertEqual(ap1.get('type'), 'Утюг')
        self.assertEqual(ap1.get('power'), '2400 Вт')

        # 17. Волна 5: Продукты, Напитки, Молочные продукты, Лекарства, Косметика, Детские товары, Авто, Картриджи, Фото/Видео
        fmcg1 = extract_title_attributes('СЫР «ПРЕЗИДЕНТ» 45% КАМАМБЕР С БЕЛОЙ ПЛЕСЕНЬЮ 125 ГР')
        self.assertEqual(fmcg1.get('type'), 'Сыр')
        self.assertEqual(fmcg1.get('fat_content'), '45%')
        self.assertEqual(fmcg1.get('pack_weight'), '125 г')

        dr1 = extract_title_attributes('НАПИТОК ГАЗИРОВАННЫЙ «COCA-COLA» 1,5 Л')
        self.assertEqual(dr1.get('type'), 'Газированные и энергетические напитки')
        self.assertEqual(dr1.get('volume_ml'), '1.5 л')

        ph1 = extract_title_attributes('Кетонал ДУО 150мг № 30 капс')
        self.assertEqual(ph1.get('type'), 'Капсулы')
        self.assertEqual(ph1.get('dosage'), '150 мг')
        self.assertEqual(ph1.get('pieces_count'), '30 шт.')

        bb1 = extract_title_attributes('Молочко NAN 3 Optipro 1050г с 12месяцев')
        self.assertEqual(bb1.get('type'), 'Молочные смеси')
        self.assertEqual(bb1.get('pack_weight'), '1050 г')
        self.assertEqual(bb1.get('age_from'), 'с 12 мес.')

        hc1 = extract_title_attributes('Электробритва Panasonic ES-SL41-S520')
        self.assertEqual(hc1.get('type'), 'Бритвы, триммеры и эпиляторы')

        sk1 = extract_title_attributes('Крем для лица MIXIT солнцезащитный SPF50, 50 мл')
        self.assertEqual(sk1.get('type'), 'Уход за лицом и губами')
        self.assertEqual(sk1.get('spf'), 'SPF 50+')
        self.assertEqual(sk1.get('volume_ml'), '50 мл')

        ink1 = extract_title_attributes('Картридж HP C3906A - Black')
        self.assertEqual(ink1.get('type'), 'Картридж для принтера')
        self.assertEqual(ink1.get('print_color'), 'Черный (Black)')

        pv1 = extract_title_attributes('Web-камера Logitech HD C922 Pro Stream Full HD 1080p')
        self.assertEqual(pv1.get('type'), 'Веб-камера / Стриминг')
        self.assertEqual(pv1.get('resolution'), 'Full HD 1080p')

        # 18. Волна 6: ПО, Телефония, Велосипеды и самокаты, Автозапчасти, Бронепленки, Книги, Мебель, Батарейки
        sw_lic = extract_title_attributes('Антивирус Dr. Web Security Space, 24 мес., 3 ПК, Электронный ключ')
        self.assertEqual(sw_lic.get('type'), 'Антивирус и безопасность')
        self.assertEqual(sw_lic.get('license_term'), '2 года (24 мес.)')
        self.assertEqual(sw_lic.get('devices_count'), '3 ПК')
        self.assertEqual(sw_lic.get('delivery_type'), 'Электронный ключ (ESD)')

        tel1 = extract_title_attributes('Радио-телефон Panasonic KX-TG1711CAB, Black')
        self.assertEqual(tel1.get('type'), 'Радиотелефон (DECT)')
        self.assertEqual(tel1.get('connection_type'), 'Беспроводной DECT')

        bike1 = extract_title_attributes('Детский велосипед Ninebot Kids Bike 16" (N1KB16/RED)')
        self.assertEqual(bike1.get('type'), 'Велосипед')
        self.assertEqual(bike1.get('wheel_size'), '16"')
        self.assertEqual(bike1.get('power_supply'), 'Механический')

        scoot1 = extract_title_attributes('Электросамокат Ninebot KickScooter ES4, Black')
        self.assertEqual(scoot1.get('type'), 'Электросамокат')
        self.assertEqual(scoot1.get('power_supply'), 'Электрический (АКБ)')

        auto_part = extract_title_attributes('Stellox сайлентблок рычага подвески 87-98072-SX')
        self.assertEqual(auto_part.get('type'), 'Автозапчасти, подвеска и фильтры')

        film1 = extract_title_attributes('ТПУ Бронепленка Devia, Soft Protective Front Film (update), глянцевая')
        self.assertEqual(film1.get('type'), 'Защитная пленка')

        book_auth = extract_title_attributes('Беляков Л. М.: Поколение без фильтра: иллюстрированная биография группы Nirvana')
        self.assertEqual(book_auth.get('type'), 'Книги и учебная литература')
        self.assertEqual(book_auth.get('author'), 'Беляков Л. М.')

        furn_divan = extract_title_attributes('Льери 160 Велюр Розовый')
        self.assertEqual(furn_divan.get('type'), 'Диван')
        self.assertEqual(furn_divan.get('material'), 'Велюр')

        cbl_int = extract_title_attributes('Кабель интерфейсный для Apple Lightning Ship API08BB, Black')
        self.assertEqual(cbl_int.get('type'), 'Кабель')
        self.assertEqual(cbl_int.get('connector'), 'Lightning')

        bat1 = extract_title_attributes('Батарейка литиевая CR2032 Camelion')
        self.assertEqual(bat1.get('type'), 'Батарейки и элементы питания')

    def test_category_schema_and_search_priority_orders(self):
        from domain.category import MASTER_TAXONOMY
        from repositories.category_repo import CategoryRepository

        cats = {c.id: c for c in MASTER_TAXONOMY}
        for cat_id in ('tv', 'headphones', 'air_conditioners', 'smartwatches', 'refrigerators',
                       'washing_machines', 'vacuums', 'tires', 'monitors', 'peripherals',
                       'kitchen_appliances', 'network', 'printers', 'tablets', 'removable_storage',
                       'consoles', 'cpus', 'pc_cases', 'power', 'cables_chargers', 'shoes',
                       'clothing', 'furniture', 'tools_repair', 'perfume', 'beauty', 'toys',
                       'pc_desktops', 'motherboards', 'cooling', 'audio', 'books_stationery',
                       'books', 'pets', 'cat_food', 'dog_food', 'kitchenware', 'appliances',
                       'home', 'home_textile', 'garden', 'food', 'drinks', 'dairy', 'meat_poultry',
                       'medicine', 'household_chem', 'kids', 'baby_care', 'baby_food', 'strollers',
                       'makeup', 'haircare', 'skincare', 'auto', 'phone_cases', 'printer_ink',
                       'tourism', 'board_games', 'photo_video', 'software', 'telephony',
                       'bicycles', 'sport', 'fitness', 'phone_glass', 'car_electronics'):
            self.assertIn(cat_id, cats)
            self.assertGreater(len(cats[cat_id].attributes_schema), 0)

        conn = create_test_db()
        repo = CategoryRepository(conn)
        repo.sync_master_taxonomy()

        ref_cat = repo.get_by_id('refrigerators')
        self.assertIsNotNone(ref_cat)
        self.assertIn('refrigeration_defrost', ref_cat.attributes_schema)
        self.assertIn('doors_type', ref_cat.attributes_schema)

        wm_cat = repo.get_by_id('washing_machines')
        self.assertIsNotNone(wm_cat)
        self.assertIn('load_capacity', wm_cat.attributes_schema)
        self.assertIn('drying', wm_cat.attributes_schema)

        vac_cat = repo.get_by_id('vacuums')
        self.assertIsNotNone(vac_cat)
        self.assertIn('type', vac_cat.attributes_schema)
        self.assertIn('suction_power', vac_cat.attributes_schema)

        tire_cat = repo.get_by_id('tires')
        self.assertIsNotNone(tire_cat)
        self.assertIn('diameter', tire_cat.attributes_schema)
        self.assertIn('season', tire_cat.attributes_schema)


if __name__ == "__main__":
    unittest.main()
