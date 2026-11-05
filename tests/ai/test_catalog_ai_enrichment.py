from tests import test_support  # noqa: F401
"""
Tests for Catalog AI Enrichment Service (KZPH-G5-06).
Tests deterministic rules, local AI extraction, validation, GPU safety, and DB persistence.
"""
import json
import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from domain.catalog_enrichment_ai import (
    batch_enrich_catalog,
    build_ai_prompt,
    can_use_local_ai,
    enrich_canonical_product,
    extract_attributes_from_rules,
    extract_attributes_with_local_ai,
    DDL_ATTRIBUTES,
)


class TestCatalogAIEnrichment(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE canonical_products (
                id TEXT PRIMARY KEY,
                canonical_key TEXT,
                title TEXT,
                category TEXT,
                brand TEXT,
                model TEXT,
                description TEXT,
                attributes_json TEXT DEFAULT '{}',
                updated_at TEXT
            );
        """)
        self.conn.executescript(DDL_ATTRIBUTES)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_can_use_local_ai_respects_mode_and_gpu(self):
        with patch("local_ai.mode", return_value={"on": False, "label": "выключена"}):
            ok, reason = can_use_local_ai()
            self.assertFalse(ok)
            self.assertIn("выключен", reason)

        with patch("local_ai.mode", return_value={"on": True}), \
             patch("local_ai.gpu_state", return_value={"busy": True, "reason": "игра"}):
            ok, reason = can_use_local_ai()
            self.assertFalse(ok)
            self.assertIn("GPU занят", reason)

        with patch("local_ai.mode", return_value={"on": True}), \
             patch("local_ai.gpu_state", return_value={"busy": False}):
            ok, reason = can_use_local_ai()
            self.assertTrue(ok)
            self.assertEqual(reason, "OK")

    def test_extract_attributes_from_rules_title_and_description(self):
        # 1. Из названия
        title = "Смартфон Apple iPhone 15 Pro Max 256GB Natural Titanium"
        attrs = extract_attributes_from_rules(title)
        self.assertEqual(attrs.get("storage"), "256 ГБ")
        self.assertEqual(attrs.get("color"), "Титановый")

        # 2. Из описания, если в названии нет
        title2 = "Холодильник LG DoorCooling+ GA-B509CLCL"
        desc2 = "Двухкамерный холодильник с общим объемом 340 л, система разморозки Total No Frost, цвет графитовый."
        attrs2 = extract_attributes_from_rules(title2, desc2)
        self.assertEqual(attrs2.get("total_volume"), "340 л")
        self.assertEqual(attrs2.get("refrigeration_defrost"), "No Frost")

        # 3. Стиральная машина
        title3 = "Стиральная машина Samsung WW70T4020EE"
        desc3 = "Фронтальная стиральная машина, максимальная загрузка 7 кг, скорость отжима 1200 об/мин, цвет белый."
        attrs3 = extract_attributes_from_rules(title3, desc3)
        self.assertEqual(attrs3.get("load_capacity"), "7 кг")
        self.assertEqual(attrs3.get("spin_speed"), "1200 об/мин")
        self.assertEqual(attrs3.get("color"), "Белый")

    def test_build_ai_prompt(self):
        prompt = build_ai_prompt("Ноутбук ASUS ROG Strix", description="16 ГБ ОЗУ, 1 ТБ SSD, RTX 4060", category="Ноутбуки")
        self.assertIn("ASUS ROG Strix", prompt)
        self.assertIn("Ноутбуки", prompt)
        self.assertIn("16 ГБ ОЗУ", prompt)
        self.assertIn("storage", prompt)
        self.assertIn("ram", prompt)

    @patch("urllib.request.urlopen")
    def test_extract_attributes_with_local_ai(self, mock_urlopen):
        # Моделируем ответ Ollama API
        fake_response = {
            "response": json.dumps({
                "ram": "16 GB",
                "storage": "512 GB",
                "cpu": "Intel Core i7-13700H",
                "gpu": "RTX 4060",
                "screen_size": "15.6\"",
                "color": "черный",
                "hallucinated_invalid_key": "some value",  # Должно быть отфильтровано
            })
        }
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        with patch("domain.catalog_enrichment_ai.can_use_local_ai", return_value=(True, "OK")):
            res = extract_attributes_with_local_ai(
                title="Игровой ноутбук ASUS TUF Gaming",
                description="Мощный процессор Intel Core i7, 16 ГБ памяти, накопитель 512 ГБ",
                category="Ноутбуки",
            )

        self.assertEqual(res.get("ram"), "16 ГБ")
        self.assertEqual(res.get("storage"), "512 ГБ")
        self.assertEqual(res.get("color"), "Черный")
        self.assertEqual(res.get("screen_size"), "15.6\"")
        self.assertNotIn("hallucinated_invalid_key", res)

    def test_enrich_canonical_product_with_rules(self):
        self.conn.execute("""
            INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, description, attributes_json)
            VALUES ('p_iphone', 'k1', 'Смартфон Apple iPhone 15 128GB Black', 'Смартфоны', 'Apple', 'iPhone 15', 'Отличный смартфон', '{}')
        """)
        self.conn.commit()

        res = enrich_canonical_product(self.conn, "p_iphone", use_ai_if_needed=False)
        self.assertTrue(res["ok"])
        self.assertEqual(res["source"], "rule")
        self.assertIn("Встроенная память", res["attributes"])
        self.assertEqual(res["attributes"]["Встроенная память"], "128 ГБ")
        self.assertEqual(res["attributes"]["Цвет"], "Черный")

        # Проверяем запись в product_search_attributes
        rows = self.conn.execute("SELECT key, value FROM product_search_attributes WHERE product_id = 'p_iphone'").fetchall()
        attr_dict = {r[0]: r[1] for r in rows}
        self.assertEqual(attr_dict["storage"], "128 ГБ")
        self.assertEqual(attr_dict["color"], "Черный")

        # Проверяем обновление canonical_products.attributes_json
        saved_attrs_raw = self.conn.execute("SELECT attributes_json FROM canonical_products WHERE id = 'p_iphone'").fetchone()[0]
        saved_attrs = json.loads(saved_attrs_raw)
        self.assertEqual(saved_attrs["Встроенная память"], "128 ГБ")

    @patch("domain.catalog_enrichment_ai.extract_attributes_with_local_ai")
    def test_enrich_canonical_product_with_ai_fallback(self, mock_ai_extract):
        mock_ai_extract.return_value = {
            "load_capacity": "8 кг",
            "spin_speed": "1400 об/мин",
            "color": "Белый",
        }

        self.conn.execute("""
            INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, description, attributes_json)
            VALUES ('p_washer', 'k2', 'LG F4V5VS0W', 'Стиральные машины', 'LG', 'F4V5VS0W', 'Стиральная машина с загрузкой 8 кг и инвертором', '{}')
        """)
        self.conn.commit()

        with patch("domain.catalog_enrichment_ai.can_use_local_ai", return_value=(True, "OK")):
            res = enrich_canonical_product(self.conn, "p_washer", use_ai_if_needed=True)

        self.assertTrue(res["ok"])
        self.assertIn("Максимальная загрузка", res["attributes"])
        self.assertEqual(res["attributes"]["Максимальная загрузка"], "8 кг")
        self.assertEqual(res["attributes"]["Скорость отжима"], "1400 об/мин")

    def test_batch_enrich_catalog(self):
        self.conn.execute("""
            INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, description, attributes_json)
            VALUES 
                ('p1', 'k1', 'Apple iPhone 14 128GB Blue', 'Смартфоны', 'Apple', 'iPhone 14', '', '{}'),
                ('p2', 'k2', 'Samsung Galaxy S23 256GB Green', 'Смартфоны', 'Samsung', 'Galaxy S23', '', '{}'),
                ('p3', 'k3', 'Простой товар без характеристик', 'Прочее', 'Brand', 'Model', '', '{}')
        """)
        self.conn.commit()

        stats = batch_enrich_catalog(self.conn, limit=10, use_ai=False)
        self.assertEqual(stats["total_candidates"], 3)
        self.assertEqual(stats["processed"], 3)
        self.assertEqual(stats["enriched"], 2)
        self.assertEqual(stats["by_rule"], 2)
        self.assertEqual(stats["empty"], 1)


if __name__ == "__main__":
    unittest.main()
