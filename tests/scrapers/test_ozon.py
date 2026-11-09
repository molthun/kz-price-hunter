"""Tests for Ozon KZ scraper and showcase scanner."""
from tests import test_support  # noqa: F401  isolates DATA_DIR
import json
import unittest
from unittest.mock import MagicMock, patch

from scrapers.ozon import OzonScraper
from scrapers.showcase_scanners import (
    OzonShowcaseScanner,
    extract_marketplace_merchant_info,
)


class OzonScraperTest(unittest.TestCase):
    def test_extract_search_path(self):
        url = "https://ozon.kz/category/smartfony-15502/?text=iphone"
        path = OzonScraper.extract_search_path(url, 2)
        self.assertIn("category/smartfony-15502/", path)
        self.assertIn("page=2", path)
        self.assertIn("text=iphone", path)

    def test_parse_payload_products_and_sellers(self):
        # Ozon returns widgetStates mapping
        mock_items = [
            {
                "id": "164829104",
                "sku": "164829104",
                "title": "Ноутбук ASUS Vivobook 15 OLED",
                "cellTrackingInfo": {
                    "title": "Ноутбук ASUS Vivobook 15 OLED",
                    "price": 389990,
                    "originalPrice": 429990,
                    "sellerId": 88123,
                    "sellerName": "Asus Kazakhstan",
                },
                "action": {
                    "link": "/product/noutbuk-asus-vivobook-15-oled-164829104/"
                },
                "tileImage": {
                    "image": {
                        "link": "https://ir.ozon.kz/s3/multimedia-1/wc200/601234567.jpg"
                    }
                }
            },
            {
                "id": "189283741",
                "sku": "189283741",
                "title": "Беспроводная мышь Logitech M185",
                "cellTrackingInfo": {
                    "title": "Беспроводная мышь Logitech M185",
                    "price": 6490,
                    "sellerId": 77112,
                    "sellerName": "GadgetStore",
                },
                "action": {
                    "link": "/product/mysh-logitech-189283741/"
                },
                "tileImage": {
                    "image": {
                        "link": "https://ir.ozon.kz/s3/multimedia-2/wc200/609876543.jpg"
                    }
                }
            }
        ]

        payload = {
            "widgetStates": {
                "searchResultsV2-284910-default-1": json.dumps({"items": mock_items}),
                "header-1": "{}",
            }
        }

        result = OzonScraper.parse_payload(payload, "Ноутбуки", "https://ozon.kz", 1)
        self.assertEqual(len(result), 2)

        it1 = result[0]
        self.assertEqual(it1["id"], "ozon_164829104")
        self.assertEqual(it1["shop"], "Ozon KZ")
        self.assertEqual(it1["title"], "Ноутбук ASUS Vivobook 15 OLED")
        self.assertEqual(it1["price"], 389990.0)
        self.assertEqual(it1["old_price_on_site"], 429990.0)
        self.assertEqual(it1["seller"], "Asus Kazakhstan")
        self.assertEqual(it1["seller_id"], "ozon:88123")
        self.assertEqual(it1["marketplace"], "ozon")
        self.assertEqual(it1["external_sku"], "164829104")
        self.assertTrue(it1["url"].startswith("https://ozon.kz/product/"))
        # Image should be upgraded from wc200 to c1000
        self.assertIn("c1000", it1["image_url"])

        it2 = result[1]
        self.assertEqual(it2["id"], "ozon_189283741")
        self.assertEqual(it2["price"], 6490.0)
        self.assertEqual(it2["seller"], "GadgetStore")
        self.assertEqual(it2["seller_id"], "ozon:77112")

    def test_parse_payload_empty_last_page(self):
        # Конец каталога подтверждает только товарный виджет с пустым списком (R02)
        empty_payload = {"widgetStates": {"searchResultsV2-1": json.dumps({"items": []})}}
        res = OzonScraper.parse_payload(empty_payload, "Ноутбуки", "https://ozon.kz", 3)
        self.assertEqual(len(res), 0)
        self.assertTrue(res.complete)

    def test_parse_payload_without_product_widget_is_not_end(self):
        from scrapers.base import UnconfirmedEnd
        with self.assertRaises(UnconfirmedEnd):
            OzonScraper.parse_payload({"widgetStates": {}}, "Ноутбуки", "https://ozon.kz", 3)

    def test_parse_payload_modern_tilegrid_mainstate(self):
        """Проверка парсинга современного формата Ozon (tileGridDesktop с mainState)."""
        modern_item = {
            "id": "6155650909",
            "sku": 6155650909,
            "action": {
                "link": "/product/smartfon-i18-pro-max-32-2-tb-6155650909/"
            },
            "mainState": [
                {
                    "type": "priceV2",
                    "priceV2": {
                        "price": [
                            {"text": "4 810 ₸ × 12 мес", "textStyle": "PRICE"},
                            {"text": "57 718 ₸", "textStyle": "PRICE"},
                            {"text": "119 747 ₸", "textStyle": "ORIGINAL_PRICE"},
                        ]
                    }
                },
                {
                    "type": "textDS",
                    "id": "name",
                    "textDS": {
                        "text": "Смартфон i18 Pro Max 32/2 ТБ",
                        "testInfo": {"automatizationId": "tile-name"}
                    }
                }
            ],
            "tileImage": {
                "items": [
                    {
                        "type": "image",
                        "image": {"link": "https://ir.ozon.kz/s3/multimedia-1/wc200/sample.jpg"}
                    }
                ]
            }
        }
        payload = {
            "widgetStates": {
                "tileGridDesktop-3669724-default-1": json.dumps({"items": [modern_item]})
            }
        }
        res = OzonScraper.parse_payload(payload, "Смартфоны", "https://ozon.kz/category/smartfony-15502/", 1)
        self.assertEqual(len(res), 1)
        it = res[0]
        self.assertEqual(it["id"], "ozon_6155650909")
        self.assertEqual(it["title"], "Смартфон i18 Pro Max 32/2 ТБ")
        self.assertEqual(it["price"], 57718.0)
        self.assertEqual(it["old_price_on_site"], 119747.0)
        self.assertEqual(it["external_sku"], "6155650909")
        self.assertIn("c1000", it["image_url"])


