from tests import test_support  # must be first

import asyncio
import sqlite3
import unittest

import catalog_ai_sort
from catalog_classifier import CatalogClassifier
from repositories.schema_v2 import init_schema_v2


class _Unclosable:
    """Соединение с базой в памяти переживает close() внутри прохода."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


class CatalogAiSortTest(unittest.TestCase):
    def setUp(self):
        self.real = sqlite3.connect(":memory:", check_same_thread=False)
        self.real.row_factory = sqlite3.Row
        init_schema_v2(self.real)
        self.addCleanup(self.real.close)
        self.conn = _Unclosable(self.real)

    def _add(self, pid, title, category):
        self.conn.execute(
            "INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'x', 'x')", (pid, pid, title, category))
        self.conn.commit()

    def _category(self, pid):
        return self.conn.execute("SELECT category FROM canonical_products WHERE id = ?", (pid,)).fetchone()[0]

    def test_decide_applies_only_confident_and_narrower(self):
        self.assertTrue(catalog_ai_sort.decide("Дом и сад", {"id": "furniture", "c": 0.95})["applied"])
        self.assertFalse(catalog_ai_sort.decide("Дом и сад", {"id": "furniture", "c": 0.7})["applied"])
        self.assertFalse(catalog_ai_sort.decide("Мебель", {"id": "home", "c": 0.99})["applied"])
        self.assertFalse(catalog_ai_sort.decide("Дом и сад", {"id": "other", "c": 0.99})["applied"])
        self.assertFalse(catalog_ai_sort.decide("Дом и сад", "мусор")["applied"])

    def test_tick_uses_rules_first_and_logs_model_verdict(self):
        self._add("rule", "Смартфон Samsung Galaxy S24", "Смартфоны")
        self._add("ai", "Шкаф Noble 45 см", "Mebel.kz: 🚪 Шкафы")
        asked = []

        async def ask(prompt):
            asked.append(prompt)
            return {"id": "furniture", "c": 0.95}

        stats = asyncio.run(catalog_ai_sort.sort_tick(lambda: self.conn, ask, "m", CatalogClassifier(), pause=0))
        self.assertEqual(len(asked), 1)
        self.assertIn("Шкаф", asked[0])
        self.assertEqual(stats["applied"], 1)
        self.assertEqual(self._category("ai"), "Мебель")
        row = self.conn.execute("SELECT old_category, applied FROM category_ai_verdicts WHERE product_id = 'ai'").fetchone()
        self.assertEqual(tuple(row), ("Mebel.kz: 🚪 Шкафы", 1))

    def test_check_keeps_cheap_real_devices(self):
        no = {"same": False, "id": "vacuum_parts", "c": 0.98}
        self.assertEqual(catalog_ai_sort.decide_check("Пылесосы", no, "Стяжка задняя 870 мм"), "Расходники для пылесосов")
        self.assertIsNone(catalog_ai_sort.decide_check("Пылесосы", no, "Haley HY-906 красный"))
        self.assertIsNone(catalog_ai_sort.decide_check("Пылесосы", no, "Робот уборщик"))
        self.assertIsNone(catalog_ai_sort.decide_check("Пылесосы", {"same": True, "c": 1}, "Стяжка"))
        self.assertIsNone(catalog_ai_sort.decide_check("Пылесосы", {"same": False, "id": "toys", "c": 0.5}, "Стяжка"))

    def test_check_uses_parent_when_model_target_is_not_trusted(self):
        wrong = {"same": False, "id": "cables_chargers", "c": 0.98}
        self.assertEqual(catalog_ai_sort.decide_check("Смартфоны", wrong, "Sven PS-240 ЧЕРНЫЙ"), "Электроника")
        self.assertEqual(catalog_ai_sort.decide_check("Телевизоры", wrong, "Кронштейн WALFIX R-241B"), "Электроника")

    def test_no_answer_keeps_product_in_queue(self):
        self._add("ai", "Шкаф", "Mebel.kz: 🚪 Шкафы")

        async def ask(prompt):
            return None

        stats = asyncio.run(catalog_ai_sort.sort_tick(lambda: self.conn, ask, "m", CatalogClassifier(), pause=0))
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM category_ai_verdicts").fetchone()[0], 0)

    def test_applied_verdict_survives_rescan(self):
        from domain.models import CanonicalProduct
        from repositories.product_repo import ProductRepository
        self._add("ai", "Шкаф", "Мебель")
        catalog_ai_sort.save_verdict(self.conn, "ai", "Дом и сад", "Мебель", 0.95, "m", True)
        ProductRepository(self.real).save_or_update(
            CanonicalProduct(id="ai", canonical_key="ai", title="Шкаф", category="Дом и сад"))
        self.assertEqual(self._category("ai"), "Мебель")


if __name__ == "__main__":
    unittest.main()
