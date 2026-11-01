from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import unittest
from unittest.mock import MagicMock, patch

from scrapers.base import UnconfirmedEnd
from scrapers.kari import KariScraper

URL = "https://kz.kari.com/catalog/zhenschinam/zhenskaya-obuv/"


def page(ids, total_pages=2):
    entities = {
        "W1": {"articul": "W1", "title": "Ботинки женские KK1", "preview": "https://cdn/1.webp",
               "brand": {"name": "T.TACCARDI"},
               "price": {"first": 26999, "current": 22490, "max": 26999, "kari_p": 20990}},
        "W2": {"articul": "W2", "title": "Kari Туфли", "brand": {"name": "kari"},
               "price": {"first": 5000, "current": 5000, "kari_p": 4500}},
        "W3": {"articul": "W3", "title": "Без цены", "brand": {}, "price": {"current": 0}},
    }
    state = {"props": {"initialState": {"products": {
        "pagination": {"totalPages": total_pages, "totalCount": 100, "size": 60},
        "data": {"ids": ids, "entities": {i: entities[i] for i in ids}},
    }}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(state)}</script></html>'


class KariScraperTest(unittest.TestCase):
    def fetch(self, text, number=1, status=200):
        scraper = KariScraper()
        scraper.session = MagicMock()
        scraper.session.get.return_value = MagicMock(status_code=status, text=text)
        return scraper._fetch_page("Kari: Обувь", URL, number)

    def test_uses_price_for_any_buyer_not_loyalty_price(self):
        result = self.fetch(page(["W1", "W2", "W3"]))
        self.assertEqual([p["id"] for p in result], ["kari_W1", "kari_W2"])  # без цены не берём
        first = result[0]
        self.assertEqual(first["price"], 22490)
        self.assertEqual(first["old_price_on_site"], 26999)
        self.assertEqual(first["url"], "https://kz.kari.com/product/W1/")
        self.assertEqual(first["title"], "T.TACCARDI Ботинки женские KK1")
        self.assertEqual(first["category"], "Kari: Обувь")
        self.assertEqual(result[1]["old_price_on_site"], 0)
        self.assertEqual(result[1]["title"], "Kari Туфли")  # бренд не дублируется

    def test_end_of_catalog_needs_confirmation(self):
        self.assertFalse(self.fetch(page(["W1"]), 1).complete)
        self.assertTrue(self.fetch(page(["W1"]), 2).complete)
        with self.assertRaises(UnconfirmedEnd):
            self.fetch(page([]), 2)
        with self.assertRaises(UnconfirmedEnd):
            self.fetch("", 3, status=404)

    def test_empty_section_is_complete(self):
        result = self.fetch(page([], total_pages=0))
        self.assertEqual(list(result), [])
        self.assertTrue(result.complete)

    def test_broken_page_is_an_error_not_an_empty_catalog(self):
        with self.assertRaises(RuntimeError):
            self.fetch("<html>защита от роботов</html>")
        with self.assertRaises(RuntimeError):
            self.fetch("", status=403)

    def test_all_requests_to_kari_use_one_browser_profile(self):
        from scrapers import http
        self.assertEqual(http.impersonate_for_url("https://kz.kari.com/_next/a.js"), "chrome120")
        self.assertEqual(http.impersonate_for_url("https://kaspi.kz/shop/"), http.DEFAULT_IMPERSONATE)
        self.assertEqual(http.impersonate_for_url("https://evil.kz.kari.com.example/"), http.DEFAULT_IMPERSONATE)

    def test_registered_for_scanning(self):
        import config
        from web.server import SHOP_REGISTRY
        scraper_cls, categories, name = SHOP_REGISTRY["kari"]
        self.assertIs(scraper_cls, KariScraper)
        self.assertEqual(name, "Kari")
        self.assertEqual(config.SHOP_KEYS["kari"], "Kari")
        for category in categories:
            self.assertIn(category["master"], config.MASTER_CATEGORIES)
            self.assertLessEqual(category["max_pages"], 167)  # предел выдачи сайта: 10 000 товаров

    def test_every_section_has_a_catalog_category(self):
        from config import KARI_CATEGORIES
        from domain.feed_categories import section_category
        for category in KARI_CATEGORIES:
            with self.subTest(category=category["name"]):
                self.assertIsNotNone(section_category(category["name"]))


if __name__ == "__main__":
    unittest.main()
