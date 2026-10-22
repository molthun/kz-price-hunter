"""Граничные случаи детектора: скомпилированные regex, уценка с «sn:», фильтры против скидок.

Проверяем, что ускорение детектора не изменило поведения на краях: границы слов,
витринные серийники, аксессуары с падением цены и легитимная глубокая скидка.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest

from detector import check_anomaly, is_junk_accessory, is_used_goods


class JunkAccessoryCompiledRegexTest(unittest.TestCase):
    def test_junk_words_are_caught_whole_word(self):
        self.assertTrue(is_junk_accessory("Чехол для iPhone 15 Pro"))
        self.assertTrue(is_junk_accessory("Защитное стекло Samsung Galaxy S24"))
        self.assertTrue(is_junk_accessory("Кабель Type-C USB"))
        self.assertTrue(is_junk_accessory("Браслет S&M для Apple Watch сталь нержавеющая"))
        self.assertTrue(is_junk_accessory("WiWU Comf Secur", "WiWU Ремешки для Apple Watch"))

    def test_normal_goods_are_not_junk(self):
        self.assertFalse(is_junk_accessory("Смартфон Apple iPhone 15 Pro 256GB Черный"))
        self.assertFalse(is_junk_accessory("15.6\" Ноутбук ASUS TUF Gaming"))
        self.assertFalse(is_junk_accessory("Фитнес-браслет Xiaomi Smart Band 9"))

    def test_word_boundary_does_not_match_inside_another_word(self):
        self.assertFalse(is_junk_accessory("Супер-пупер стеклоочиститель для авто"))

    def test_custom_stop_words_still_work(self):
        self.assertTrue(is_junk_accessory("Коврик для мыши", custom_keywords=["коврик"]))
        self.assertFalse(is_junk_accessory("Коврик для мыши", custom_keywords=["  "]))


class UsedGoodsTest(unittest.TestCase):
    def test_used_and_showcase_markers(self):
        self.assertTrue(is_used_goods("iPhone 13 128GB (sn:12345)"))
        self.assertTrue(is_used_goods("MacBook Pro 14 M3, уцененный, витринный образец"))
        self.assertTrue(is_used_goods("Ноутбук после ремонта"))
        self.assertTrue(is_used_goods("iPhone 14 б/у"))
        self.assertTrue(is_used_goods("Samsung S23 восстановленный"))

    def test_false_positives_are_not_used(self):
        self.assertFalse(is_used_goods("Новый ноутбук ASUS в заводской упаковке, sn-кабель в комплекте"))
        self.assertFalse(is_used_goods("Смартфон Xiaomi 14T 512GB"))


class AnomalyFilteringTest(unittest.TestCase):
    # Пустые настройки берут документированные умолчания детектора и не зависят от settings.json.
    SETTINGS = {}

    def test_junk_accessory_with_price_collapse_is_not_an_anomaly(self):
        product = {
            "title": "Чехол для видеокарты RTX 4090",
            "current_price": 1500,
            "old_price_on_site": 15000,
        }
        history = {"old_price": 15000, "first_seen_price": 15000}
        self.assertIsNone(check_anomaly(product, history, custom_settings=self.SETTINGS))

    def test_used_goods_with_price_collapse_is_not_an_anomaly(self):
        product = {
            "title": "Ноутбук ASUS (уцененный товар)",
            "current_price": 60000,
            "old_price_on_site": 200000,
        }
        history = {"old_price": 200000, "first_seen_price": 200000}
        self.assertIsNone(check_anomaly(product, history, custom_settings=self.SETTINGS))

    def test_legitimate_super_discount_is_detected(self):
        product = {
            "title": "Видеокарта Palit GeForce RTX 4060 Dual 8GB",
            "current_price": 35000,
            "old_price_on_site": 100000,
        }
        history = {"old_price": 100000, "first_seen_price": 100000}
        result = check_anomaly(product, history, custom_settings=self.SETTINGS)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "SUPER_DISCOUNT")
        self.assertEqual(result["drop_pct"], 65.0)
        self.assertEqual(result["savings"], 65000)


if __name__ == "__main__":
    unittest.main()
