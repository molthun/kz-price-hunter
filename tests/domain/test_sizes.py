from tests import test_support  # noqa: F401
import json
import pathlib
import unittest
from types import SimpleNamespace

import database
import local_search
from domain.sizes import (
    extract_size_from_query,
    normalize_available_sizes,
    normalize_size_token,
    sort_size_key,
)
from scrapers.intertop import IntertopScraper
from scrapers.kari import KariScraper


class SizesNormalizationTest(unittest.TestCase):
    def test_normalize_tokens_and_sorting(self):
        self.assertEqual(normalize_size_token("EU 42"), "42")
        self.assertEqual(normalize_size_token("37,5"), "37.5")
        self.assertEqual(normalize_size_token("38.0"), "38")
        self.assertEqual(normalize_size_token("2xl"), "XXL")
        self.assertIsNone(normalize_size_token("256GB"))
        self.assertIsNone(normalize_size_token("99"))

        sizes = normalize_available_sizes([{"size": "41"}, {"size": "36"}, {"size": "37,5"}, {"size": "36"}, "XL", "S"])
        self.assertEqual(sizes, ["36", "37.5", "41", "S", "XL"])
        self.assertLess(sort_size_key("36"), sort_size_key("37.5"))
        self.assertLess(sort_size_key("42"), sort_size_key("S"))

    def test_extract_size_from_query_respects_context(self):
        self.assertEqual(extract_size_from_query("кроссовки мужские 42 размер"), ("42", "кроссовки мужские"))
        self.assertEqual(extract_size_from_query("сапоги женские 38.5"), ("38.5", "сапоги женские"))
        self.assertEqual(extract_size_from_query("куртка зимняя 2XL"), ("XXL", "куртка зимняя"))
        self.assertEqual(extract_size_from_query("размер 39 туфли"), ("39", "туфли"))
        # Техника не должна ошибочно считаться размером обуви
        self.assertEqual(extract_size_from_query("телевизор LG 42 дюйма"), (None, "телевизор LG 42 дюйма"))
        self.assertEqual(extract_size_from_query("Apple Watch 44 мм"), (None, "Apple Watch 44 мм"))
        self.assertEqual(extract_size_from_query("SSD 256 ГБ"), (None, "SSD 256 ГБ"))


class ShoeScrapersSizesTest(unittest.TestCase):
    def test_kari_extracts_available_sizes_from_next_data(self):
        scraper = KariScraper()
        ent = {
            "articul": "W123456",
            "title": "Кроссовки женские",
            "brand": {"name": "T.Taccardi"},
            "price": {"current": 19990, "first": 24990},
            "preview": "https://kz.kari.com/img/w123.jpg",
            "sizes": [{"size": "38"}, {"size": "36"}, {"size": "37"}],
        }
        item = scraper._item(ent, "Kari: 👠 Женская обувь")
        self.assertIsNotNone(item)
        self.assertEqual(item["available_sizes"], ["36", "37", "38"])

    def test_intertop_extracts_available_sizes_from_tile_options(self):
        html = """
        <html><body>
          <div class="in-product-tile" data-product-id="8514154" data-product-sku="115-9100">
            <a href="/ru-kz/product/8514154">link</a>
            <div class="in-product-tile__product-brand">Tamaris</div>
            <div class="in-product-tile__product-name">Ботинки зимние</div>
            <div class="in-price__actual">34 990 ₸</div>
            <img src="https://intertop.kz/img/1.jpg" />
            <div class="in-product-tile__options">
              <span>39</span><span>37</span><span>40</span>
            </div>
          </div>
        </body></html>
        """
        scraper = IntertopScraper()
        scraper.session = SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(status_code=200, text=html))
        res = scraper._fetch_page("Интертоп: Женская обувь", "https://intertop.kz/ru-kz/catalog/zhenskaya_obuv/", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["available_sizes"], ["37", "39", "40"])


class SizesSearchIntegrationTest(unittest.TestCase):
    def setUp(self):
        database.init_db()

    def test_sync_index_facet_search_and_sold_out_update(self):
        pair_small = {
            "id": "kari_size_test_small",
            "sku": "W100",
            "shop": "Kari",
            "title": "Кроссовки T.Taccardi Run Alpha Белые",
            "category": "Kari: 👠 Женская обувь",
            "url": "https://kz.kari.com/product/W100/",
            "image_url": "https://kz.kari.com/img/w100.jpg",
            "price": 18990,
            "old_price_on_site": 22990,
            "city": "Алматы / Казахстан",
            "available_sizes": ["36", "37", "38"],
        }
        pair_large = {
            "id": "kari_size_test_large",
            "sku": "M200",
            "shop": "Kari",
            "title": "Кроссовки Pierre Cardin Run Beta Черные",
            "category": "Kari: 👞 Мужская обувь",
            "url": "https://kz.kari.com/product/M200/",
            "image_url": "https://kz.kari.com/img/m200.jpg",
            "price": 25990,
            "old_price_on_site": 0,
            "city": "Алматы / Казахстан",
            "available_sizes": ["41", "42", "43"],
        }
        database.save_or_update_products_batch([pair_small, pair_large])

        # 1. Поиск «кроссовки 42 размер» находит только вторую пару и отдаёт фасет «Размер»
        res42 = local_search.search_sync(
            {"query": "кроссовки 42 размер", "limit": 20, "offset": 0},
            {"plan": None},
            {},
        )
        titles42 = [it["product"]["title"] for it in res42["items"]]
        self.assertTrue(any("Run Beta" in t for t in titles42), titles42)
        self.assertFalse(any("Run Alpha" in t for t in titles42), titles42)

        # Доступные размеры пробрасываются в AggregatedOffer
        beta_item = next(it for it in res42["items"] if "Run Beta" in it["product"]["title"])
        self.assertEqual(beta_item["offers"][0]["available_sizes"], ["41", "42", "43"])

        # Фасет «Размер» присутствует и отсортирован по возрастанию
        size_facet = next((f for f in res42["attribute_facets"] if f["key"] == "size"), None)
        self.assertIsNotNone(size_facet)
        self.assertEqual(size_facet["label"], "Размер")
        facet_vals = [v["value"] for v in size_facet["values"]]
        self.assertIn("42", facet_vals)
        self.assertIn("36", facet_vals)
        self.assertEqual(facet_vals, sorted(facet_vals, key=sort_size_key))

        # 2. Обновление: 42-й размер раскупили -> поиск по 42 больше не находит эту пару
        database.save_or_update_products_batch([{**pair_large, "available_sizes": ["41", "43"]}])
        res42_after = local_search.search_sync(
            {"query": "кроссовки 42 размер", "limit": 20, "offset": 0},
            {"plan": None},
            {},
        )
        titles_after = [it["product"]["title"] for it in res42_after["items"]]
        self.assertFalse(any("Run Beta" in t for t in titles_after), titles_after)

    def test_search_first_template_renders_available_sizes(self):
        html = pathlib.Path("web/templates/search_first.html").read_text(encoding="utf-8")
        self.assertIn("off.available_sizes", html)
        self.assertIn("offer-sizes-row", html)
        self.assertIn("offer-size-chip", html)


if __name__ == "__main__":
    unittest.main()
