from tests import test_support  # noqa: F401
import sqlite3
import pytest
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


def test_normalize_attribute_value():
    assert normalize_attribute_value('storage', '256 gb') == '256 ГБ'
    assert normalize_attribute_value('storage', '1 tb') == '1 ТБ'
    assert normalize_attribute_value('color', 'space gray') == 'Space gray'
    assert normalize_attribute_value('screen_size', '6.7 дюйм') == '6.7"'
    assert normalize_attribute_value('refresh_rate', '120 hz') == '120 Гц'
    assert normalize_attribute_value('5g', 'Да') == 'Да'
    assert normalize_attribute_value('nfc', 'true') == 'Да'


def test_index_and_search_attributes():
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


def test_sync_all_source_attributes():
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
