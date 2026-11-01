from tests import test_support  # must be first
import unittest
from types import SimpleNamespace

from domain.product_options import build_options, offer_options, title_color


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
        self.assertEqual(options[0]["key"], "color")
        values = {v["value"]: v for v in options[0]["values"]}
        self.assertEqual(values["black"]["min_price"], 90)
        self.assertEqual(values["black"]["offers_count"], 2)
        self.assertIsNone(values["red"]["min_price"])
        self.assertEqual(options[0]["values"][-1]["value"], "red")

    def test_single_color_gives_no_choice(self):
        self.assertEqual(build_options([self._offer("X Black", 100), self._offer("X", 90)]), [])


if __name__ == "__main__":
    unittest.main()
