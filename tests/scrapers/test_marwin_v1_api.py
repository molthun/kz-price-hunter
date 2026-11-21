"""Тесты MarwinScraper на публичном JSON REST API `/v1/catalog/*` (v6.42.10)."""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import Mock

import config
from scrapers.marwin import MarwinScraper, extract_category_slug
from tests.scrapers.test_city_confirmation_r1a import _retired_after


class MarwinV1ApiTest(unittest.TestCase):
    def test_extract_category_slug_handles_locales_and_trailing_slash(self):
        self.assertEqual(extract_category_slug("https://www.marwin.kz/knigi-32627"), "knigi-32627")
        self.assertEqual(extract_category_slug("https://www.marwin.kz/en/knigi-32627/"), "knigi-32627")
        self.assertEqual(extract_category_slug("https://www.marwin.kz/ru/videoigry-i-konsoli-32532"), "videoigry-i-konsoli-32532")

    def test_out_of_stock_skipped_without_unparsed_and_allows_complete(self):
        s = MarwinScraper()
        s._slug_to_id["knigi-32627"] = 33
        resp = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [
                    {
                        "id": 101,
                        "sku": "101",
                        "name": "Книга в наличии",
                        "slug": "kniga-101",
                        "image": "https://media.marwin.kz/101.jpg",
                        "price": {"current": 4990, "old": 5990},
                        "in_stock": True,
                    },
                    {
                        "id": 102,
                        "sku": "102",
                        "name": "Нет в наличии",
                        "slug": "kniga-102",
                        "image": "https://media.marwin.kz/102.jpg",
                        "price": {"current": 3990, "old": None},
                        "in_stock": False,
                    },
                ],
                "pagination": {"total": 2, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp))

        res = s._fetch_page("Книги", "https://www.marwin.kz/knigi-32627", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "marwin_101")
        self.assertEqual(res[0]["old_price_on_site"], 5990)
        self.assertEqual(res.unparsed, 0)
        self.assertTrue(res.complete)

    def test_broken_item_increments_unparsed_and_blocks_retirement(self):
        src_url = "https://www.marwin.kz/videoigry-i-konsoli-32532"
        existing = ("marwin_9001@unknown", "marwin_9002@unknown")

        s = MarwinScraper()
        s._slug_to_id["videoigry-i-konsoli-32532"] = 28
        resp = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [
                    {
                        "id": 9001,
                        "sku": "9001",
                        "name": "PlayStation 5 Slim",
                        "slug": "ps5-9001",
                        "image": "https://media.marwin.kz/ps5.jpg",
                        "price": {"current": 399990, "old": None},
                        "in_stock": True,
                    },
                    {
                        "id": 9999,
                        "name": "Сломанная карточка без цены",
                        "slug": "broken-9999",
                        "price": {"current": 0},
                        "in_stock": True,
                    },
                ],
                "pagination": {"total": 2, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp))

        res = s._scrape_sync("Меломан: 🎮 Видеоигры и консоли", src_url, max_pages=5)
        self.assertEqual(len(res), 1)
        self.assertEqual(res.unparsed, 1)
        self.assertFalse(res.complete)
        retired, active, assessment = _retired_after(res, existing, "marwin", src_url)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertEqual(active, set(existing))

        # Положительный контроль: чистый полный обход (unparsed=0) снимает отсутствующий marwin_9002@unknown
        resp_clean = Mock(
            status_code=200,
            json=Mock(return_value={
                "items": [
                    {
                        "id": 9001,
                        "sku": "9001",
                        "name": "PlayStation 5 Slim",
                        "slug": "ps5-9001",
                        "image": "https://media.marwin.kz/ps5.jpg",
                        "price": {"current": 399990, "old": None},
                        "in_stock": True,
                    },
                ],
                "pagination": {"total": 1, "page": 1, "pages": 1, "limit": 20},
            }),
        )
        s.session = Mock(get=Mock(return_value=resp_clean))
        res_clean = s._scrape_sync("Меломан: 🎮 Видеоигры и консоли", src_url, max_pages=5)
        self.assertTrue(res_clean.complete)
        self.assertEqual(res_clean.unparsed, 0)
        retired_clean, active_clean, assessment_clean = _retired_after(res_clean, existing, "marwin", src_url)
        self.assertTrue(assessment_clean["may_retire"])
        self.assertEqual(retired_clean, ["marwin_9002@unknown"])
        self.assertEqual(active_clean, {"marwin_9001@unknown"})

    def test_legacy_url_resolves_via_redirect_fallback(self):
        s = MarwinScraper()
        r_404 = Mock(status_code=404)
        r_redir = Mock(status_code=200, url="https://www.marwin.kz/en/knigi-32627")
        r_cat = Mock(status_code=200, json=Mock(return_value={"id": 33, "slug": "knigi-32627"}))
        s.session = Mock(get=Mock(side_effect=[r_404, r_redir, r_cat]))

        cat_id = s._resolve_category_id("https://www.marwin.kz/books/")
        self.assertEqual(cat_id, 33)
        # Cached on second call
        self.assertEqual(s._resolve_category_id("https://www.marwin.kz/books/"), 33)
        self.assertEqual(s.session.get.call_count, 3)

    def test_marwin_categories_use_v1_slugs(self):
        self.assertEqual(len(config.MARWIN_CATEGORIES), 9)
        urls = [c["url"] for c in config.MARWIN_CATEGORIES]
        self.assertEqual(len(urls), len(set(urls)))
        for entry in config.MARWIN_CATEGORIES:
            slug = extract_category_slug(entry["url"])
            self.assertRegex(slug, r".+-\d+$", f"Expected numeric-suffix v1 slug in {entry}")


if __name__ == "__main__":
    unittest.main()
