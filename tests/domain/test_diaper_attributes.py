from tests import test_support  # noqa: F401
import sqlite3
import unittest

from domain.attribute_indexing import extract_title_attributes, normalize_attribute_value, index_product_facts, ATTRIBUTE_LABELS
from product_evidence import enrich_facts_with_canonical_attributes


class TestDiaperAttributes(unittest.TestCase):
    def test_diaper_title_attribute_extraction(self):
        cases = [
            (
                "Трусики Joonies Premium Soft M (6-11 кг) 58 шт.",
                {
                    "diaper_type": "Подгузники-трусики",
                    "diaper_size": "3 (M)",
                    "weight_range": "6-11 кг",
                    "pieces_count": "58 шт.",
                }
            ),
            (
                "Подгузники Joonies Premium Soft для новорожденных NB (до 5 кг) 24 шт.",
                {
                    "diaper_type": "Подгузники",
                    "diaper_size": "0 (NB)",
                    "weight_range": "до 5 кг",
                    "pieces_count": "24 шт.",
                }
            ),
            (
                "Huggies трусики 6 (16-22 кг) для девочек № 44 шт",
                {
                    "diaper_type": "Подгузники-трусики",
                    "diaper_size": "6 (XXL)",
                    "weight_range": "16-22 кг",
                    "pieces_count": "44 шт.",
                }
            ),
            (
                "Трусики Joonies Magic Night (ночные) M, 24 шт",
                {
                    "diaper_type": "Ночные трусики",
                    "diaper_size": "3 (M)",
                    "pieces_count": "24 шт.",
                }
            ),
            (
                "Подгузники-трусики Pampers 7 17кг+ 34 шт",
                {
                    "diaper_type": "Подгузники-трусики",
                    "diaper_size": "7 (XXXL)",
                    "weight_range": "17+ кг",
                    "pieces_count": "34 шт.",
                }
            ),
            (
                "Huggies подгузники Ultra Comfort 4 (8-14кг) для мальчиков № 19 шт",
                {
                    "diaper_type": "Подгузники",
                    "diaper_size": "4 (L)",
                    "weight_range": "8-14 кг",
                    "pieces_count": "19 шт.",
                }
            ),
            (
                "YokoSun подгузники S для детей до 6 кг п/эт пакет № 26 шт",
                {
                    "diaper_type": "Подгузники",
                    "diaper_size": "2 (S)",
                    "weight_range": "до 6 кг",
                    "pieces_count": "26 шт.",
                }
            ),
            (
                "Подгузники-трусики Manu 5 (12-17 кг) 40 шт.",
                {
                    "diaper_type": "Подгузники-трусики",
                    "diaper_size": "5 (XL)",
                    "weight_range": "12-17 кг",
                    "pieces_count": "40 шт.",
                }
            ),
            (
                "Подгузники-трусики Мама Знает XXL, 42 шт",
                {
                    "diaper_type": "Подгузники-трусики",
                    "diaper_size": "6 (XXL)",
                    "pieces_count": "42 шт.",
                }
            ),
        ]

        for title, expected in cases:
            with self.subTest(title=title):
                attrs = extract_title_attributes(title)
                for k, v in expected.items():
                    self.assertIn(k, attrs, f"Key {k} not found in {attrs} for '{title}'")
                    self.assertEqual(attrs[k], v, f"Mismatch for {k} in '{title}': got {attrs[k]}, expected {v}")

    def test_indexing_and_enriching_facts(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE canonical_products (
                id TEXT PRIMARY KEY,
                title TEXT,
                category TEXT,
                brand TEXT,
                model TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE product_search_attributes (
                product_id TEXT,
                key TEXT,
                value TEXT,
                evidence TEXT,
                source_title TEXT,
                updated_at TEXT,
                PRIMARY KEY (product_id, key)
            )
        """)

        pid = "prod_joonies_m58"
        title = "Трусики Joonies Premium Soft M (6-11 кг) 58 шт."
        conn.execute(
            "INSERT INTO canonical_products VALUES (?, ?, ?, ?, ?)",
            (pid, title, "Подгузники и гигиена", "Joonies", "Premium Soft")
        )

        indexed = index_product_facts(conn, pid, [], title)
        self.assertGreaterEqual(indexed, 3)

        rows = conn.execute("SELECT key, value FROM product_search_attributes WHERE product_id = ?", (pid,)).fetchall()
        attr_dict = {r[0]: r[1] for r in rows}

        self.assertEqual(attr_dict.get("diaper_type"), "Подгузники-трусики")
        self.assertEqual(attr_dict.get("diaper_size"), "3 (M)")
        self.assertEqual(attr_dict.get("weight_range"), "6-11 кг")
        self.assertEqual(attr_dict.get("pieces_count"), "58 шт.")

        # Test facts enrichment for product details / specifications table
        enriched = enrich_facts_with_canonical_attributes(conn, pid, [], title)
        fact_names = {f["name"] for f in enriched}
        self.assertIn("Бренд", fact_names)
        self.assertIn("Модель", fact_names)
        self.assertIn("Тип", fact_names)
        self.assertIn("Размер", fact_names)
        self.assertIn("Вес ребенка", fact_names)
        self.assertIn("Количество в упаковке", fact_names)

        # Values check
        facts_by_name = {f["name"]: f["value"] for f in enriched}
        self.assertEqual(facts_by_name["Бренд"], "Joonies")
        self.assertEqual(facts_by_name["Модель"], "Premium Soft")
        self.assertEqual(facts_by_name["Тип"], "Подгузники-трусики")
        self.assertEqual(facts_by_name["Размер"], "3 (M)")
        self.assertEqual(facts_by_name["Вес ребенка"], "6-11 кг")
        self.assertEqual(facts_by_name["Количество в упаковке"], "58 шт.")


if __name__ == "__main__":
    unittest.main()
