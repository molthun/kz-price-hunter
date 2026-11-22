from tests import test_support  # noqa: F401
import json
import unittest
from types import SimpleNamespace

from scrapers import http as requests
from scrapers.ispace import ISpaceScraper


def _listing_html(count: int) -> str:
    cards = "".join(
        f'<div class="entity-card"><a class="entity-card_name" href="/product/item-{i}">Item {i}</a></div>'
        for i in range(count)
    )
    return f"<html><body>{cards}</body></html>"


def _product_html(idx: int) -> str:
    payload = {
        "@type": "Product",
        "name": f"Apple iPhone Test {idx}",
        "sku": f"MT{idx:03d}HX/A",
        "image": [f"https://ispace.kz/img/{idx}.jpg"],
        "offers": {
            "price": str(450000 + idx * 1000),
            "availability": "https://schema.org/InStock",
        },
    }
    return f'<html><head><script type="application/ld+json">{json.dumps(payload)}</script></head></html>'


class ISpaceRateLimitTest(unittest.TestCase):
    def test_polite_concurrency_and_card_delay_defaults(self):
        self.assertLessEqual(ISpaceScraper.PRODUCT_WORKERS, 2)
        self.assertGreaterEqual(ISpaceScraper.CARD_DELAY_SECONDS, 0.5)

    def test_http_429_stops_remaining_cards_immediately(self):
        scraper = ISpaceScraper()
        scraper.CARD_DELAY_SECONDS = 0.0
        scraper.PRODUCT_WORKERS = 1
        calls = []

        def fake_get(url):
            calls.append(url)
            if "catalog" in url:
                return SimpleNamespace(status_code=200, text=_listing_html(10))
            if len(calls) == 3:  # 2-я карточка -> HTTP 429
                return SimpleNamespace(status_code=429, text="Too Many Requests")
            return SimpleNamespace(status_code=200, text=_product_html(len(calls)))

        scraper._get = fake_get
        res = scraper._scrape_sync("iSpace: iPhone", "https://ispace.kz/catalog/iphone", max_pages=1)
        self.assertFalse(res.complete)
        self.assertIn("HTTP 429", res.error or "")
        # 1 листинг + 1 успешная карточка + 1 карточка с 429 = ровно 3 запроса, остальные 8 отменены
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(res), 1)

    def test_host_cooldown_error_stops_remaining_cards(self):
        scraper = ISpaceScraper()
        scraper.CARD_DELAY_SECONDS = 0.0
        scraper.PRODUCT_WORKERS = 1
        calls = []

        def fake_get(url):
            calls.append(url)
            if "catalog" in url:
                return SimpleNamespace(status_code=200, text=_listing_html(8))
            raise requests.HostCooldown("ispace.kz", 60.0)

        scraper._get = fake_get
        res = scraper._scrape_sync("iSpace: iPhone", "https://ispace.kz/catalog/iphone", max_pages=1)
        self.assertFalse(res.complete)
        self.assertIn("HTTP 429", res.error or "")
        self.assertEqual(len(calls), 2)

    def test_clean_run_returns_complete_without_errors(self):
        scraper = ISpaceScraper()
        scraper.CARD_DELAY_SECONDS = 0.0

        def fake_get(url):
            if "catalog" in url:
                return SimpleNamespace(status_code=200, text=_listing_html(3))
            idx = int(url.rsplit("-", 1)[-1])
            return SimpleNamespace(status_code=200, text=_product_html(idx))

        scraper._get = fake_get
        res = scraper._scrape_sync("iSpace: iPhone", "https://ispace.kz/catalog/iphone", max_pages=1)
        self.assertTrue(res.complete)
        self.assertIsNone(res.error)
        self.assertEqual(len(res), 3)


if __name__ == "__main__":
    unittest.main()
