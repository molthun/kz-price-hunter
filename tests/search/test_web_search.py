"""
Unit and integration tests for domain.web_search and SearXNG integration.
"""
from tests import test_support  # noqa: F401
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import database
from domain.web_search import (
    KNOWN_KZ_SHOPS,
    build_search_query,
    get_shop_info,
    parse_price,
    search_web,
)


class TestWebSearchDomain(unittest.TestCase):
    def test_get_shop_info_known_partners(self):
        # Kaspi
        k1 = get_shop_info("https://kaspi.kz/shop/p/iphone-15-113137790/")
        self.assertEqual(k1["shop_name"], "Kaspi Магазин")
        self.assertEqual(k1["badge"], "Kaspi")
        self.assertTrue(k1["is_known_partner"])

        # Mechta
        m1 = get_shop_info("www.mechta.kz")
        self.assertEqual(m1["shop_name"], "Мечта")
        self.assertEqual(m1["badge"], "Мечта")
        self.assertTrue(m1["is_known_partner"])

        # Белый Ветер
        s1 = get_shop_info("https://shop.kz/offers/videokarty/")
        self.assertEqual(s1["shop_name"], "Белый Ветер")
        self.assertEqual(s1["badge"], "Белый Ветер")

    def test_get_shop_info_unknown_domain(self):
        u1 = get_shop_info("https://myrandomshop.kz/item/123")
        self.assertEqual(u1["domain"], "myrandomshop.kz")
        self.assertEqual(u1["badge"], "Myrandomshop")
        self.assertFalse(u1["is_known_partner"])

    def test_parse_price(self):
        # Различные форматы цен в Казахстане
        self.assertEqual(parse_price("Кофемашина DeLonghi от 159 990 ₸ в Алматы"), 159990)
        self.assertEqual(parse_price("Купить за 29900 тг"), 29900)
        self.assertEqual(parse_price("Цена 1 250 000 тенге с НДС"), 1250000)
        self.assertEqual(parse_price("Скидка 45 000 KZT"), 45000)
        self.assertIsNone(parse_price("Просто товар без цены"))
        self.assertIsNone(parse_price("15 штук в упаковке"))

    def test_build_search_query_kz_shops(self):
        q = build_search_query("iPhone 15", city="Астана", mode="kz_shops")
        self.assertEqual(q, "iPhone 15 Астана купить")

        # Если уже содержит site:, не добавляем второй раз
        q2 = build_search_query("iPhone 15 site:kaspi.kz", mode="kz_shops")
        self.assertEqual(q2, "iPhone 15 site:kaspi.kz")

        # Если уже содержит город или купить
        q3 = build_search_query("iPhone 15 купить в Алматы", mode="kz_shops")
        self.assertEqual(q3, "iPhone 15 купить в Алматы")

    def test_build_search_query_kz_general(self):
        q = build_search_query("детская коляска", city="Алматы", mode="kz_general")
        self.assertEqual(q, "детская коляска Алматы купить")

        q_all = build_search_query("детская коляска", city="Все", mode="kz_general")
        self.assertEqual(q_all, "детская коляска Казахстан купить")


