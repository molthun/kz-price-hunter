from tests import test_support  # noqa: F401
import unittest
import sqlite3
from unittest.mock import patch

from repositories import init_schema_v2
from repositories.catalog_expansion_repo import DDL as EXPANSION_DDL
from repositories.search_repo import SearchRepository, TOKEN_SYNONYMS
from domain.models import CanonicalProduct, Offer
from repositories.product_repo import ProductRepository
from repositories.offer_repo import OfferRepository
import local_search


def create_test_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema_v2(conn)
    conn.executescript(EXPANSION_DDL)
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
    return conn


class BroadFuzzySearchUnitTests(unittest.TestCase):
    def test_russian_adjective_stemming(self):
        """Проверяет стемминг окончаний прилагательных: 'широкий', 'тихий', 'игровой'."""
        alts_wide = SearchRepository._russian_word_alts("широкий")
        self.assertIn("широк", alts_wide)

        alts_quiet = SearchRepository._russian_word_alts("тихий")
        self.assertIn("тих", alts_quiet)

        alts_gaming = SearchRepository._russian_word_alts("игровой")
        self.assertIn("игров", alts_gaming)


class BroadFuzzySearchDBTests(unittest.TestCase):
    def setUp(self):
        self.conn = create_test_db()
        cur = self.conn.cursor()

        # Seed categories
        cur.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, attributes_schema_json, search_priority, is_active, created_at, updated_at) VALUES ('monitors', 'Мониторы', 'electronics', '', '[]', '{}', 0, 1, '2026-11-06', '2026-11-06')")
        cur.execute("INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, attributes_schema_json, search_priority, is_active, created_at, updated_at) VALUES ('vacuum_cleaners', 'Пылесосы', 'appliances', '', '[]', '{}', 0, 1, '2026-11-06', '2026-11-06')")

        # Seed seller & channel
        cur.execute("INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) VALUES ('s1', 'Kaspi', 'kaspi', 4.9, 'kaspi.kz', 1, '{}', '2026-11-06', '2026-11-06')")
        cur.execute("INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('ch1', 's1', 'marketplace', 'Kaspi', '2026-11-06')")

        # Seed products:
        # 1) Ultrawide monitor with "Монитор" in Russian and "UltraWide"
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='m1', canonical_key='k1', title='Монитор LG UltraWide 34U620B 34 Black', category='monitors'))
        OfferRepository(self.conn).save_or_update(Offer(id='o1', product_id='m1', seller_id='s1', channel_id='ch1', external_sku='sku1', url='http://ex.com/1', price=200000, condition='new', city='Алматы'))

        # 2) 21:9 monitor
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='m2', canonical_key='k2', title='Монитор Игровой 34 Samsung 3440x1440 21:9 Curved Black', category='monitors'))
        OfferRepository(self.conn).save_or_update(Offer(id='o2', product_id='m2', seller_id='s1', channel_id='ch1', external_sku='sku2', url='http://ex.com/2', price=250000, condition='new', city='Алматы'))

        # 3) Regular monitor without wide
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='m3', canonical_key='k3', title='Монитор Dell P2424HT 24 Silver', category='monitors'))
        OfferRepository(self.conn).save_or_update(Offer(id='o3', product_id='m3', seller_id='s1', channel_id='ch1', external_sku='sku3', url='http://ex.com/3', price=150000, condition='new', city='Алматы'))

        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_wide_monitor_synonyms_find_ultrawide_and_21_9(self):
        """Проверяет, что английский неявный запрос 'wide monitor' находит UltraWide и 21:9 мониторы."""
        repo = SearchRepository(self.conn)
        results = repo.search_products("wide monitor")
        matched_ids = [it["product"].id for it in results]
        self.assertIn("m1", matched_ids)
        self.assertIn("m2", matched_ids)
        self.assertNotIn("m3", matched_ids)

    def test_category_fallback_when_descriptive_words_miss_catalog(self):
        """Проверяет, что абстрактный описательный запрос без точных слов находит товары раздела."""
        understanding = {
            "status": "ok",
            "plan": {
                "search_query": "хороший экран для работы",
                "category_id": "monitors",
                "category_name": "Мониторы",
                "fields": [],
                "attributes": {},
                "confidence": 0.95,
                "clarification": ""
            }
        }
        args = {
            "query": "хороший экран для работы",
            "city": None,
            "category": None,
            "limit": 20
        }
        with patch("database.get_connection", return_value=self.conn):
            data = local_search.search_sync(args, understanding, {})
        self.assertGreater(data["total_products"], 0)
        self.assertTrue(data["search_help"]["is_fallback"])
        self.assertIn("Показаны товары из раздела «Мониторы»", data["search_help"]["clarification"])


if __name__ == "__main__":
    unittest.main()
