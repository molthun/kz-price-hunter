from tests import test_support  # noqa: F401
"""P17-C1: стандарт ключа товара — один ключ для одних и тех же полей, как бы их ни записала модель."""
import unittest

import product_key as pk


class SameFieldsSameKeyTest(unittest.TestCase):
    def test_different_model_spellings_give_one_key(self):
        """Так одно и то же извлекают разные модели — ключ обязан совпасть."""
        writings = [
            {"brand": "Samsung", "model": "Galaxy S24 Ultra", "variants": [{"value": 256, "unit": "ГБ"}]},
            {"brand": "самсунг", "model": "galaxy s24-ultra", "variants": ["256 gb"]},
            {"brand": "SAMSUNG", "model": "Samsung Galaxy S24 Ultra Black", "variants": [{"value": "256", "unit": "gb"}]},
            {"brand": " samsung ", "model": "GALAXY  S24 ULTRA", "variants": [{"value": 0.25, "unit": "TB"}, {"value": 256, "unit": "Gb"}]},
        ]
        keys = {pk.build_key(w) for w in writings}
        # 0,25 ТБ = 256 ГБ: после приведения единиц вариант тот же, ключ один
        self.assertEqual(keys, {"k2:samsung:galaxys24ultra:256gb"})

    def test_units_and_numbers(self):
        self.assertEqual(pk.variant_tokens([{"value": "0,96", "unit": "л"}]), ["0.96l"])
        self.assertEqual(pk.variant_tokens([{"value": 1, "unit": "ТБ"}]), ["1024gb"])
        self.assertEqual(pk.variant_tokens([{"value": 55, "unit": "дюйм"}, {"value": 8, "unit": "гб"}]), ["55in", "8gb"])
        self.assertEqual(pk.variant_tokens([{"value": 30, "unit": "см"}]), ["300mm"])
        self.assertEqual(pk.variant_tokens([{"value": 0, "unit": "гб"}, {"value": 5, "unit": "попугаев"}, "много"]), [])

    def test_empty_and_unbranded_give_no_key(self):
        for fields in ({"brand": None, "model": "x1"}, {"brand": "0", "model": "x1"}, {"brand": "null", "model": "x1"},
                       {"brand": "без бренда", "model": "вешалка"}, {"brand": "Bosch", "model": None, "sku": "0"},
                       {"brand": "Bosch", "model": "x", "unbranded": True}, "строка", None):
            with self.subTest(fields=fields):
                self.assertIsNone(pk.build_key(fields))

    def test_sku_when_no_model_and_translit_brand(self):
        self.assertEqual(pk.build_key({"brand": "Bestway", "model": None, "sku": "55001"}), "k2:bestway:55001")
        self.assertEqual(pk.build_key({"brand": "Китфорт", "model": "KT-1351"}), "k2:kitfort:kt1351")
        self.assertEqual(pk.build_key({"brand": "Ақсу", "model": "Модель 5"}), "k2:aksu:model5")
        self.assertEqual(pk.build_key({"brand": "LG Electronics", "model": "27GP850-B"}), "k2:lg:27gp850b")

    def test_no_placeholder_leaks_into_key(self):
        key = pk.build_key({"brand": "SJCAM", "model": "C300", "variants": [{"value": None, "unit": "гб"}]})
        self.assertEqual(key, "k2:sjcam:c300")
        self.assertNotIn("null", key)

    def test_keys_for_only_batch_titles(self):
        items = [{"title": "A", "brand": "Apple", "model": "iPhone 15"}, {"title": "чужое", "brand": "X", "model": "1"},
                 {"title": "B", "brand": None}]
        self.assertEqual(pk.keys_for(items, ["A", "B"]), {"A": "k2:apple:iphone15", "B": None})


if __name__ == "__main__":
    unittest.main()
