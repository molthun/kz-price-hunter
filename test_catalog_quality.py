"""P09 Качество каталога: фасовка как часть личности товара, замер на проверочном наборе, теневой отчёт."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import catalog_quality as cq
import database
from config import DB_PATH

GOLDEN = json.loads((pathlib.Path(__file__).parent / "docs" / "golden_matching.json").read_text(encoding="utf-8"))


class QuantityTest(unittest.TestCase):
    def test_volume_and_weight_are_normalized(self):
        self.assertEqual(cq.quantity("Напиток Coca-Cola 1 л"), {"unit": "ml", "value": 1000.0})
        self.assertEqual(cq.quantity("Кока-Кола 1000 мл"), {"unit": "ml", "value": 1000.0})
        self.assertEqual(cq.quantity("Мука Цесна 2 кг"), {"unit": "g", "value": 2000.0})
        self.assertEqual(cq.quantity("Корм Whiskas 85 гр"), {"unit": "g", "value": 85.0})
        self.assertEqual(cq.quantity("Кабель USB-C 2 м"), {"unit": "m", "value": 2.0})

    def test_specifications_are_not_packaging(self):
        """Память, диагональ и проценты жирности фасовкой не являются."""
        self.assertIsNone(cq.quantity("Смартфон Apple iPhone 15 128GB"))
        self.assertIsNone(cq.quantity("Телевизор Samsung 55\" 4K"))
        self.assertEqual(cq.quantity("Молоко 2.5% 900 мл"), {"unit": "ml", "value": 900.0})

    def test_pack_count(self):
        self.assertEqual(cq.pack_count("Туалетная бумага Zewa 4 шт"), 4)
        self.assertEqual(cq.pack_count("Салфетки Huggies 64 штуки"), 64)
        self.assertEqual(cq.pack_count("Вода Тассай 0.5 л х6"), 6)
        self.assertEqual(cq.pack_count("Набор из 3 кружек"), 3)
        self.assertIsNone(cq.pack_count("Смартфон Apple iPhone 15 128GB"))   # не найдено — не «1»

    def test_different_packaging_is_not_comparable(self):
        cases = [("Напиток Coca-Cola 1 л", "Напиток Coca-Cola 1.5 л"),
                 ("Корм для кошек Whiskas 75 г", "Корм для кошек Whiskas 85 г"),
                 ("Туалетная бумага Zewa 4 шт", "Туалетная бумага Zewa 8 шт"),
                 ("Кабель USB-C 1 м", "Кабель USB-C 2 м")]
        for left, right in cases:
            with self.subTest(left=left):
                ok, reason = cq.comparable(left, right)
                self.assertFalse(ok)
                self.assertTrue(reason)

    def test_same_packaging_written_differently_is_comparable(self):
        self.assertTrue(cq.comparable("Coca-Cola 1 л", "Кока-Кола 1000 мл")[0])
        self.assertTrue(cq.comparable("Мука 2 кг", "Мука 2000 г")[0])

    def test_unknown_packaging_is_not_a_conflict_but_stops_the_comparison(self):
        """Прямого противоречия нет, но сравнивать вслепую нельзя: 85 г может оказаться 75 г."""
        self.assertTrue(cq.comparable("Coca-Cola 1 л", "Coca-Cola")[0])
        unsure, reason = cq.uncertain("Coca-Cola 1 л", "Coca-Cola")
        self.assertTrue(unsure)
        self.assertIn("только у одного", reason)
        self.assertFalse(cq.same_product("Coca-Cola 1 л", "Coca-Cola"))


class GoldenDatasetTest(unittest.TestCase):
    """Пороги согласованы с владельцем ДО замера: точность ≥ 99 %, полнота ≥ 70 %."""

    def test_thresholds_are_the_agreed_ones(self):
        self.assertEqual(GOLDEN["agreed_thresholds"]["precision"], cq.PRECISION_TARGET)
        self.assertEqual(GOLDEN["agreed_thresholds"]["recall"], cq.RECALL_FLOOR)

    def test_quality_meets_the_agreed_thresholds(self):
        result = cq.evaluate(GOLDEN["pairs"])
        self.assertGreaterEqual(result["precision"], cq.PRECISION_TARGET,
                                f"ложные сравнения: {result['false_positives']}")
        self.assertGreaterEqual(result["recall"], cq.RECALL_FLOOR,
                                f"пропуски: {result['false_negatives']}")

    def test_dangerous_pairs_are_never_matched(self):
        """Опасные случаи проверяются поимённо: память, SIM, вариант, фасовка, упаковка, аксессуар."""
        dangerous = [p for p in GOLDEN["pairs"] if not p["same"]]
        self.assertGreaterEqual(len(dangerous), 20)
        for pair in dangerous:
            with self.subTest(group=pair["group"], left=pair["left"]):
                self.assertFalse(cq.same_product(pair["left"], pair["right"]))

    def test_new_rule_beats_the_old_one_on_packaging(self):
        """Сравнение «до и после»: прежнее правило путало фасовки, новое — нет."""
        from model_matching import same_model
        packaging = [p for p in GOLDEN["pairs"] if p["group"] in ("фасовка", "упаковка")]
        old_errors = sum(1 for p in packaging if same_model(p["left"], p["right"]) != p["same"])
        new_errors = sum(1 for p in packaging if cq.same_product(p["left"], p["right"]) != p["same"])
        self.assertLess(new_errors, old_errors)
        self.assertEqual(new_errors, 0)

    def test_dataset_is_versioned_and_explains_itself(self):
        self.assertGreaterEqual(GOLDEN["version"], 1)
        self.assertIn("about", GOLDEN)
        self.assertIn("known_limitations", GOLDEN)


class ShadowReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def save(self, rows):
        database.save_or_update_products_batch(rows)

    def test_different_packaging_is_not_used_as_a_competitor(self):
        """Кола 1,5 л в другом магазине больше не удешевляет колу 1 л."""
        self.save([
            {"id": "a1", "title": "Напиток Coca-Cola 1 л", "price": 700, "shop": "Arbuz", "city": "Астана",
             "url": "https://a/1", "category": "Продукты"},
            {"id": "z1", "title": "Напиток Coca-Cola 1.5 л", "price": 500, "shop": "Zeta", "city": "Астана",
             "url": "https://z/1", "category": "Продукты"},
        ])
        self.assertIsNone(database.find_market_comparisons("Напиток Coca-Cola 1 л", "Arbuz", 700, "Астана"))
        shadow = database.matching_shadow(days=1)
        self.assertEqual(shadow["uncertain"], 0)
        self.assertEqual(shadow["blocked"], 1)          # видно, что именно фасовка развела эти предложения
        self.assertIn("1 л и 1.5 л", shadow["rows"][0]["reason"])

    def test_same_product_in_another_shop_is_found(self):
        self.save([
            {"id": "a2", "title": "Напиток Coca-Cola 1 л", "price": 700, "shop": "Arbuz", "city": "Астана",
             "url": "https://a/2", "category": "Продукты"},
            {"id": "z2", "title": "Coca-Cola 1000 мл", "price": 500, "shop": "Zeta", "city": "Астана",
             "url": "https://z/2", "category": "Продукты"},
        ])
        found = database.find_market_comparisons("Напиток Coca-Cola 1 л", "Arbuz", 700, "Астана")
        self.assertIsNotNone(found)
        self.assertEqual(found["min_price"], 500)
        self.assertEqual(found["cheapest_shop"], "Zeta")

    def test_uncertain_pair_is_recorded_and_not_compared(self):
        self.save([
            {"id": "a3", "title": "Корм Whiskas говядина 85 г", "price": 500, "shop": "Arbuz", "city": "Астана",
             "url": "https://a/3", "category": "Зоотовары"},
            {"id": "z3", "title": "Корм Whiskas говядина", "price": 400, "shop": "Zeta", "city": "Астана",
             "url": "https://z/3", "category": "Зоотовары"},
        ])
        # Фасовку указал только один магазин: сравнение не делается, случай записан для разбора
        self.assertIsNone(database.find_market_comparisons("Корм Whiskas говядина 85 г", "Arbuz", 500, "Астана"))
        shadow = database.matching_shadow(days=1)
        self.assertGreaterEqual(shadow["uncertain"], 1)
        self.assertIn("только у одного", shadow["rows"][0]["reason"])

    def test_shadow_report_is_grouped_and_capped(self):
        for _ in range(5):
            database.record_matching_shadow("blocked", "разная фасовка: 1 л и 1.5 л",
                                            "Coca-Cola 1 л", "Coca-Cola 1.5 л")
        shadow = database.matching_shadow(days=1)
        self.assertEqual(shadow["blocked"], 5)
        self.assertEqual(len(shadow["rows"]), 1)        # одинаковые причины сгруппированы
        self.assertLessEqual(len(shadow["rows"][0]["example_left"]), 200)

    def test_shadow_failure_never_breaks_comparison(self):
        with patch.object(database, "get_connection", side_effect=RuntimeError("база занята")):
            database.record_matching_shadow("blocked", "причина", "a", "b")   # не должно бросить


if __name__ == "__main__":
    unittest.main()
