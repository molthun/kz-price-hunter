"""Тесты для иерархии категорий, органического наполнения каталога и безопасного сброса."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from search_engine import determine_category_and_master
from scripts.reset_catalog import reset_catalog


class CategoryHierarchyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_prices.db"
        self.patcher = patch.object(database, "DB_PATH", self.db_path)
        self.patcher.start()
        database.init_db()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_determine_retail_categories(self):
        cat, master = determine_category_and_master("Перфоратор Bosch 800W", "перфоратор")
        self.assertEqual(master, "diy")

        cat, master = determine_category_and_master("Кофе Jacobs Monarch 250г", "кофе")
        self.assertEqual(master, "grocery")

        cat, master = determine_category_and_master("Стиральный порошок Ariel 3кг", "порошок")
        self.assertEqual(master, "household")

        cat, master = determine_category_and_master("Супер скидка Распродажа года", "распродажа")
        self.assertEqual(master, "actions")

    def test_hierarchical_tree_and_parent_reassignment(self):
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO tracked_categories (id, name, query, master_category, search_count, is_active, is_hot)
                VALUES (101, 'iPhone 15', 'iphone 15', 'smartphones', 5, 1, 0),
                       (102, 'Перфораторы', 'перфоратор', 'diy', 2, 1, 0),
                       (103, 'Неизвестная группа', 'что-то', NULL, 1, 1, 0)
            """)
            conn.commit()

        tree = database.get_hierarchical_categories()
        groups_by_id = {g["id"]: g for g in tree["groups"]}
        self.assertIn("smartphones", groups_by_id)
        self.assertIn("diy", groups_by_id)

        smartphones_subs = [s["name"] for s in groups_by_id["smartphones"]["subcategories"]]
        self.assertIn("iPhone 15", smartphones_subs)

        unassigned_names = [u["name"] for u in tree["unassigned"]]
        self.assertIn("Неизвестная группа", unassigned_names)

        # Переназначаем родителя для 'Неизвестная группа' в audio
        database.set_tracked_category_parent(103, "audio")
        tree_after = database.get_hierarchical_categories()
        groups_after = {g["id"]: g for g in tree_after["groups"]}
        audio_subs = [s["name"] for s in groups_after["audio"]["subcategories"]]
        self.assertIn("Неизвестная группа", audio_subs)
        self.assertEqual(len(tree_after["unassigned"]), 0)

    def test_reset_catalog_preserves_users_and_creates_actions_hot(self):
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO users (id, username) VALUES (123, 'admin_user')")
            cur.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ('tok1', 123, 9999999999)")
            cur.execute("""
                INSERT INTO products (id, title, current_price, first_seen_price, min_price, max_price, shop, category, url)
                VALUES ('p1', 'Тестовый товар', 1000, 1000, 1000, 1000, 'DNS', 'smartphones', 'http://example.com')
            """)
            conn.commit()

        res = reset_catalog(backup=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["deleted_products"], 1)
        self.assertEqual(res["preserved_users"], 1)
        self.assertEqual(res["preserved_sessions"], 1)
        self.assertTrue(Path(res["backup_path"]).exists())

        # Проверяем, что в tracked_categories создана горячая категория акций
        tracked = database.get_tracked_categories()
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["master_category"], "actions")
        self.assertEqual(tracked[0]["is_hot"], 1)

        # Проверяем, что пользователи целы
        with database.get_connection() as conn:
            u_count = conn.execute("SELECT count(*) FROM users").fetchone()[0]
            s_count = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
            p_count = conn.execute("SELECT count(*) FROM products").fetchone()[0]
            self.assertEqual(u_count, 1)
            self.assertEqual(s_count, 1)
            self.assertEqual(p_count, 0)


if __name__ == "__main__":
    unittest.main()
