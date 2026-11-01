from tests import test_support  # must be first
import datetime
import sqlite3
import unittest
from types import SimpleNamespace

from domain.product_options import (build_family_options, build_options, family_id, family_params,
                                     mark_gifts, offer_options, refresh_family_ids, split_added_product, split_explicit_gift,
                                     title_color)
from repositories.schema_v2 import init_schema_v2
from repositories.search_repo import SearchRepository


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
        self.assertEqual(family_params("Смартфоны", "Galaxy Z Fold8 Ultra 16/1TB"), {"storage": "1024", "ram": "16"})

    def test_different_tier_is_not_a_variant(self):
        siblings = [
            {"id": "a", "title": "Redmi Note 12 128 GB", "min_price": 100},
            {"id": "b", "title": "Redmi Note 12 256 GB", "min_price": 150},
            {"id": "c", "title": "Redmi Note 12 Pro+ 5G 512 GB", "min_price": 300},
        ]
        options = build_family_options("a", "Смартфоны", "Redmi Note 12 128 GB", siblings)
        self.assertEqual([v["value"] for v in options[0]["values"]], ["128", "256"])

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

    def test_family_id_separates_tiers_and_skips_other_categories(self):
        def product(title, category="Смартфоны", kind="device"):
            return SimpleNamespace(title=title, brand="Xiaomi", model="Redmi Note 12", category=category, item_kind=kind)
        self.assertEqual(family_id(product("Redmi Note 12 128Gb")), family_id(product("Redmi Note 12 256Gb")))
        self.assertNotEqual(family_id(product("Redmi Note 12 128Gb")), family_id(product("Redmi Note 12 Pro 128Gb")))
        self.assertIsNone(family_id(product("X", category="Ноутбуки")))
        self.assertIsNone(family_id(product("X", kind="accessory")))


class GiftTest(unittest.TestCase):
    def test_explicit_gift(self):
        for title, main, gift in (
            ("Смартфон Redmi 15 8/256Gb черный + подарок", "Смартфон Redmi 15 8/256Gb черный", ""),
            ("Смартфон Apple iPhone 14 256GB Blue + акция 4 подарка", "Смартфон Apple iPhone 14 256GB Blue", ""),
            ("Смартфон ZTE Blade V50 6/128Gb + Подарок Смартфон ZTE A51", "Смартфон ZTE Blade V50 6/128Gb", "Смартфон ZTE A51"),
            ("Часы X + чехол в подарок", "Часы X", "чехол"),
        ):
            with self.subTest(title=title):
                self.assertEqual(split_explicit_gift(title), (main, gift))

    def test_not_a_gift(self):
        for title in (
            "Dsquared2 WOOD Femme Подарочный набор",
            "Смарт-часы Apple Watch Ultra 2 GPS + Cellular 49 мм",
            "AVA ACTN-07TG белый + монтажный комплект",
            "Комплект Samsung Galaxy Buds Core + Samsung Galaxy S26 FE",
            "Подарок для мамы",
        ):
            with self.subTest(title=title):
                self.assertIsNone(split_added_product(title)[1])

    def test_added_product(self):
        self.assertEqual(split_added_product("Смартфон Oppo A6x 4/64Gb + Рюкзак RenoChoice Синий"),
                         ("Смартфон Oppo A6x 4/64Gb", "Рюкзак RenoChoice Синий"))

    def test_gift_color_and_memory_are_ignored(self):
        self.assertEqual(title_color("Смартфон X 8/256Gb Blue + Наушники Белый"), "blue")
        self.assertEqual(family_params("Смартфоны", "Смартфон X 8/256Gb + Смартфон Y 4/64Gb"), {"storage": "256", "ram": "8"})

    def test_mark_gifts(self):
        def offer(title):
            return SimpleNamespace(title=title, gift=None)
        plain, gifted, bundle = offer("X 128Gb"), offer("X 128Gb + подарок"), offer("X 128Gb + Наушники Y")
        self.assertTrue(mark_gifts([plain, gifted, bundle]))
        self.assertEqual((gifted.title, gifted.gift), ("X 128Gb", ""))
        self.assertEqual((bundle.title, bundle.gift), ("X 128Gb", "Наушники Y"))
        self.assertIsNone(plain.gift)
        lone = offer("X 128Gb + Наушники Y")
        self.assertFalse(mark_gifts([lone]))
        self.assertEqual(lone.title, "X 128Gb + Наушники Y")


class SearchFamilyCollapseTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.addCleanup(self.conn.close)
        self.conn.execute("INSERT INTO sellers (id, name, slug, is_active, created_at, updated_at) VALUES ('s', 'S', 's', 1, 'x', 'x')")
        self.conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, is_active, created_at) VALUES ('c', 's', 'website', 'W', 1, 'x')")
        for pid, title, model, price in (
            ("a", "Xiaomi Redmi 10 128Gb", "Redmi 10", 200.0),
            ("b", "Xiaomi Redmi 10 256Gb", "Redmi 10", 300.0),
            ("c", "Xiaomi Redmi 11 128Gb", "Redmi 11", 250.0),
        ):
            self.conn.execute(
                "INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, created_at, updated_at) "
                "VALUES (?, ?, ?, 'Смартфоны', 'Xiaomi', ?, 'x', 'x')", (pid, pid, title, model))
            self.conn.execute(
                "INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, currency, condition, availability, city, "
                "is_active, observed_at, created_at, updated_at) VALUES (?, ?, 's', 'c', ?, 'http://x', ?, 'KZT', 'NEW', 'in_stock', "
                "'Казахстан', 1, '2026-11-01T00:00:00+00:00', 'x', 'x')", ("off_" + pid, pid, pid, price))
        self.conn.commit()
        self.assertEqual(refresh_family_ids(self.conn), 3)
        self.repo = SearchRepository(self.conn)

    def test_search_returns_cheapest_variant_with_count(self):
        now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        items = self.repo.search_products("Redmi", now=now)
        found = {i["product"].id: i["variants_count"] for i in items}
        self.assertEqual(found, {"a": 1, "c": 0})
        self.assertEqual(self.repo.count_search_products("Redmi")["total_products"], 2)

    def test_query_for_one_variant_shows_it(self):
        now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        items = self.repo.search_products("Redmi 256Gb", now=now)
        self.assertEqual([i["product"].id for i in items], ["b"])

    def test_refresh_is_idempotent(self):
        self.assertEqual(refresh_family_ids(self.conn), 0)


if __name__ == "__main__":
    unittest.main()
