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
        self.assertEqual(pk.variant_tokens([{"value": 1, "unit": "ТБ"}]), ["1tb"])
        self.assertEqual(pk.variant_tokens([{"value": 1000, "unit": "гб"}]), ["1tb"])
        self.assertEqual(pk.variant_tokens([{"value": 55, "unit": "дюйм"}, {"value": 8, "unit": "гб"}]), ["55in", "8gb"])
        # Габариты и частоты в ключ не входят (замер C2), диагональ в сантиметрах — в дюймах
        self.assertEqual(pk.variant_tokens([{"value": 30, "unit": "см"}]), [])
        self.assertEqual(pk.variant_tokens([{"value": 3500, "unit": "гц"}]), [])
        self.assertEqual(pk.variant_tokens([{"value": 109, "unit": "см"}]), ["43in"])

    def test_sku_and_edition_not_glued_into_model(self):
        a = {"brand": "ASRock", "model": "B850 PRO RS 90-MXBQK0", "sku": "90-MXBQK0", "variants": []}
        b = {"brand": "ASRock", "model": "B850 Pro RS", "sku": None, "variants": []}
        self.assertEqual(pk.build_key(a), pk.build_key(b))
        c = {"brand": "Afox", "model": "GeForce GTX 1050 Ti Gaming", "edition": "Gaming", "variants": []}
        d = {"brand": "AFOX", "model": "GTX 1050Ti", "variants": []}
        self.assertEqual(pk.build_key(c), pk.build_key(d))
        self.assertEqual(pk.brand_token("Яндекс"), pk.brand_token("Yandex"))

    def test_capacity_and_diagonal_from_title(self):
        """Модель через раз пропускает объём в variants — код берёт его из названия (замер C2)."""
        bare = {"brand": "Kingston", "model": "NV3", "variants": []}
        full = {"brand": "Kingston", "model": "NV3", "variants": [{"value": 2048, "unit": "гб"}]}
        self.assertEqual(pk.build_key(bare, "SSD накопитель 2 TB Kingston NV3, M.2 2230"),
                         pk.build_key(full, "Твердотельный накопитель SSD Kingston NV3 2TB"))
        self.assertEqual(pk.build_key(bare, "SSD Kingston NV3 2Tb"), "k2:kingston:nv3:2tb")
        # 1000 ГБ и 1 ТБ — один объём; 2.5" у диска — не диагональ
        self.assertEqual(pk.build_key(bare, 'SSD 2.5" Kingston NV3 1000GB'), "k2:kingston:nv3:1tb")
        tv = {"brand": "Konka", "model": "KT-Q682WB", "variants": []}
        self.assertEqual(pk.build_key(tv, 'Телевизор 55" (140 см) Konka KT-Q682WB'), "k2:konka:ktq682wb:55in")
        self.assertEqual(pk.build_key(tv, "Konka KT-Q682WB 140 см черный"), "k2:konka:ktq682wb:55in")
        # Диагональ, уже входящая в номер модели, не дублируется: одни магазины её пишут, другие нет
        hisense = {"brand": "Hisense", "model": "65E7S", "variants": []}
        self.assertEqual(pk.build_key(hisense, 'Телевизор Hisense 65" QLED 65E7S'),
                         pk.build_key(hisense, "Телевизор Hisense 65E7S RU"))
        phone = {"brand": "Vivo", "model": "V25E", "variants": []}
        self.assertEqual(pk.build_key(phone, "Смартфон Vivo V25E 8/128Gb"), "k2:vivo:v25e:128gb+8gb")
        self.assertEqual(pk.brand_token("Kingston Technology"), "kingston")
        self.assertEqual(pk.brand_token("Patriot Memory"), "patriot")
        # Остальные варианты от модели сохраняются
        kettle = {"brand": "Tefal", "model": "KO1501", "variants": [{"value": 1.7, "unit": "л"}]}
        self.assertEqual(pk.build_key(kettle, "Чайник Tefal KO1501 1,7 л"), "k2:tefal:ko1501:1.7l")
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