class OzonShowcaseScannerTest(unittest.TestCase):
    def test_extract_merchant_info(self):
        platform, m_id = extract_marketplace_merchant_info("https://ozon.kz/seller/alser-computers-10492/")
        self.assertEqual(platform, "ozon")
        self.assertEqual(m_id, "alser-computers-10492")

        platform, m_id = extract_marketplace_merchant_info("https://www.ozon.ru/seller/shop-test/")
        self.assertEqual(platform, "ozon")
        self.assertEqual(m_id, "shop-test")

        platform, m_id = extract_marketplace_merchant_info("ozon:alser-10492")
        self.assertEqual(platform, "ozon")
        self.assertEqual(m_id, "alser-10492")

    @patch("scrapers.showcase_scanners.requests.get")
    def test_fetch_merchant_page(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_items = [
            {
                "sku": "55443322",
                "cellTrackingInfo": {
                    "title": "Монитор Dell 27 4K",
                    "price": 215000,
                },
                "action": {"link": "/product/monitor-dell-55443322/"},
                "tileImage": {"image": {"link": "https://ir.ozon.kz/s3/multimedia-1/wc200/dell.jpg"}},
            }
        ]
        mock_resp.json.return_value = {
            "widgetStates": {
                "searchResults-1": json.dumps({"items": mock_items})
            }
        }
        mock_get.return_value = mock_resp

        scanner = OzonShowcaseScanner(city_name="Астана")
        offers, is_last = scanner.fetch_merchant_page("dell-official", page=1)

        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.marketplace, "ozon")
        self.assertEqual(offer.merchant_id, "dell-official")
        self.assertEqual(offer.external_sku, "55443322")
        self.assertEqual(offer.title, "Монитор Dell 27 4K")
        self.assertEqual(offer.price, 215000.0)
        self.assertEqual(offer.city, "Астана")

    @patch("scrapers.showcase_scanners.requests.get")
    def test_showcase_scanner_modern_tilegrid_mainstate(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_item = {
            "id": "77889900",
            "sku": "77889900",
            "action": {"link": "/product/tovar-77889900/"},
            "mainState": [
                {
                    "type": "priceV2",
                    "priceV2": {
                        "price": [
                            {"text": "14 990 ₸", "textStyle": "PRICE"},
                            {"text": "19 990 ₸", "textStyle": "ORIGINAL_PRICE"},
                        ]
                    }
                },
                {
                    "type": "textDS",
                    "id": "name",
                    "textDS": {"text": "Тестовый товар Ozon"}
                }
            ],
            "tileImage": {
                "items": [
                    {"image": {"link": "https://ir.ozon.kz/s3/multimedia-1/wc200/prod.jpg"}}
                ]
            }
        }
        mock_resp.json.return_value = {
            "widgetStates": {
                "tileGridDesktop-14036369-default-1": json.dumps({"items": [mock_item]})
            }
        }
        mock_get.return_value = mock_resp

        scanner = OzonShowcaseScanner(city_name="Алматы")
        offers, is_last = scanner.fetch_merchant_page("5490173", page=1)

        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.marketplace, "ozon")
        self.assertEqual(offer.merchant_id, "5490173")
        self.assertEqual(offer.external_sku, "77889900")
        self.assertEqual(offer.title, "Тестовый товар Ozon")
        self.assertEqual(offer.price, 14990.0)
        self.assertEqual(offer.old_price, 19990.0)
        self.assertIn("c1000", offer.image_url)
        self.assertTrue(offer.url.startswith("https://ozon.kz/product/"))
        self.assertTrue(is_last)  # < 12 items