class TestWebSearchAsync(unittest.IsolatedAsyncioTestCase):
    async def test_search_web_from_cache(self):
        cached_data = [
            {
                "title": "Купить iPhone 15",
                "url": "https://kaspi.kz/shop/p/123",
                "snippet": "Отличный смартфон 299 990 ₸",
                "domain": "kaspi.kz",
                "shop_name": "Kaspi Магазин",
                "badge": "Kaspi",
                "color": "#f14635",
                "price": 299990,
                "currency": "₸",
                "engine": "duckduckgo",
            }
        ]
        with patch.object(database, "get_web_search", return_value=cached_data):
            res = await search_web("iPhone 15", mode="kz_shops")
            self.assertTrue(res["ok"])
            self.assertTrue(res["from_cache"])
            self.assertEqual(res["total"], 1)
            self.assertEqual(res["results"][0]["shop_name"], "Kaspi Магазин")

    async def test_search_web_live_and_save(self):
        mock_searxng_resp = {
            "results": [
                {
                    "title": "Ноутбук ASUS ROG Strix G16 от 750 000 ₸",
                    "url": "https://www.mechta.kz/product/asus-rog-strix/",
                    "content": "Мощный игровой ноутбук ASUS ROG Strix в наличии в Астане",
                    "engine": "duckduckgo",
                }
            ]
        }

        class MockResponse:
            status = 200

            async def json(self, content_type=None):
                return mock_searxng_resp

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class MockSession:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                return MockResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch.object(database, "get_web_search", return_value=None), \
             patch.object(database, "reserve_web_search", return_value=True), \
             patch.object(database, "save_web_search") as mock_save, \
             patch("aiohttp.ClientSession", MockSession):

            res = await search_web("ASUS ROG Strix", city="Астана", mode="kz_shops")

            self.assertTrue(res["ok"])
            self.assertFalse(res["from_cache"])
            self.assertEqual(res["total"], 1)
            item = res["results"][0]
            self.assertEqual(item["shop_name"], "Мечта")
            self.assertEqual(item["price"], 750000)
            self.assertEqual(item["currency"], "₸")
            mock_save.assert_called_once()

    async def test_search_web_network_failure_handled(self):
        class FailingSession:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                raise ConnectionResetError("Connection lost")

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch.object(database, "get_web_search", return_value=None), \
             patch.object(database, "reserve_web_search", return_value=True), \
             patch("aiohttp.ClientSession", FailingSession):

            res = await search_web("iPhone 15", mode="kz_shops")
            self.assertFalse(res["ok"])
            self.assertIn("временно недоступен", res["error"])
            self.assertEqual(res["results"], [])


    async def test_search_web_partner_priority_ranking(self):
        # Проверяем, что в режиме kz_shops известные магазины и товары с ценой идут первыми
        mock_searxng_resp = {
            "results": [
                {
                    "title": "Случайный зарубежный блог о технике",
                    "url": "https://techblog.ru/article/123",
                    "content": "Обзор видеокарты без цен",
                    "engine": "duckduckgo",
                },
                {
                    "title": "Видеокарта RTX 4070 за 350 000 ₸",
                    "url": "https://forcecom.kz/catalog/rtx4070",
                    "content": "Купить с доставкой по Алматы",
                    "engine": "duckduckgo",
                },
                {
                    "title": "Видеокарта в магазине Мечта за 360 000 ₸",
                    "url": "https://www.mechta.kz/product/rtx4070",
                    "content": "Официальная гарантия в Казахстане",
                    "engine": "duckduckgo",
                },
            ]
        }

        class MockResponse:
            status = 200

            async def json(self, content_type=None):
                return mock_searxng_resp

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class MockSession:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                return MockResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch.object(database, "get_web_search", return_value=None), \
             patch.object(database, "reserve_web_search", return_value=True), \
             patch.object(database, "save_web_search"), \
             patch("aiohttp.ClientSession", MockSession):

            res = await search_web("RTX 4070", mode="kz_shops", limit=5)
            self.assertTrue(res["ok"])
            self.assertEqual(res["total"], 3)
            # Первые два должны быть известными магазинами (Мечта и Forcecom)
            first_two = [r["shop_name"] for r in res["results"][:2]]
            self.assertIn("Мечта", first_two)
            self.assertIn("Forcecom", first_two)
            # Последним должен идти чужой блог
            self.assertEqual(res["results"][2]["domain"], "techblog.ru")


    def test_olx_and_classifieds_metadata(self):
        info = get_shop_info("https://www.olx.kz/d/obyavlenie/iphone-16-pro-ID123.html")
        self.assertEqual(info["shop_name"], "OLX Казахстан")
        self.assertEqual(info["badge"], "OLX")
        self.assertEqual(info["channel_type"], "classifieds")
        self.assertTrue(info["is_classifieds"])

        kaspi_adv = get_shop_info("https://obyavleniya.kaspi.kz/item/123")
        self.assertEqual(kaspi_adv["channel_type"], "classifieds")
        self.assertTrue(kaspi_adv["is_classifieds"])

        # Обычный магазин
        mechta = get_shop_info("https://www.mechta.kz/product/123")
        self.assertEqual(mechta["channel_type"], "store")
        self.assertFalse(mechta["is_classifieds"])

    async def test_retail_stores_outrank_olx_classifieds(self):
        # Проверяем, что даже если у OLX есть цена, а у официального магазина в сниппете её нет,
        # официальный магазин (Kaspi, DNS, iSpace) ранжируется выше объявлений OLX
        mock_searxng_resp = {
            "results": [
                {
                    "title": "iPhone 16 Pro на OLX за 590 000 ₸",
                    "url": "https://www.olx.kz/d/obyavlenie/iphone-16-pro-ID1.html",
                    "content": "Продам б/у телефон в идеальном состоянии",
                    "engine": "duckduckgo",
                },
                {
                    "title": "Смартфон Apple iPhone 16 Pro в интернет-магазине DNS",
                    "url": "https://dns-shop.kz/product/iphone16pro/",
                    "content": "Официальная гарантия 1 год, доставка в Алматы",
                    "engine": "duckduckgo",
                },
                {
                    "title": "Купить Apple iPhone 16 Pro в рассрочку в Kaspi Магазине",
                    "url": "https://kaspi.kz/shop/p/apple-iphone-16-pro-128gb-123789/",
                    "content": "Kaspi Red, рассрочка 0-0-24, бесплатная доставка",
                    "engine": "duckduckgo",
                },
            ]
        }

        class MockResponse:
            status = 200

            async def json(self, content_type=None):
                return mock_searxng_resp

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class MockSession:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, *args, **kwargs):
                return MockResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch.object(database, "get_web_search", return_value=None), \
             patch.object(database, "reserve_web_search", return_value=True), \
             patch.object(database, "save_web_search"), \
             patch("aiohttp.ClientSession", MockSession):

            res = await search_web("iPhone 16 Pro", mode="kz_shops", limit=5)
            self.assertTrue(res["ok"])
            self.assertEqual(res["total"], 3)
            # Официальные магазины (DNS и Kaspi) должны идти первыми
            first_two_shops = [r["shop_name"] for r in res["results"][:2]]
            self.assertIn("DNS Казахстан", first_two_shops)
            self.assertIn("Kaspi Магазин", first_two_shops)
            # OLX должен идти после розничных магазинов и иметь флаг is_classifieds
            last_item = res["results"][2]
            self.assertEqual(last_item["shop_name"], "OLX Казахстан")
            self.assertTrue(last_item["is_classifieds"])


if __name__ == "__main__":
    unittest.main()
