"""Тесты для интеллектуальной классификации категорий (AI + расширенная эвристика)."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

from aiohttp.test_utils import TestClient, TestServer

import auth
import database
import web.server as server
from ai_service import classify_categories_batch_ai
from config import MASTER_CATEGORIES
from search_engine import determine_category_and_master


class AiCategoryHeuristicsTest(unittest.TestCase):
    def test_new_master_categories_presence(self):
        self.assertIn("clothes", MASTER_CATEGORIES)
        self.assertIn("pets", MASTER_CATEGORIES)
        self.assertIn("home_furniture", MASTER_CATEGORIES)
        self.assertIn("beauty_health", MASTER_CATEGORIES)

    def test_determine_category_and_master_heuristics(self):
        # 1. Носки для девочек -> clothes
        cat, master = determine_category_and_master("Носки для девочек", "акции", "Носки для девочек")
        self.assertEqual(master, "clothes")
        self.assertEqual(cat, "Одежда и обувь")

        # 2. Мед натуральный -> grocery
        cat, master = determine_category_and_master("Мед натуральный цветочный", "акции", "Мед")
        self.assertEqual(master, "grocery")
        self.assertEqual(cat, "Продукты и бакалея")

        # 3. Емкости для хранения продуктов -> home_furniture
        cat, master = determine_category_and_master("Емкости для хранения продуктов", "акции", "Емкости для хранения продуктов")
        self.assertEqual(master, "home_furniture")
        self.assertEqual(cat, "Дом, мебель и уют")

        # 4. Наполнители туалета для животных -> pets
        cat, master = determine_category_and_master("Наполнители туалета для животных", "акции", "Наполнители туалета для животных")
        self.assertEqual(master, "pets")
        self.assertEqual(cat, "Зоотовары")

        # 5. Красота и здоровье -> beauty_health
        cat, master = determine_category_and_master("Шампунь для волос восстанавливающий", "шампунь", "Косметика")
        self.assertEqual(master, "beauty_health")
        self.assertEqual(cat, "Красота и здоровье")

        # 6. Проверка хлопковых носков (не должны попадать в ПК из-за подстроки 'пк')
        cat, master = determine_category_and_master("Хлопковые носки черные 3 пары", "носки", "")
        self.assertEqual(master, "clothes")
        self.assertNotEqual(master, "laptops")

        # 7. Компьютер или реальный ПК -> laptops
        cat, master = determine_category_and_master("Игровой ПК Core i5 RTX 4060", "пк", "")
        self.assertEqual(master, "pc_components" if "rtx" in "Игровой ПК Core i5 RTX 4060".lower() else "laptops")


class AiCategoryBatchClassificationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_batch.db"
        self.patcher = patch.object(database, "DB_PATH", self.db_path)
        self.patcher.start()
        database.init_db()

    async def asyncTearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    async def test_batch_classification_heuristic_fallback(self):
        categories = [
            {"id": 78, "name": "Носки для девочек", "query": "акции"},
            {"id": 79, "name": "Мед", "query": "акции"},
            {"id": 80, "name": "Емкости для хранения продуктов", "query": "акции"},
            {"id": 81, "name": "Наполнители туалета для животных", "query": "акции"}
        ]
        # При пустых AI-ключах должна отработать эвристика
        with patch("ai_service._get_api_credentials", return_value={"gemini_api_key": "", "openai_api_key": ""}):
            result = await classify_categories_batch_ai(categories)
            self.assertEqual(result.get(78), "clothes")
            self.assertEqual(result.get(79), "grocery")
            self.assertEqual(result.get(80), "home_furniture")
            self.assertEqual(result.get(81), "pets")

    async def test_batch_classification_ai_mock(self):
        categories = [
            {"id": 101, "name": "Неизвестная экзотическая вещь", "query": "экзотика"}
        ]
        mock_response = {
            "mappings": [
                {"id": 101, "master_category": "home_furniture"}
            ]
        }
        with patch("ai_service._get_api_credentials", return_value={"gemini_api_key": "test-key", "openai_api_key": ""}), \
             patch("ai_service.call_gemini_api", new_callable=AsyncMock, return_value=mock_response):
            result = await classify_categories_batch_ai(categories)
            self.assertEqual(result.get(101), "home_furniture")


class AdminCategoryAiClassifyApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "test_classify.db"
        self.patcher = patch.object(database, "DB_PATH", self.db_path)
        self.patcher.start()
        database.init_db()

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO tracked_categories (id, name, query, master_category, search_count, is_active, is_hot)
                VALUES (78, 'Носки для девочек', 'акции', NULL, 1, 1, 0),
                       (79, 'Мед', 'акции', NULL, 1, 1, 0),
                       (80, 'Емкости для хранения продуктов', 'акции', NULL, 1, 1, 0),
                       (81, 'Наполнители туалета для животных', 'акции', NULL, 1, 1, 0)
            """)
            conn.commit()

        app = server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = {"Origin": str(self.client.make_url("/")).rstrip("/")}

    async def asyncTearDown(self):
        await self.client.close()
        self.patcher.stop()
        self.tmp.cleanup()

    def login_admin(self):
        admin_id = 999111
        database.upsert_telegram_user({"id": admin_id, "first_name": "Admin"})
        sess = database.create_session(admin_id)
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: sess})

    async def test_guest_forbidden(self):
        resp = await self.client.post("/api/admin/categories/ai-classify", headers=self.origin)
        self.assertEqual(resp.status, 401)

    async def test_admin_ai_classify_endpoint(self):
        self.login_admin()
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {999111}):
            resp = await self.client.post("/api/admin/categories/ai-classify", headers=self.origin)
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["count"], 4)

            # Проверяем, что в БД категории получили свои master_category
            tree = database.get_hierarchical_categories()
            self.assertEqual(len(tree["unassigned"]), 0)

            groups_map = {g["id"]: [s["name"] for s in g["subcategories"]] for g in tree["groups"]}
            self.assertIn("Носки для девочек", groups_map.get("clothes", []))
            self.assertIn("Мед", groups_map.get("grocery", []))
            self.assertIn("Емкости для хранения продуктов", groups_map.get("home_furniture", []))
            self.assertIn("Наполнители туалета для животных", groups_map.get("pets", []))


if __name__ == "__main__":
    unittest.main()
