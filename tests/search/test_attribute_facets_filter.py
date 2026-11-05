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


if __name__ == "__main__":
    unittest.main()
