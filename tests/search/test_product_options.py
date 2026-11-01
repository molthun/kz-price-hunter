from tests import test_support  # must be first
import unittest
from types import SimpleNamespace

from domain.product_options import (build_family_options, build_options, collapse_family_cards,
                                     family_params, offer_options, title_color)


class ProductOptionsTest(unittest.TestCase):
    def test_title_color(self):
        for title, expected in (
            ("Смартфон Apple iPhone 11 64Gb Black", "black"),
            ("Смарт-часы Garmin Fenix 8 47 мм черно-оранжевый", "black+orange"),
            ("Шкаф Noble 57 см, цвет Белый премиум", "white"),
            ("Смартфон Redmi Note 14 Ocean Blue", "blue"),
            ("Монитор Sanc M2442PH 23.8", None),
            ("Серия Redmi Note", None),
            ("Интернет-магазин Белый Ветер", "white"),
        ):
            with self.subTest(title=title):
                self.assertEqual(title_color(title), expected)

    def _offer(self, title, price, purchasable=True):
        return SimpleNamespace(options=offer_options(title), price=price, is_purchasable=purchasable)

    def test_build_options_groups_and_prices(self):
        options = build_options([
            self._offer("X Black", 100), self._offer("X Black", 90),
            self._offer("X White", 120), self._offer("X", 50), self._offer("X Red", 10, purchasable=False),
        ])
        self.assertEqual(options[0]["id"], "color")
        values = {v["value"]: v for v in options[0]["values"]}
        self.assertEqual(values["black"]["min_price"], 90)
        self.assertEqual(values["black"]["offers_count"], 2)
        self.assertIsNone(values["red"]["min_price"])
        self.assertEqual(options[0]["values"][-1]["value"], "red")

    def test_single_color_gives_no_choice(self):
        self.assertEqual(build_options([self._offer("X Black", 100), self._offer("X", 90)]), [])

    def test_family_params(self):
        self.assertEqual(family_params("Смартфоны", "Смартфон Redmi 10 4/128Gb Sea"), {"storage": "128", "ram": "4"})
        self.assertEqual(family_params("Смартфоны", "iPhone 12 Pro Max 1TB Gold"), {"storage": "1024"})
        self.assertEqual(family_params("Смарт-часы", "Apple Watch Series 10 42mm"), {"size": "42"})
        self.assertEqual(family_params("Ноутбуки", "MacBook Air 13 8/256Gb"), {})

    def test_family_options_lead_to_matching_variant(self):
        siblings = [
            {"id": "a", "title": "X 4/128Gb", "min_price": 100},
            {"id": "b", "title": "X 4/256Gb", "min_price": 150},
            {"id": "c", "title": "X 6/256Gb", "min_price": 140},
        ]
        options = {o["id"]: o for o in build_family_options("a", "Смартфоны", "X 4/128Gb", siblings)}
        storage = {v["value"]: v for v in options["storage"]["values"]}
        self.assertTrue(storage["128"]["current"])
        self.assertEqual(storage["256"]["product_id"], "b")      # та же оперативная память, хотя c дешевле
        self.assertEqual(storage["256"]["min_price"], 140)
        self.assertIn("ram", options)
        self.assertEqual(options["storage"]["values"][1]["label"], "256 ГБ")

    def test_collapse_keeps_cheapest_and_counts_others(self):
        def card(pid, price, category="Смартфоны", model="X"):
            product = SimpleNamespace(id=pid, brand="B", model=model, category=category, item_kind="device")
            return SimpleNamespace(product=product, min_price=price, variants_count=0)
        cards = [card("a", 200), card("n", 50, category="Ноутбуки"), card("b", 100), card("c", 300), card("d", 10, model="Y")]
        out = collapse_family_cards(cards)
        self.assertEqual([c.product.id for c in out], ["b", "n", "d"])
        self.assertEqual(out[0].variants_count, 2)


if __name__ == "__main__":
    unittest.main()
