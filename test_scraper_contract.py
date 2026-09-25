"""Tests for the unified Scraper contract, Scraper Protocol, and product validator."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
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
from scrapers.masterok import MasterOkScraper
from scrapers.magnum import MagnumScraper
from scrapers.intertop import IntertopScraper
from scrapers.marwin import MarwinScraper
from scrapers.iteka import ITekaScraper
from scrapers.mebel import MebelScraper
from scrapers.detmir import DetmirScraper
from scrapers.askona import AskonaScraper
from scrapers.zoomarket import ZooMarketScraper


class ScraperContractTest(unittest.TestCase):
    def test_scraper_classes_conform_to_contract(self):
        # Check standard scrapers
        for cls in (TechnodomScraper, ForteMarketScraper, FourMobileScraper, ShopKzScraper, KaspiScraper, VkusmartScraper, TwelveMonthsScraper, ZetaScraper, KomfortScraper, LemanaProScraper, ArbuzScraper, MasterOkScraper, MagnumScraper, IntertopScraper, MarwinScraper, ITekaScraper, MebelScraper, DetmirScraper, AskonaScraper, ZooMarketScraper):
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

    def test_masterok_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = MasterOkScraper()
        mock_html = '''
        <div class="catalog-item-card" data-entity="item" id="bx_40480796_40732_52eccb44ded0bb34f72b273e9a62ef02" itemscope itemtype="http://schema.org/Product">
            <div class="item-image-cont">
                <div class="item-image">
                    <meta itemprop="image" content="/upload/iblock/fubag.jpg">
                    <a href="/catalog/instrumenty/derzhatel-dlya-pnevmoinstrumenta-fubag-10sht/">
                        <img class="item_img" src="/upload/iblock/fubag.jpg" alt="Держатель для пневмоинструмента, FUBAG 10шт">
                    </a>
                </div>
            </div>
            <div class="item-all-title">
                <a class="item-title" href="/catalog/instrumenty/derzhatel-dlya-pnevmoinstrumenta-fubag-10sht/" itemprop="url">
                    <span itemprop="name">Держатель для пневмоинструмента, FUBAG 10шт</span>
                </a>
            </div>
            <div class="article_rating">
                <div class="article">Артикул: 5260003</div>
            </div>
            <div class="item-desc" itemprop="description">
                Назначение: для аккуратного хранения
            </div>
            <div class="item-price-cont" itemprop="offers" itemscope itemtype="http://schema.org/Offer">
                <div class="item-price">
                    <span class="catalog-item-price-old">22 462 тг</span>
                    <span class="catalog-item-price">12 387 <span class="unit">тг</span></span>
                </div>
                <meta itemprop="price" content="12387">
                <meta itemprop="priceCurrency" content="KZT">
                <meta itemprop="availability" content="InStock">
            </div>
        </div>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Инструменты", "https://masterok.kz/catalog/instrumenty/", 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "40732")
        self.assertEqual(items[0]["sku"], "5260003")
        self.assertEqual(items[0]["shop"], "MasterOK")
        self.assertEqual(items[0]["title"], "Держатель для пневмоинструмента, FUBAG 10шт")
        self.assertEqual(items[0]["price"], 12387)
        self.assertEqual(items[0]["old_price_on_site"], 22462)
        self.assertEqual(items[0]["url"], "https://masterok.kz/catalog/instrumenty/derzhatel-dlya-pnevmoinstrumenta-fubag-10sht/")
        self.assertEqual(items[0]["image_url"], "https://masterok.kz/upload/iblock/fubag.jpg")
        self.assertEqual(items[0]["city"], "Алматы")
        self.assertIn("аккуратного хранения", items[0]["description"])
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_magnum_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = MagnumScraper(city="Алматы")
        mock_payload = [
            {
                "id": 1930,
                "name": "ОГУРЦЫ «BONDUELLE» МАРИНОВАННЫЕ 680 Г",
                "start_price": 2179,
                "final_price": 1199,
                "discount": 0.45,
                "image": "/uploads/bonduelle.jpg",
                "discount_type": {
                    "conditions": "Акция действует во всех магазинах"
                }
            }
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_payload

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        res = scraper._scrape_sync("Бакалея", "https://magnum.kz/catalog?category=bakaleia")
        self.assertTrue(res.complete)
        self.assertIsNone(res.error)
        self.assertEqual(len(res), 1)
        item = res[0]
        self.assertEqual(item["id"], "magnum_1930")
        self.assertEqual(item["shop"], "Магнум")
        self.assertEqual(item["title"], "ОГУРЦЫ «BONDUELLE» МАРИНОВАННЫЕ 680 Г")
        self.assertEqual(item["price"], 1199)
        self.assertEqual(item["old_price_on_site"], 2179)
        self.assertEqual(item["url"], "https://magnum.kz/products/1930")
        self.assertEqual(item["image_url"], "https://magnum.kz:1337/uploads/bonduelle.jpg")
        self.assertEqual(item["description"], "Акция действует во всех магазинах")
        self.assertEqual(item["city"], "Алматы")

        # Live search test
        matches = scraper._search_live_sync("bonduelle огурцы")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], "magnum_1930")

        no_matches = scraper._search_live_sync("несуществующий")
        self.assertEqual(len(no_matches), 0)

        # Empty response
        mock_resp.json.return_value = []
        res_empty = scraper._scrape_sync("Бакалея", "https://magnum.kz/catalog?category=bakaleia")
        self.assertFalse(res_empty.complete)
        self.assertEqual(res_empty.error, "Пустая выдача: полнота не подтверждена")

        # HTTP error
        mock_resp.status_code = 500
        res_err = scraper._scrape_sync("Бакалея", "https://magnum.kz/catalog?category=bakaleia")
        self.assertFalse(res_err.complete)
        self.assertIn("HTTP", res_err.error)

        scraper.close()
        self.assertIsNone(scraper.session)

    def test_intertop_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = IntertopScraper()
        mock_html = '''
        <html><body>
        <div class="in-product-tile" data-product-id="10193392" data-product-sku="FW0FW08424-TRY">
            <a href="/ru-kz/product/sandals-tommy-hilfiger-10193392/">
                <div class="in-product-tile__product-brand">Tommy Hilfiger</div>
                <div class="in-product-tile__product-name">Босоножки</div>
            </a>
            <div class="in-price__regular">₸ 85 990</div>
            <div class="in-price__actual">₸ 68 790</div>
            <img class="in-picture__img" src="https://kz.media.intertop.com/load/mp676428/small/MAIN.webp" alt="Босоножки Tommy Hilfiger Фото" />
        </div>
        <div class="pagination">
            <a href="/ru-kz/shopping/catalog/women/shoes/?page=2">2</a>
        </div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Женская обувь", "https://intertop.kz/ru-kz/shopping/catalog/women/shoes/", 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "intertop_10193392")
        self.assertEqual(item["sku"], "FW0FW08424-TRY")
        self.assertEqual(item["shop"], "Интертоп")
        self.assertEqual(item["title"], "Tommy Hilfiger Босоножки")
        self.assertEqual(item["price"], 68790)
        self.assertEqual(item["old_price_on_site"], 85990)
        self.assertEqual(item["url"], "https://intertop.kz/ru-kz/product/sandals-tommy-hilfiger-10193392/")
        self.assertEqual(item["image_url"], "https://kz.media.intertop.com/load/mp676428/small/MAIN.webp")
        self.assertEqual(item["city"], "Алматы / Казахстан")
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_marwin_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = MarwinScraper()
        mock_html = '''
        <html><body>
        <li class="product-item">
            <div class="product-item-info" data-product-id="3595999">
                <a class="product-item-photo" href="/press/banner.html">
                    <img class="product-image-photo" data-src="https://simg.marwin.kz/media/catalog/product/ps5.png" alt="PS5" />
                </a>
                <strong class="product-item-name">
                    <a class="product-item-link" href="https://www.marwin.kz/videogames/ps5.html" data-product-name="Игровая консоль PlayStation 5 Slim">
                        Игровая консоль PlayStation 5 Slim
                    </a>
                </strong>
                <div class="price-box">
                    <span data-price-type="oldPrice" data-price-amount="420000"></span>
                    <span data-price-type="finalPrice" data-price-amount="389990">
                        <span class="price">389 990 ₸</span>
                    </span>
                </div>
            </div>
        </li>
        <div class="pages">
            <ul class="pages-items">
                <li class="item"><a class="page" href="?p=2"><span>2</span></a></li>
            </ul>
        </div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Видеоигры и консоли", "https://www.marwin.kz/videogames/", 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "marwin_3595999")
        self.assertEqual(item["sku"], "3595999")
        self.assertEqual(item["shop"], "Меломан")
        self.assertEqual(item["title"], "Игровая консоль PlayStation 5 Slim")
        self.assertEqual(item["price"], 389990)
        self.assertEqual(item["old_price_on_site"], 420000)
        self.assertEqual(item["url"], "https://www.marwin.kz/videogames/ps5.html")
        self.assertEqual(item["image_url"], "https://simg.marwin.kz/media/catalog/product/ps5.png")
        self.assertEqual(item["city"], "Алматы / Казахстан")
        self.assertFalse(items.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_marwin_live_search_mock(self):
        from unittest.mock import MagicMock
        scraper = MarwinScraper()
        mock_html = '''
        <html><body>
        <div class="product-item-info" data-product-id="123">
            <a class="product-item-link" href="https://www.marwin.kz/books/lego.html">Книга LEGO</a>
            <span data-price-type="finalPrice" data-price-amount="5000"></span>
            <img class="product-image-photo" src="https://simg.marwin.kz/img.jpg" />
        </div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("LEGO", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Книга LEGO")
        self.assertEqual(results[0]["price"], 5000)
        scraper.close()

    def test_iteka_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = ITekaScraper()
        mock_html = '''
        <html><body>
        <div class="rounded-16 bg-white p-4">
            <a href="/astana/medicaments/paracetamol-500mg-tab-n10-12345">Парацетамол 500мг таб №10</a>
            <img src="https://i-teka.kz/images/paracetamol.jpg" />
            <button x-data="addToCartButton({ drugId: '12345', drugPrice: 250, count: 1 })">Купить</button>
            <span class="line-through">300 ₸</span>
        </div>
        <ul class="pagination">
            <li class="page-item"><a class="page-link" href="?GlossaryTnfull_page=2">2</a></li>
        </ul>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Обезболивающие", "https://i-teka.kz/astana/medicaments/obezbolivayushie-preparaty", 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "iteka_12345")
        self.assertEqual(item["sku"], "12345")
        self.assertEqual(item["shop"], "i-Teka")
        self.assertEqual(item["title"], "Парацетамол 500мг таб №10")
        self.assertEqual(item["price"], 250)
        self.assertEqual(item["old_price_on_site"], 300)
        self.assertEqual(item["url"], "https://i-teka.kz/astana/medicaments/paracetamol-500mg-tab-n10-12345")
        self.assertEqual(item["image_url"], "https://i-teka.kz/images/paracetamol.jpg")
        self.assertEqual(item["city"], "Астана")
        self.assertFalse(items.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_iteka_live_search_mock(self):
        from unittest.mock import MagicMock
        scraper = ITekaScraper()
        mock_html = '''
        <html><body>
        <div class="rounded-16 bg-white p-4">
            <a href="/astana/medicaments/aspirin-100mg-54321">Аспирин Кардио 100мг</a>
            <button x-data="addToCartButton({ drugId: '54321', drugPrice: 1500 })">Купить</button>
        </div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("Аспирин", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Аспирин Кардио 100мг")
        self.assertEqual(results[0]["price"], 1500)
        self.assertEqual(results[0]["shop"], "i-Teka")
        scraper.close()

    def test_mebel_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = MebelScraper()
        mock_html = '''
        <html><body>
        <div class="ProductCardMain-module__4dYtKq__container">
            <a href="/product/divan-test-slug-123">
                <img src="https://cdn.servicecdn.ru/img/divan.jpg" alt="Диван Тестовый" />
            </a>
            <div class="ProductName">Диван прямой Тестовый</div>
            <span class="FullPrice-module__Dh5lpq__actual" data-testid="price">250 000 ₸</span>
            <span class="FullPrice-module__Dh5lpq__expired" data-testid="price">300 000 ₸</span>
        </div>
        <div class="pagination">
            <a href="/category/divany/page-2">2</a>
        </div>
        </body></html>
        '''
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        items = scraper._fetch_page("Диваны", "https://mebel.kz/category/divany", 1)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], "mebel_divan-test-slug-123")
        self.assertEqual(item["sku"], "divan-test-slug-123")
        self.assertEqual(item["shop"], "Mebel.kz")
        self.assertEqual(item["title"], "Диван прямой Тестовый")
        self.assertEqual(item["price"], 250000)
        self.assertEqual(item["old_price_on_site"], 300000)
        self.assertEqual(item["url"], "https://mebel.kz/product/divan-test-slug-123")
        self.assertEqual(item["image_url"], "https://cdn.servicecdn.ru/img/divan.jpg")
        self.assertEqual(item["city"], "Алматы / Казахстан")
        self.assertFalse(items.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_mebel_live_search_mock(self):
        from unittest.mock import MagicMock
        scraper = MebelScraper()
        mock_data = {
            "ok": True,
            "data": {
                "products": [
                    {
                        "id": 998877,
                        "type": "Кресло офисное",
                        "name": "Эргономик Люкс",
                        "link": "/product/kreslo-ergonomic-lux",
                        "price": {
                            "actual": 85000,
                            "expired": 105000
                        },
                        "images": [
                            {"src": "https://cdn.servicecdn.ru/kreslo.jpg"}
                        ]
                    }
                ]
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("кресло", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Кресло офисное Эргономик Люкс")
        self.assertEqual(results[0]["price"], 85000)
        self.assertEqual(results[0]["old_price_on_site"], 105000)
        self.assertEqual(results[0]["shop"], "Mebel.kz")
        self.assertEqual(results[0]["url"], "https://mebel.kz/product/kreslo-ergonomic-lux")
        scraper.close()

    def test_detmir_parsing_mock(self):
        import json
        from unittest.mock import MagicMock
        scraper = DetmirScraper()

        app_payload = {
            "catalog": {
                "data": {
                    "meta": {"productsLength": 100},
                    "items": [
                        {
                            "id": "778899",
                            "title": "Конструктор LEGO City Пожарный самолет",
                            "price": {"price": 14990},
                            "old_price": {"price": 19990},
                            "link": {"web_url": "https://detmir.kz/product/index/id/778899/"},
                            "pictures": [{"original": "https://catalog-cdn.detmir.st/lego.jpg"}]
                        }
                    ]
                }
            }
        }
        json_inner = json.dumps(app_payload)
        json_arg = json.dumps(json_inner)
        html = f"""
        <html><body>
        <script>window.appData = JSON.parse({json_arg});</script>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        res = scraper._fetch_page("Конструкторы", "https://detmir.kz/catalog/index/name/konstruktory/", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "detmir_778899")
        self.assertEqual(res[0]["title"], "Конструктор LEGO City Пожарный самолет")
        self.assertEqual(res[0]["price"], 14990)
        self.assertEqual(res[0]["old_price_on_site"], 19990)
        self.assertEqual(res[0]["shop"], "Детский мир")
        self.assertEqual(res[0]["url"], "https://detmir.kz/product/index/id/778899/")
        self.assertFalse(res.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_detmir_live_search_mock(self):
        import json
        from unittest.mock import MagicMock
        scraper = DetmirScraper()

        app_payload = {
            "catalog": {
                "data": {
                    "items": [
                        {
                            "id": "554433",
                            "title": "Подгузники Pampers Active Baby-Dry 4 (9-14 кг) 106 шт.",
                            "price": {"price": 12490},
                            "old_price": {"price": 15990},
                            "link": {"web_url": "https://detmir.kz/product/index/id/554433/"},
                            "pictures": [{"original": "https://catalog-cdn.detmir.st/pampers.jpg"}]
                        }
                    ]
                }
            }
        }
        json_inner = json.dumps(app_payload)
        json_arg = json.dumps(json_inner)
        html = f"""
        <html><body>
        <script>window.appData = JSON.parse({json_arg});</script>
        </body></html>
        """
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("памперсы", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Подгузники Pampers Active Baby-Dry 4 (9-14 кг) 106 шт.")
        self.assertEqual(results[0]["price"], 12490)
        self.assertEqual(results[0]["old_price_on_site"], 15990)
        self.assertEqual(results[0]["shop"], "Детский мир")
        self.assertEqual(results[0]["url"], "https://detmir.kz/product/index/id/554433/")
        scraper.close()

    def test_askona_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = AskonaScraper()

        html = """
        <html>
        <body>
          <div class="card-v6" data-cur-sku-id="12345" data-id="12345">
            <a class="card-v6__title" href="/matrasy/ergonomic-sleep-160x200/">Анатомический матрас Sleep Lux 160х200</a>
            <div class="card-v6__price-actual">189 990 ₸</div>
            <div class="card-v6__price-old">249 990 ₸</div>
            <img src="/upload/iblock/sleep_lux.jpg" />
          </div>
          <div class="pagination-v3">
            <a href="/matrasy/page/1/">1</a>
            <a href="/matrasy/page/2/">2</a>
            <a href="/matrasy/page/3/">3</a>
          </div>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        res = scraper._fetch_page("Матрасы", "https://askona.kz/matrasy/", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "askona_12345")
        self.assertEqual(res[0]["title"], "Анатомический матрас Sleep Lux 160х200")
        self.assertEqual(res[0]["price"], 189990)
        self.assertEqual(res[0]["old_price_on_site"], 249990)
        self.assertEqual(res[0]["shop"], "Askona")
        self.assertEqual(res[0]["url"], "https://askona.kz/matrasy/ergonomic-sleep-160x200/")
        self.assertEqual(res[0]["image_url"], "https://askona.kz/upload/iblock/sleep_lux.jpg")
        self.assertFalse(res.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_askona_live_search_mock(self):
        from unittest.mock import MagicMock
        scraper = AskonaScraper()

        html = """
        <html>
        <body>
          <div class="card-v6" data-cur-sku-id="98765" data-id="98765">
            <a class="card-v6__title" href="/podushki/alpha-gel/">Анатомическая подушка Alpha Gel</a>
            <div class="card-v6__price-actual">29 990 ₸</div>
            <div class="card-v6__price-old">39 990 ₸</div>
            <img src="https://askona.kz/upload/alpha.jpg" />
          </div>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("подушка", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "askona_98765")
        self.assertEqual(results[0]["title"], "Анатомическая подушка Alpha Gel")
        self.assertEqual(results[0]["price"], 29990)
        self.assertEqual(results[0]["old_price_on_site"], 39990)
        self.assertEqual(results[0]["shop"], "Askona")
        self.assertEqual(results[0]["url"], "https://askona.kz/podushki/alpha-gel/")
        scraper.close()

    def test_zoomarket_parsing_mock(self):
        from unittest.mock import MagicMock
        scraper = ZooMarketScraper()

        html = """
        <html>
        <body>
          <div class="catalog_item" id="bx_123_45678" data-param-id="45678">
            <div class="item-title">
              <a class="dark_link link-product-page" href="/catalog/cat/korm_k/vzroslye/45678/">
                <span>Корм Carny Chicken для кошек 10 кг</span>
              </a>
            </div>
            <div class="cost prices clearfix">
              <div class="price" data-value="18500">
                <span class="price_value">18 500</span>
              </div>
              <div class="price price_old" data-value="22000">
                <span class="price_value">22 000</span>
              </div>
            </div>
            <a class="thumb" href="/catalog/cat/korm_k/vzroslye/45678/">
              <img src="/upload/iblock/carny.jpg" />
            </a>
          </div>
          <div class="nums">
            <span class="cur">1</span>
            <a href="/catalog/cat/korm_k/?PAGEN_1=2">2</a>
            <a href="/catalog/cat/korm_k/?PAGEN_1=3">3</a>
          </div>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        res = scraper._fetch_page("Корма для кошек", "https://zoomarket.kz/catalog/cat/korm_k/", 1)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["id"], "zoomarket_45678")
        self.assertEqual(res[0]["title"], "Корм Carny Chicken для кошек 10 кг")
        self.assertEqual(res[0]["price"], 18500)
        self.assertEqual(res[0]["old_price_on_site"], 22000)
        self.assertEqual(res[0]["shop"], "Зоомаркет")
        self.assertEqual(res[0]["url"], "https://zoomarket.kz/catalog/cat/korm_k/vzroslye/45678/")
        self.assertEqual(res[0]["image_url"], "https://zoomarket.kz/upload/iblock/carny.jpg")
        self.assertFalse(res.complete)
        scraper.close()
        self.assertIsNone(scraper.session)

    def test_zoomarket_live_search_mock(self):
        from unittest.mock import MagicMock
        scraper = ZooMarketScraper()

        html = """
        <html>
        <body>
          <div class="catalog_item" data-param-id="998877">
            <div class="item-title">
              <a class="link-product-page" href="/catalog/dog/korm/998877/">
                <span>Корм Royal Canin Mini Puppy 4 кг</span>
              </a>
            </div>
            <div class="price" data-value="12400">
              <span class="price_value">12 400</span>
            </div>
          </div>
        </body>
        </html>
        """

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        scraper.session = mock_session

        results = scraper.search_live("royal canin", limit=5)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "zoomarket_998877")
        self.assertEqual(results[0]["title"], "Корм Royal Canin Mini Puppy 4 кг")
        self.assertEqual(results[0]["price"], 12400)
        self.assertEqual(results[0]["shop"], "Зоомаркет")
        self.assertEqual(results[0]["url"], "https://zoomarket.kz/catalog/dog/korm/998877/")
        scraper.close()


if __name__ == "__main__":
    unittest.main()




