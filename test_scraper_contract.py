"""Tests for the unified Scraper contract, Scraper Protocol, and product validator."""
import inspect
import unittest
from scrapers.base import Scraper, PagedScraper, validate_product_item
from scrapers.kaspi import KaspiScraper
from scrapers.technodom import TechnodomScraper
from scrapers.fortemarket import ForteMarketScraper
from scrapers.fourmobile import FourMobileScraper
from scrapers.shopkz import ShopKzScraper
from scrapers.vkusmart import VkusmartScraper


class ScraperContractTest(unittest.TestCase):
    def test_scraper_classes_conform_to_contract(self):
        # Check standard scrapers
        for cls in (TechnodomScraper, ForteMarketScraper, FourMobileScraper, ShopKzScraper, KaspiScraper, VkusmartScraper):
            self.assertTrue(hasattr(cls, "SHOP_NAME"), f"{cls} must define SHOP_NAME")
            self.assertTrue(callable(getattr(cls, "scrape", None)), f"{cls} must define scrape")
            self.assertTrue(callable(getattr(cls, "close", None)), f"{cls} must define close")

    def test_kaspi_search_live_signature(self):
        self.assertTrue(hasattr(KaspiScraper, "search_live"))
        sig = inspect.signature(KaspiScraper.search_live)
        self.assertIn("query", sig.parameters)
        self.assertIn("max_items", sig.parameters)

    def test_validate_product_item_valid(self):
        valid = {
            "id": "item_123",
            "title": "Apple iPhone 15 128GB",
            "price": "399990",
            "url": "https://example.com/p/item_123",
        }
        res = validate_product_item(valid, default_shop="TestShop")
        self.assertIsNotNone(res)
        self.assertEqual(res["id"], "item_123")
        self.assertEqual(res["price"], 399990)
        self.assertEqual(res["shop"], "TestShop")

    def test_validate_product_item_invalid(self):
        bad_items = [
            {},
            {"id": "", "title": "Good", "price": 1000, "url": "https://a.com"},
            {"id": "1", "title": "", "price": 1000, "url": "https://a.com"},
            {"id": "1", "title": "Good", "price": 0, "url": "https://a.com"},
            {"id": "1", "title": "Good", "price": -50, "url": "https://a.com"},
            {"id": "1", "title": "Good", "price": "invalid", "url": "https://a.com"},
            {"id": "1", "title": "Good", "price": 1000, "url": "javascript:alert(1)"},
            {"id": "1", "title": "Good", "price": 1000, "url": "file:///etc/passwd"},
            "not a dict",
            None,
        ]
        for it in bad_items:
            self.assertIsNone(validate_product_item(it), f"Should reject {it}")

    def test_vkusmart_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = VkusmartScraper()
        mock_html = '''
        <div class="catalog-block-view__item" data-id="12345">
            <meta itemprop="name" content="Чай Greenfield Golden Ceylon 100 пакетиков" />
            <meta itemprop="image" content="https://vkusmart.vmv.kz/upload/tea.jpg" />
            <meta itemprop="description" content="Черный цейлонский чай" />
            <a href="/catalog/chay/12345/" class="thumb">Ссылка</a>
            <div class="cost prices">
                <div class="price" data-value="1850">
                    <span class="price_value">1 850</span>
                </div>
                <div class="price_old" data-value-old="2200">2 200 тг.</div>
            </div>
        </div>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Чай", "https://vkusmart.vmv.kz/catalog/chay/", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "12345")
        self.assertEqual(items[0]["shop"], "Вкусмарт")
        self.assertEqual(items[0]["title"], "Чай Greenfield Golden Ceylon 100 пакетиков")
        self.assertEqual(items[0]["price"], 1850)
        self.assertEqual(items[0]["old_price_on_site"], 2200)
        self.assertEqual(items[0]["url"], "https://vkusmart.vmv.kz/catalog/chay/12345/")
        self.assertEqual(items[0]["image_url"], "https://vkusmart.vmv.kz/upload/tea.jpg")
        self.assertEqual(items[0]["description"], "Черный цейлонский чай")
        scraper.close()
        self.assertIsNone(scraper.session)


if __name__ == "__main__":
    unittest.main()
