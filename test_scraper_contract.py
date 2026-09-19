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
from scrapers.twelve_months import TwelveMonthsScraper
from scrapers.zeta import ZetaScraper
from scrapers.komfort import KomfortScraper
from scrapers.lemanapro import LemanaProScraper
from scrapers.arbuz import ArbuzScraper


class ScraperContractTest(unittest.TestCase):
    def test_scraper_classes_conform_to_contract(self):
        # Check standard scrapers
        for cls in (TechnodomScraper, ForteMarketScraper, FourMobileScraper, ShopKzScraper, KaspiScraper, VkusmartScraper, TwelveMonthsScraper, ZetaScraper, KomfortScraper, LemanaProScraper, ArbuzScraper):
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

    def test_twelve_months_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = TwelveMonthsScraper()
        mock_html = '''
        <div class="products-view-item js-products-view-item" data-product-id="55543" data-offer-id="78816">
            <div class="products-view-name">
                <a href="/products/0434357" class="products-view-name-link">Дрель ударная ALTECO DP 800</a>
            </div>
            <div class="products-view-price">
                <div class="price">
                    <div class="price-old cs-t-3">
                        <div class="price-number">28 990</div>
                    </div>
                    <div class="price-new cs-t-1">
                        <div class="price-number">22 500</div>
                    </div>
                </div>
            </div>
            <img class="products-view-picture" src="/pictures/product/small/48261_small.png" alt="Дрель" />
            <div class="info">Артикул: 0434357 Есть в наличии</div>
        </div>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Дрели", "https://12.kz/categories/dreli/", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "55543")
        self.assertEqual(items[0]["shop"], "12 Месяцев")
        self.assertEqual(items[0]["title"], "Дрель ударная ALTECO DP 800")
        self.assertEqual(items[0]["price"], 22500)
        self.assertEqual(items[0]["old_price_on_site"], 28990)
        self.assertEqual(items[0]["url"], "https://12.kz/products/0434357")
        self.assertEqual(items[0]["image_url"], "https://12.kz/pictures/product/small/48261_small.png")
        self.assertEqual(items[0]["sku"], "0434357")
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_zeta_parsing_mock(self):
        import json
        from unittest.mock import MagicMock
        scraper = ZetaScraper()
        mock_payload = {
            "results": [
                {
                    "id": "6816190cf545f61b54721d24",
                    "name": {"ru": "Бак для воды (35 л.)"},
                    "price": 2205,
                    "oldPrice": 2500,
                    "available": True,
                    "article": "ПЛ-00309",
                    "slug": "bak-35-l",
                    "fullSlug": ["dacha-i-sad/emkosti/bak-35-l"],
                    "images": ["/uploads/bak.jpg"]
                }
            ],
            "resultCount": 1,
            "page": 1
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Емкости", "6851938995dd04035cad42d6", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "6816190cf545f61b54721d24")
        self.assertEqual(items[0]["shop"], "Zeta")
        self.assertEqual(items[0]["title"], "Бак для воды (35 л.)")
        self.assertEqual(items[0]["price"], 2205)
        self.assertEqual(items[0]["old_price_on_site"], 2500)
        self.assertEqual(items[0]["url"], "https://zeta.kz/catalog/dacha-i-sad/emkosti/bak-35-l")
        self.assertEqual(items[0]["image_url"], "https://zeta.kz/uploads/bak.jpg")
        self.assertEqual(items[0]["sku"], "ПЛ-00309")
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_komfort_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = KomfortScraper()
        mock_html = '''
        <div class="catalog-block-view__item" data-id="145568">
            <div class="item-title">
                <a class="dark_link" href="/catalog/instrumenty/elektroistrumenty/dreli_shurupoverty/145568/">
                    Дрель ЗУБР "ПРОФЕССИОНАЛ" безударная реверсивная
                </a>
            </div>
            <div class="cost prices">
                <div class="price" data-value="21990">
                    <span class="price_value">21 990₸/шт</span>
                </div>
                <div class="price_old" data-value-old="25990">25 990₸/шт</div>
            </div>
            <div class="thumb">
                <img data-src="/upload/zubr.webp" alt="Дрель ЗУБР" />
            </div>
        </div>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Дрели", "https://komfort.kz/catalog/dreli/", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "145568")
        self.assertEqual(items[0]["shop"], "Комфорт")
        self.assertEqual(items[0]["title"], 'Дрель ЗУБР "ПРОФЕССИОНАЛ" безударная реверсивная')
        self.assertEqual(items[0]["price"], 21990)
        self.assertEqual(items[0]["old_price_on_site"], 25990)
        self.assertEqual(items[0]["url"], "https://komfort.kz/catalog/instrumenty/elektroistrumenty/dreli_shurupoverty/145568/")
        self.assertEqual(items[0]["image_url"], "https://komfort.kz/upload/zubr.webp")
        self.assertEqual(items[0]["city"], "Алматы")
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_lemanapro_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = LemanaProScraper()
        mock_html = '''
        <div class="largeCard" data-qa-product="" data-qa="product">
            <span data-qa="product-article">Арт. 89426501</span>
            <a data-qa="product-name" href="/product/drel-shurupovert-alteco-89426501/">
                <span class="product-card-name-link">Дрель-шуруповерт аккумуляторная Alteco CD 12-23</span>
            </a>
            <div data-testid="price-block-oldprice" value="15670">15 670 ₸</div>
            <div data-testid="price-block-price" value="14150">14 150 ₸/шт.</div>
            <img src="https://cdn.lemanapro.ru/lmru/image/upload/alteco.png" alt="Дрель Alteco" />
        </div>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Дрели", "https://lemanapro.kz/catalogue/dreli/", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "89426501")
        self.assertEqual(items[0]["sku"], "89426501")
        self.assertEqual(items[0]["shop"], "Лемана ПРО")
        self.assertEqual(items[0]["title"], "Дрель-шуруповерт аккумуляторная Alteco CD 12-23")
        self.assertEqual(items[0]["price"], 14150)
        self.assertEqual(items[0]["old_price_on_site"], 15670)
        self.assertEqual(items[0]["url"], "https://lemanapro.kz/product/drel-shurupovert-alteco-89426501/")
        self.assertEqual(items[0]["image_url"], "https://cdn.lemanapro.ru/lmru/image/upload/alteco.png")
        self.assertEqual(items[0]["city"], "Казахстан")
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_arbuz_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = ArbuzScraper()
        mock_html = '''
        <article class="product-item product-card">
            <div class="product-card__content">
                <a class="product-card__link" href="/ru/almaty/catalog/item/351031-sredstvo_mello_home_dlya_mytya_posudy_aloe_0_5_l" title="Средство Mello Home для мытья посуды Алоэ 0.5 л">
                    <img class="product-card__img" v-lazy="'https://arbuz.kz/image/s3/arbuz-kz-products/file.jpg?w=%w&h=%h&_c=123'" alt="Средство Mello Home" />
                </a>
            </div>
            <main class="product-card__body">
                <a class="product-card__title" href="/ru/almaty/catalog/item/351031-sredstvo_mello_home_dlya_mytya_posudy_aloe_0_5_l">
                    Средство Mello Home для мытья посуды Алоэ 0.5 л
                </a>
                <p class="product-card__price">
                    <b>1 355 ₸</b>
                    <s class="product-card__price-previous">1 500 ₸</s>
                </p>
            </main>
        </article>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Средства для мытья посуды", "https://arbuz.kz/ru/almaty/catalog/cat/224494-sredstva_dlya_mytya_posudy", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "351031")
        self.assertEqual(items[0]["sku"], "351031")
        self.assertEqual(items[0]["shop"], "Arbuz")
        self.assertEqual(items[0]["title"], "Средство Mello Home для мытья посуды Алоэ 0.5 л")
        self.assertEqual(items[0]["price"], 1355)
        self.assertEqual(items[0]["old_price_on_site"], 1500)
        self.assertEqual(items[0]["url"], "https://arbuz.kz/ru/almaty/catalog/item/351031-sredstvo_mello_home_dlya_mytya_posudy_aloe_0_5_l")
        self.assertEqual(items[0]["image_url"], "https://arbuz.kz/image/s3/arbuz-kz-products/file.jpg?w=360&h=360&_c=123")
        self.assertEqual(items[0]["city"], "Алматы")
        scraper.close()
        self.assertIsNone(scraper.session)


if __name__ == "__main__":
    unittest.main()




