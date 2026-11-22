from tests import test_support  # noqa: F401

import json
import unittest
from unittest.mock import MagicMock

from scrapers.detmir import DetmirScraper


def _make_appdata_html(items, total=None):
    payload = {
        "catalog": {
            "data": {
                "items": items,
                "meta": {"productsLength": len(items) if total is None else total},
            }
        }
    }
    encoded = json.dumps(json.dumps(payload, ensure_ascii=False), ensure_ascii=False)
    return f"<html><body><script>window.appData = JSON.parse({encoded});</script></body></html>"


class DetmirScraperSizesTest(unittest.TestCase):
    def test_extract_item_sizes_filters_out_of_stock_and_non_size_variants(self):
        raw_item = {
            "id": "7155205",
            "title": "Ботинки Lassie",
            "variants": [
                {"size": "20", "type": "size", "isOutOfStock": True, "availability": "NONE", "isAvailableOnline": False, "isAvailableOffline": False},
                {"size": "24", "type": "size", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": False, "isAvailableOffline": True},
                {"size": "26", "type": "size", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": True, "isAvailableOffline": True},
                {"size": "25", "type": "size", "isOutOfStock": False, "availability": "NONE", "isAvailableOnline": True, "isAvailableOffline": False},
                {"size": "27", "type": "size", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": False, "isAvailableOffline": False},
                {"size": "Красный", "type": "color", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": True, "isAvailableOffline": True},
            ],
        }
        self.assertEqual(DetmirScraper._extract_item_sizes(raw_item), ["24", "26"])

    def test_fetch_page_and_search_live_attach_sizes_from_appdata(self):
        html = _make_appdata_html(
            [
                {
                    "id": "7155205",
                    "title": "Ботинки Lassie",
                    "price": {"price": 52699},
                    "old_price": {"price": 62000},
                    "link": {"web_url": "/product/index/id/7155205/"},
                    "pictures": [{"original": "https://cdn.detmir.kz/img1.jpg"}],
                    "variants": [
                        {"size": "28", "type": "size", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": True, "isAvailableOffline": True},
                        {"size": "24", "type": "size", "isOutOfStock": False, "availability": "AVAILABLE", "isAvailableOnline": False, "isAvailableOffline": True},
                        {"size": "22", "type": "size", "isOutOfStock": True, "availability": "NONE", "isAvailableOnline": False, "isAvailableOffline": False},
                    ],
                },
                {
                    "id": "8000001",
                    "title": "Конструктор LEGO Classic",
                    "price": {"price": 15990},
                    "link": {"web_url": "/product/index/id/8000001/"},
                    "pictures": [{"original": "https://cdn.detmir.kz/img2.jpg"}],
                    "variants": [],
                },
            ]
        )
        scraper = DetmirScraper()
        fake_session = MagicMock()
        fake_session.get.return_value = MagicMock(status_code=200, text=html)
        scraper.session = fake_session

        res = scraper._fetch_page("Детский мир: Одежда и обувь", "https://detmir.kz/catalog/index/name/children_clothes/", 1)
        self.assertTrue(res.complete)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["id"], "detmir_7155205")
        self.assertEqual(res[0]["available_sizes"], ["24", "28"])
        self.assertNotIn("available_sizes", res[1])

        live = scraper.search_live("Lassie", limit=5)
        self.assertEqual(len(live), 2)
        self.assertEqual(live[0]["available_sizes"], ["24", "28"])


if __name__ == "__main__":
    unittest.main()
