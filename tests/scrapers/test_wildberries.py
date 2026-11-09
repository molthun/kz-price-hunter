"""Tests for Wildberries KZ scraper and showcase scanner."""
from tests import test_support  # noqa: F401  isolates DATA_DIR
import json
import unittest
from unittest.mock import MagicMock, patch

from domain.models import ChannelType
from scrapers.showcase_scanners import (
    WildberriesShowcaseScanner,
    extract_marketplace_merchant_info,
    wb_image_url,
)
from scrapers.wildberries import WildberriesScraper


class WildberriesScraperTest(unittest.TestCase):
    def test_wb_image_url_calculations(self):
        # Basket bucket ranges
        # Case 1: id < 14400000 -> basket-01
        self.assertIn("basket-01.wbbasket.ru", wb_image_url(500000))
        # Case 2: 12345678 -> vol123 / part12345 / 12345678
        url = wb_image_url(12345678)
        self.assertTrue(url.startswith("https://basket-"))
        self.assertIn("/vol123/part12345/12345678/images/big/1.webp", url)
        # Invalid input
        self.assertEqual(wb_image_url(0), "")
        self.assertEqual(wb_image_url("invalid"), "")

    def test_extract_query(self):
        # Query parameter in URL
        url_search = "https://www.wildberries.kz/catalog/0/search.aspx?search=samsung%20s24"
        self.assertEqual(WildberriesScraper.extract_query(url_search), "samsung s24")

        # Query param 'query'
        url_query = "https://www.wildberries.kz/catalog?query=iphone"
        self.assertEqual(WildberriesScraper.extract_query(url_query), "iphone")

        # Category slug from path
        url_cat = "https://www.wildberries.kz/catalog/elektronika/smartfony-i-telefony"
        self.assertEqual(WildberriesScraper.extract_query(url_cat), "smartfony i telefony")

        # Root URL returns empty string
        self.assertEqual(WildberriesScraper.extract_query("https://www.wildberries.kz/"), "")

    def test_parse_payload_products_and_sellers(self):
        payload = {
            "state": 0,
            "data": {
                "products": [
                    {
                        "id": 189283741,
                        "name": "Смартфон Galaxy S24 Ultra 256GB",
                        "brand": "Samsung",
                        "supplier": "Samsung Official Store KZ",
                        "supplierId": 456789,
                        "reviewRating": 4.9,
                        "feedbacks": 128,
                        "sizes": [
                            {
                                "name": "",
                                "price": {
                                    "basic": 62000000,  # 620,000 KZT (in tiyns)
                                    "total": 54999000,  # 549,990 KZT
                                }
                            }
                        ]
                    },
                    {
                        "id": 99887766,
                        "name": "Чехол силиконовый прозрачный",
                        "brand": "Hoco",
                        "supplier": "TechZone",
                        "supplierId": 112233,
                        "reviewRating": 4.5,
                        "feedbacks": 50,
                        "sizes": [
                            {
                                "name": "",
                                "price": {
                                    "basic": 300000,   # 3,000 KZT
                                    "total": 199000,   # 1,990 KZT
                                }
                            }
                        ]
                    }
                ]
            }
        }

        result = WildberriesScraper.parse_payload(payload, "Смартфоны", "https://wildberries.kz", 1)
        self.assertEqual(len(result), 2)

        p1 = result[0]
        self.assertEqual(p1["id"], "wb_189283741")
        self.assertEqual(p1["shop"], "Wildberries KZ")
        self.assertEqual(p1["title"], "Samsung Смартфон Galaxy S24 Ultra 256GB")
        self.assertEqual(p1["price"], 549990.0)
        self.assertEqual(p1["old_price_on_site"], 620000.0)
        self.assertEqual(p1["seller"], "Samsung Official Store KZ")
        self.assertEqual(p1["seller_id"], "wildberries:456789")
        self.assertEqual(p1["marketplace"], "wildberries")
        self.assertEqual(p1["external_sku"], "189283741")
        self.assertEqual(p1["rating"], 4.9)
        self.assertEqual(p1["reviews_count"], 128)
        self.assertTrue(p1["url"].startswith("https://www.wildberries.kz/catalog/189283741/detail.aspx"))
        self.assertTrue(p1["image_url"].endswith(".webp"))

        p2 = result[1]
        self.assertEqual(p2["id"], "wb_99887766")
        self.assertEqual(p2["title"], "Hoco Чехол силиконовый прозрачный")
        self.assertEqual(p2["price"], 1990.0)
        self.assertEqual(p2["seller_id"], "wildberries:112233")

    def test_parse_payload_empty_or_last_page(self):
        empty_payload = {"data": {"products": []}}
        res = WildberriesScraper.parse_payload(empty_payload, "Категория", "https://wildberries.kz", 5)
        self.assertEqual(len(res), 0)
        self.assertTrue(res.complete)


class WildberriesShowcaseScannerTest(unittest.TestCase):
    def test_extract_merchant_info(self):
        platform, m_id = extract_marketplace_merchant_info("https://www.wildberries.kz/seller/1234567")
        self.assertEqual(platform, "wildberries")
        self.assertEqual(m_id, "1234567")

        platform, m_id = extract_marketplace_merchant_info("https://www.wildberries.ru/seller/shop-name-456")
        self.assertEqual(platform, "wildberries")
        self.assertEqual(m_id, "shop-name-456")

        platform, m_id = extract_marketplace_merchant_info("wildberries:98765")
        self.assertEqual(platform, "wildberries")
        self.assertEqual(m_id, "98765")

        platform, m_id = extract_marketplace_merchant_info("wb:555")
        self.assertEqual(platform, "wildberries")
        self.assertEqual(m_id, "555")

    @patch("scrapers.showcase_scanners.requests.get")
    def test_fetch_merchant_page(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "products": [
                    {
                        "id": 12345,
                        "name": "Робот-пылесос Xiaomi S10",
                        "brand": "Xiaomi",
                        "supplier": "Mi Store Almaty",
                        "supplierId": 7788,
                        "sizes": [
                            {
                                "price": {
                                    "total": 11999000,
                                    "basic": 14999000,
                                }
                            }
                        ]
                    }
                ]
            }
        }
        mock_get.return_value = mock_resp

        scanner = WildberriesShowcaseScanner(city_name="Алматы")
        offers, is_last = scanner.fetch_merchant_page("7788", page=1)

        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.marketplace, "wildberries")
        self.assertEqual(offer.merchant_id, "7788")
        self.assertEqual(offer.external_sku, "12345")
        self.assertEqual(offer.price, 119990.0)
        self.assertEqual(offer.old_price, 149990.0)
        self.assertEqual(offer.city, "Алматы")
        self.assertTrue(is_last)  # < 20 products
