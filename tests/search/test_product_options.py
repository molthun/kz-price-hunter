from tests import test_support  # must be first
import datetime
import sqlite3
import unittest
from types import SimpleNamespace

from domain.product_options import (build_family_options, build_options, family_id, family_model, family_params,
                                     same_variant_ids,
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
            ("Смартфон Samsung Galaxy S25 12/128Gb Blue Black", "black"),
            ("Смартфон Samsung Galaxy S25 Ultra 12/256GB Titanium Silver Blue", "blue"),
            ("Смартфон Tecno Pova 8 Pro 8 GB, 256 GB, Graphite Black (LK7)", "black"),
            ("Смартфон Xiaomi Redmi 15C 4/128GB Midnight Gray + подарок", "gray"),
            ("Apple Watch Series 10 42mm Silver Aluminum case + Sport Band M/L Black", "silver"),
            ("Black Shark GT3 Neo 51 мм серебристый-серый", "silver"),
            ("Смарт часы Garmin Fenix 8 47mm Gray/Orange", "gray+orange"),
            ("Смартфон Apple iPhone Air 256Gb Sky Blue", "lightblue"),
            ("Смартфон Apple iPhone 16 Pro 256Gb Black Titanium", "black"),
            ("Смартфон Apple iPhone 16 Pro 256Gb Natural Titanium", "titanium"),
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
        self.assertEqual([v["label"] for v in options[0]["values"]], ["Белый", "Красный", "Чёрный"])

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


class FamilyModelTest(unittest.TestCase):
    def test_color_words_leave_model(self):
        for brand, model, title, expected in (
            ("OPPO", "Reno 14F Luminous", "Смартфон Oppo Reno 14F 5G 8/256GB Luminous Green", "reno 14f"),
            ("Samsung", "Galaxy S26 sky", "Смартфон Samsung Galaxy S26 5G 256GB - sky", "galaxy s26"),
            ("Vivo", "Y35 золотистый", "Vivo Y35 золотистый", "y35"),
            ("Xiaomi", "Redmi Note 14 Ocean", "Смартфон Xiaomi Redmi Note 14 8/256Gb Ocean Blue", "redmi note 14"),
            ("Redmi", "Note 14", "Смартфон Redmi Note 14 8/256Gb", "redmi note 14"),
            ("Xiaomi", "Xiaomi 14T", "Xiaomi 14T 12/256Gb", "14t"),
        ):
            with self.subTest(title=title):
                self.assertEqual(family_model(brand, model, title), expected)

    def test_model_words_stay(self):
        self.assertEqual(family_model("Apple", "iPhone 14 Plus", "iPhone 14 Plus 128Gb Plus"), "iphone 14 plus")
        self.assertEqual(family_model("Tecno", "Spark 20", "Tecno Spark 20 128Gb 20"), "spark 20")
        self.assertEqual(family_model("Tecno", "Pop Ice", "Tecno Pop Ice"), "pop ice")

    def test_same_variant_ignores_color_and_missing_param(self):
        siblings = [
            {"id": "a", "title": "X 8/256Gb Blue"},
            {"id": "b", "title": "X 256Gb Black"},
            {"id": "c", "title": "X 12/256Gb Black"},
            {"id": "d", "title": "X 8/128Gb Blue"},
            {"id": "e", "title": "X Pro 8/256Gb Blue"},
        ]
        self.assertEqual(same_variant_ids("Смартфоны", "X 8/256Gb Blue", siblings), ["a", "b"])


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
            "Шкаф стекло 2 двери + 2 ящика Ivina",
            "Смарт-часы Apple Watch Series 10 42mm Titanium case + Milanese Loop Slate",
            "Подарок для мамы",
            "Корпус Wintek Aquarium [Mini Tower, 3 + контроллер и пульт, Черный]",
            "Печенье Roshen с желе + вишня 420 г",
            "Набор стираемых фломастеров + ручка-ластик 12 шт.",
            "ESCENTRIC MOLECULES / Molecule 01 + Mandarin Atomiser Набор",
        ):
            with self.subTest(title=title):
                self.assertIsNone(split_added_product(title)[1])

    def test_added_product(self):
        self.assertEqual(split_added_product("Планшет Xiaomi Pad 2 4G &#43; смарт-часы Redmi черный"),
                         ("Планшет Xiaomi Pad 2 4G", "смарт-часы Redmi черный"))
        self.assertEqual(split_added_product("Смартфон Oppo A6x 4/64Gb + Рюкзак RenoChoice Синий"),
                         ("Смартфон Oppo A6x 4/64Gb", "Рюкзак RenoChoice Синий"))
        self.assertEqual(split_added_product("Sony PlayStation 5 + Dualsense White + MK1"),
                         ("Sony PlayStation 5", "Dualsense White + MK1"))
        self.assertEqual(split_added_product("Комплект Стиральная машина Haier X + Сушильная машина Haier Y"),
                         ("Стиральная машина Haier X", "Сушильная машина Haier Y"))
        self.assertEqual(split_added_product("Комплект Samsung Galaxy Buds Core + Samsung Galaxy S26 FE 8/256Gb"),
                         ("Samsung Galaxy S26 FE 8/256Gb", "Samsung Galaxy Buds Core"))
        self.assertEqual(split_added_product("Apple Watch Ultra GPS + Cellular 49 мм + Чехол"),
                         ("Apple Watch Ultra GPS + Cellular 49 мм", "Чехол"))

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
        self.assertTrue(mark_gifts([lone]))
        self.assertEqual(lone.title, "X 128Gb")


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

    def test_in_stock_drops_stale_offers(self):
        now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        self.conn.execute("UPDATE offers SET observed_at = '2020-01-01T00:00:00+00:00', updated_at = '2020-01-01T00:00:00+00:00' WHERE id = 'off_c'")
        found = {i["product"].id for i in self.repo.search_products("Redmi", in_stock=True, now=now)}
        self.assertEqual(found, {"a"})

    def test_gift_filter_keeps_only_marked_offers(self):
        now = datetime.datetime(2026, 11, 1, tzinfo=datetime.timezone.utc)
        self.conn.execute("UPDATE offers SET has_gift = 1 WHERE id = 'off_c'")
        found = {i["product"].id for i in self.repo.search_products("Redmi", gift=True, now=now)}
        self.assertEqual(found, {"c"})

    def test_refresh_is_idempotent(self):
        self.assertEqual(refresh_family_ids(self.conn), 0)


if __name__ == "__main__":
    unittest.main()
