from tests import test_support  # noqa: F401
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import local_search
from domain.models import CanonicalProduct, Offer
from repositories import init_schema_v2
from repositories.catalog_expansion_repo import CatalogExpansionRepository, DDL as EXPANSION_DDL
from repositories.category_repo import CategoryRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository


class KeyboardLayoutRecoveryTest(unittest.TestCase):
    def test_converts_unmistakable_wrong_layout_queries(self):
        cases = {
            "rhjccjdrb": "кроссовки",
            "rhjccjdrb 42": "кроссовки 42",
            "ntktdbpjh": "телевизор",
            "gsktcjc": "пылесос",
            "pfhzlrf": "зарядка",
            "rjvgm.nth": "компьютер",
            "cvfhnajy": "смартфон",
            "rehnrf": "куртка",
            "rjhj,rf": "коробка",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(local_search.fix_wrong_keyboard_layout(raw), expected)

    def test_preserves_english_brands_models_and_cyrillic(self):
        untouched = [
            "iPhone 15 Pro 256",
            "Nike",
            "Nike Air Force 1",
            "Samsung Galaxy S24",
            "RTX 4070",
            "SSD 1TB",
            "USB-C",
            "PS5",
            "PlayStation 5",
            "AirPods Pro",
            "Dyson",
            "Bosch",
            "HP",
            "LG",
            "Geox",
            "Kari",
            "кроссовки",
            "зимняя резина",
            "",
            "   ",
        ]
        for raw in untouched:
            with self.subTest(raw=raw):
                self.assertIsNone(local_search.fix_wrong_keyboard_layout(raw))


class LocalSearchLayoutAndPlanGuardIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test.db")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.conn.executescript(EXPANSION_DDL)
        self.conn.executescript('''
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
        CategoryRepository(self.conn)
        self.repo = CatalogExpansionRepository(self.conn)

        self.conn.execute(
            "INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, attributes_schema_json, search_priority, is_active, created_at, updated_at) "
            "VALUES ('shoes', 'Обувь', NULL, '', '[]', '{}', 0, 1, '2026-11-26', '2026-11-26')"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, attributes_schema_json, search_priority, is_active, created_at, updated_at) "
            "VALUES ('appliances', 'Бытовая техника', NULL, '', '[]', '{}', 0, 1, '2026-11-26', '2026-11-26')"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
            "VALUES ('s1', 'Kari', 'kari', 4.8, 'kari.kz', 1, '{}', '2026-11-26', '2026-11-26')"
        )
        self.conn.execute(
            "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
            "VALUES ('c1', 's1', 'website', 'Kari KZ', '2026-11-26')"
        )
        # Товар в разделе shoes (обувь)
        ProductRepository(self.conn).save_or_update(
            CanonicalProduct(id="p_shoe", canonical_key="k_shoe", title="Кроссовки мужские спортивные", category="shoes", brand="Kari")
        )
        OfferRepository(self.conn).save_or_update(
            Offer(id="o_shoe", product_id="p_shoe", seller_id="s1", channel_id="c1", external_sku="sku_shoe", url="http://ex.com/shoe", price=25000, condition="new", city="Астана")
        )
        self.conn.execute(
            "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('o_shoe', 'p_shoe', '42', '2026-11-26')"
        )
        # Товар в разделе appliances (чтобы проверить отсутствие ложного fallback в Бытовую технику)
        ProductRepository(self.conn).save_or_update(
            CanonicalProduct(id="p_app", canonical_key="k_app", title="Стиральный порошок Doffi", category="appliances", brand="Doffi")
        )
        OfferRepository(self.conn).save_or_update(
            Offer(id="o_app", product_id="p_app", seller_id="s1", channel_id="c1", external_sku="sku_app", url="http://ex.com/app", price=3500, condition="new", city="Астана")
        )
        self.conn.commit()
        local_search._plans.clear()
        local_search._failures.clear()

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def test_wrong_layout_finds_shoes_even_when_ai_unavailable_and_preserves_raw_query(self):
        args = {"query": "rhjccjdrb 42", "city": None, "category": None, "limit": 20}
        understanding = {"status": "unavailable", "corrected_query": "кроссовки 42"}
        with patch("database.get_connection", side_effect=self._get_conn):
            data = local_search.search_sync(args, understanding, {})
        self.assertEqual(data["query"], "rhjccjdrb 42")
        self.assertEqual(data["parsed_query"]["raw_query"], "rhjccjdrb 42")
        self.assertEqual(data["total_products"], 1)
        self.assertEqual(data["items"][0]["product"]["id"], "p_shoe")
        self.assertEqual(data["selected_attributes"].get("size"), "42")
        self.assertEqual(data["local_ai"].get("corrected_query"), "кроссовки 42")
        self.assertIn("кроссовки 42", data["search_help"]["clarification"])
        self.assertFalse(data["search_help"]["is_fallback"])

    def test_understand_passes_corrected_cyrillic_to_local_model(self):
        captured_prompts = []

        async def fake_call(prompt, base, model, api_key, timeout=18.0):
            captured_prompts.append(prompt)
            return {
                "search_query": "кроссовки",
                "category_id": "shoes",
                "category_name": "Обувь",
                "parent_id": None,
                "confidence": 0.95,
                "attributes": {},
                "fields": [
                    {"key": "season", "label": "Сезон"},
                    {"key": "material", "label": "Материал"},
                    {"key": "gender", "label": "Пол"},
                ],
                "clarification": "",
            }

        categories = [{"id": "shoes", "name": "Обувь", "parent_id": None, "fields": {}}]
        with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
             patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen"}), \
             patch("local_ai.pause_reason", return_value=None), \
             patch("ai_service.call_local_api", new=AsyncMock(side_effect=fake_call)):
            res = asyncio.run(local_search.understand("rhjccjdrb", categories))

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res.get("corrected_query"), "кроссовки")
        self.assertEqual(res["plan"]["category_id"], "shoes")
        self.assertTrue(captured_prompts)
        self.assertIn('"кроссовки"', captured_prompts[0])
        self.assertNotIn('"rhjccjdrb"', captured_prompts[0])

    def test_unclear_query_does_not_poison_saved_plan_with_appliances(self):
        args = {"query": "квантовый синхрофазотрон", "city": None, "category": None, "limit": 20}
        understanding = {"status": "unclear"}
        with patch("database.get_connection", side_effect=self._get_conn):
            first = local_search.search_sync(args, understanding, {})
            second = local_search.search_sync(args, understanding, {})

        self.assertEqual(first["total_products"], 0)
        self.assertFalse(first["search_help"]["is_fallback"])
        # Повторный запрос не должен вытаскивать фейковый appliances-план из catalog_expansions
        self.assertEqual(second["total_products"], 0)
        self.assertFalse(second["search_help"]["is_fallback"])
        self.assertIsNone(self.repo.saved_plan("квантовый синхрофазотрон"))

    def test_legacy_polluted_appliances_placeholder_is_ignored_by_saved_plan(self):
        legacy_fake = {
            "category_id": "appliances",
            "category_name": "Каталог",
            "search_query": "несуществующий товар",
            "confidence": 1.0,
            "fields": [],
            "attributes": {},
        }
        self.repo.propose("несуществующий товар", None, legacy_fake, auto_approve=True)
        self.assertIsNone(self.repo.saved_plan("несуществующий товар"))

        # А настоящий план с реальным названием раздела (или полями) по-прежнему работает
        real_plan = {
            "category_id": "appliances",
            "category_name": "Бытовая техника",
            "search_query": "стиральная машина",
            "confidence": 0.95,
            "fields": [{"key": "load", "label": "Загрузка"}],
            "attributes": {},
        }
        self.repo.propose("стиральная машина", None, real_plan, auto_approve=True)
        loaded = self.repo.saved_plan("стиральная машина")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["category_id"], "appliances")


if __name__ == "__main__":
    unittest.main()
