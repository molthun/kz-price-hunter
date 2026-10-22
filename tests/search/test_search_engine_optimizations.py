"""Оптимизации гибридного поиска: аксессуары, fallback LIKE и релевантность.

Проверяем поведение, а не наличие кода: аксессуар к искомому товару отсекается один раз
на запрос, резервный LIKE-поиск видит и заголовок, и категорию, а сортировка по
релевантности поднимает точные совпадения выше «спама» из слов.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest
from unittest.mock import patch

import search_engine
from database import get_connection, init_db, save_or_update_products_batch


def product(pid: str, title: str, category: str, price: int) -> dict:
    return {
        "id": pid,
        "shop": "Kaspi Магазин",
        "city": "Астана",
        "title": title,
        "category": category,
        "price": price,
        "url": f"https://example.invalid/{pid}",
        "image_url": "https://img.example.invalid/1.jpg",
    }


class SearchEngineOptimizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        with get_connection() as conn:
            conn.execute("DELETE FROM products WHERE id LIKE 'test-opt-%'")
            conn.commit()
        save_or_update_products_batch([
            product("test-opt-laptop", "Ноутбук ASUS TUF Gaming 15.6", "Ноутбуки", 500000),
            product("test-opt-bag", "Сумка для ноутбука ASUS", "Сумки", 9000),
            product("test-opt-monitor", "Монитор Dell UltraSharp 27", "Мониторы", 180000),
            product("test-opt-bracket", "Кронштейн для двух мониторов", "Аксессуары", 7000),
            product("test-opt-ssd", "Накопитель Samsung 990 Pro 2TB", "SSD", 95000),
        ])

    def test_accessory_for_product_is_filtered_without_compiling_per_item(self):
        results = search_engine.search_in_database(
            "ноутбук asus", product_nouns=["ноутбук"], exclude_accessories=False)
        ids = [row["id"] for row in results]
        self.assertIn("test-opt-laptop", ids)
        self.assertNotIn("test-opt-bag", ids)

    def test_plural_forms_are_filtered_too(self):
        results = search_engine.search_in_database(
            "монитор dell", product_nouns=["монитор"], exclude_accessories=False)
        ids = [row["id"] for row in results]
        self.assertIn("test-opt-monitor", ids)
        self.assertNotIn("test-opt-bracket", ids)

    def test_accessory_pattern_is_compiled_once_per_noun_set(self):
        first = search_engine._accessory_pattern_for(("ноутбук",))
        second = search_engine._accessory_pattern_for(("ноутбук",))
        self.assertIs(first, second)
        self.assertTrue(first.search("сумка для ноутбука"))
        self.assertFalse(first.search("ноутбук asus"))

    def test_fallback_like_searches_title_and_category(self):
        # Пустой FTS-запрос заставляет использовать резервный LIKE-поиск.
        # Категория проверяется на латинском значении: SQLite LOWER() не понижает кириллицу,
        # и это ограничение самого SQLite, а не объединённого выражения шага 6.
        with patch.object(search_engine, "build_fts_query", return_value=""):
            by_title = search_engine.search_in_database("asus tuf")
            by_category = search_engine.search_in_database("ssd")
        self.assertIn("test-opt-laptop", [row["id"] for row in by_title])
        self.assertIn("test-opt-ssd", [row["id"] for row in by_category])

    def test_fallback_like_combines_title_and_category_without_duplicating_params(self):
        with patch.object(search_engine, "build_fts_query", return_value=""):
            results = search_engine.search_in_database("Dell UltraSharp")
        self.assertEqual([row["id"] for row in results], ["test-opt-monitor"])

    def test_relevance_prefers_exact_phrase_and_penalizes_long_titles(self):
        exact = {"title": "iPhone 15 128GB", "category": "Смартфоны"}
        long_title = {"title": "iPhone 15 128GB чехол силиконовый прозрачный с защитой камеры",
                      "category": "Чехлы"}
        exact_score = search_engine.score_relevance(exact, "iphone 15", ["iphone", "15"])
        long_score = search_engine.score_relevance(long_title, "iphone 15", ["iphone", "15"])
        self.assertGreater(exact_score, long_score)

    def test_relevance_counts_category_match(self):
        in_category = {"title": "Galaxy S24", "category": "Смартфоны"}
        without = {"title": "Galaxy S24", "category": "Прочее"}
        self.assertGreater(
            search_engine.score_relevance(in_category, "смартфон", ["смартфон"]),
            search_engine.score_relevance(without, "смартфон", ["смартфон"]))

    def test_relevance_survives_missing_category(self):
        score = search_engine.score_relevance({"title": "iPhone 15", "category": None},
                                              "iphone", ["iphone"])
        self.assertGreater(score, 0)


if __name__ == "__main__":
    unittest.main()
