from tests import test_support  # noqa: F401
"""Тесты региональной политики источников и мультигородского обхода (R1-01, v6.41.0)."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config
import data_quality
import offer_identity
from scrapers import http as scraper_http
from scrapers.kaspi import KaspiScraper
from scrapers.magnum import MagnumScraper
from scrapers.mechta import MechtaScraper
from scrapers.shopkz import ShopKzScraper
from scrapers.sulpak import SulpakScraper
from scrapers.technodom import TechnodomScraper
from scrapers.twelve_months import TwelveMonthsScraper


def _aspro_page_html(city_label: str, price_text: str, old_price_text: str = "") -> str:
    item_json = json.dumps({
        "IBLOCK_ID": 34,
        "ID": 573273,
        "NAME": "Радиатор алюминиевый UNO Алюрад 500/100",
        "DETAIL_PAGE_URL": "/catalog/vodosnabzhenie-otoplenie-i-ventilyatsiya/radiatory/radiator-0575073/",
    }, ensure_ascii=False)
    old_block = f'<span class="price__old-val">{old_price_text}</span>' if old_price_text else ""
    return f"""
    <html>
      <body>
        <span class="regions__name">{city_label}</span>
        <div class="catalog-block__item">
          <div data-item='{item_json}'></div>
          <div class="catalog-block__info-title">
            <a class="dark_link" href="/catalog/vodosnabzhenie-otoplenie-i-ventilyatsiya/radiatory/radiator-0575073/">
              <span class="js-gt-name-ru">Радиатор алюминиевый UNO Алюрад 500/100</span>
            </a>
          </div>
          <span class="price__new-val">{price_text}</span>
          {old_block}
          <img class="js-replace-img" data-src="/upload/rad.jpg" />
        </div>
      </body>
    </html>
    """


def _technodom_html(items, city_slug=None):
    state = {"productList": {"items": items}}
    if city_slug is not None:
        state["core"] = {"currentCity": {"slug": city_slug, "city_code": "KZ"}}
    return f'<script id="__NEXT_DATA__">{json.dumps({"props": {"pageProps": {"initialState": state}}})}</script>'


class RegionalPolicyRegistryTest(unittest.TestCase):
    def test_every_active_shop_has_valid_regional_policy(self):
        self.assertEqual(set(config.SHOP_REGIONAL_POLICY), set(config.SHOP_KEYS))
        for key in config.SHOP_KEYS:
            with self.subTest(shop_key=key):
                policy = config.get_shop_regional_policy(key)
                self.assertIn(policy["mode"], config.REGIONAL_MODES)
                self.assertTrue(policy["scan_cities"])
                self.assertTrue(policy["target_cities"])
                self.assertIsInstance(policy["delivery_kz"], bool)
                self.assertTrue(policy["city_mechanism"])

    def test_almaty_kz_and_astana_kz_map_to_nationwide_slug(self):
        self.assertEqual(offer_identity.location_slug("Алматы / Казахстан"), "kz")
        self.assertEqual(offer_identity.location_slug("Астана / Казахстан"), "kz")
        self.assertEqual(offer_identity.location_slug("Алматы"), "almaty")
        self.assertEqual(offer_identity.location_slug("Астана"), "astana")


class TwelveMonthsMultiCityTest(unittest.TestCase):
    def test_multi_city_scrape_returns_distinct_almaty_and_astana_offers(self):
        scraper = TwelveMonthsScraper(cities=("Алматы", "Астана"))
        seen_cookies = []

        def fake_get(url, **kwargs):
            cid = (kwargs.get("cookies") or {}).get("current_region")
            seen_cookies.append(cid)
            if cid == TwelveMonthsScraper.REGION_IDS["Алматы"]:
                return SimpleNamespace(status_code=200, text=_aspro_page_html("Алматы", "27 300 теңге/шт", "30 000 теңге"))
            if cid == TwelveMonthsScraper.REGION_IDS["Астана"]:
                return SimpleNamespace(status_code=200, text=_aspro_page_html("Астана", "35 000 теңге/шт"))
            raise AssertionError(f"unexpected region cookie: {cid}")

        fake_session = MagicMock()
        fake_session.get.side_effect = fake_get

        with patch.object(TwelveMonthsScraper, "_get_session", return_value=fake_session):
            res = scraper._scrape_sync("Отопление", "https://12.kz/catalog/vodosnabzhenie-otoplenie-i-ventilyatsiya/", max_pages=1)

        self.assertTrue(res.complete)
        self.assertIsNone(res.error)
        self.assertEqual(seen_cookies, ["471567", "471565"])
        self.assertEqual(len(res), 2)

        offer_identity.assign_offer_ids(res)
        deduped = data_quality.dedupe(res)
        self.assertEqual(len(deduped), 2)
        by_id = {item["id"]: item for item in deduped}
        self.assertEqual(by_id["twelve_months_573273@almaty"]["price"], 27300)
        self.assertEqual(by_id["twelve_months_573273@almaty"]["old_price_on_site"], 30000)
        self.assertEqual(by_id["twelve_months_573273@astana"]["price"], 35000)
        self.assertEqual(by_id["twelve_months_573273@almaty"]["sku"], "0575073")

    def test_region_mismatch_rejected_and_prevents_complete_reconcile(self):
        """Если сайт отдал Алматы при запросе Астаны, чужая цена не записывается и complete=False."""
        scraper = TwelveMonthsScraper(cities=("Алматы", "Астана"))

        def fake_get(url, **kwargs):
            # Оба раза сайт ошибочно возвращает Алматы
            return SimpleNamespace(status_code=200, text=_aspro_page_html("Алматы", "27 300 теңге/шт"))

        fake_session = MagicMock()
        fake_session.get.side_effect = fake_get

        with patch.object(TwelveMonthsScraper, "_get_session", return_value=fake_session), \
             patch("time.sleep"):
            res = scraper._scrape_sync("Отопление", "https://12.kz/catalog/vodosnabzhenie-otoplenie-i-ventilyatsiya/", max_pages=1)

        self.assertFalse(res.complete, "при ошибке города complete обязан быть False, чтобы не снять другой город")
        self.assertIn("Астана", res.error or "")
        # Только подтверждённая Алматы попала в результат, Астана не записана с алматинской ценой
        self.assertEqual([item["city"] for item in res], ["Алматы"])

    def test_retries_on_522_and_does_not_retry_host_cooldown(self):
        scraper = TwelveMonthsScraper(cities=("Алматы",))
        session = MagicMock()
        session.get.side_effect = [
            SimpleNamespace(status_code=522, text="522"),
            SimpleNamespace(status_code=200, text=_aspro_page_html("Алматы", "27 300 теңге/шт")),
        ]
        scraper.session = session
        with patch("time.sleep") as mock_sleep:
            res = scraper._fetch_page("Отопление", "https://12.kz/catalog/radiatory/", 1, city="Алматы")
        self.assertEqual(len(res), 1)
        self.assertEqual(session.get.call_count, 2)
        mock_sleep.assert_called_once_with(TwelveMonthsScraper.RETRY_DELAY_SECONDS)

        session_cd = MagicMock()
        session_cd.get.side_effect = scraper_http.HostCooldown("12.kz", 60)
        scraper.session = session_cd
        with patch("time.sleep") as mock_sleep_cd:
            with self.assertRaises(scraper_http.HostCooldown):
                scraper._fetch_page("Отопление", "https://12.kz/catalog/radiatory/", 1, city="Алматы")
        self.assertEqual(session_cd.get.call_count, 1)
        mock_sleep_cd.assert_not_called()


class ShopKzNationwideYmlTest(unittest.TestCase):
    def test_shopkz_yml_marks_offers_nationwide_kz(self):
        yml_bytes = b"""<?xml version="1.0" encoding="utf-8"?>
        <yml_catalog><shop>
          <categories><category id="1">Laptops</category></categories>
          <offers>
            <offer id="99911" available="true">
              <name>Laptop Test 16GB</name>
              <price>350000</price>
              <url>https://shop.kz/offer/laptop-99911/</url>
              <categoryId>1</categoryId>
            </offer>
          </offers>
        </shop></yml_catalog>"""

        class FakeResp:
            status_code = 200
            def iter_content(self, chunk_size=65536):
                yield yml_bytes
            def close(self):
                pass

        class FakeSess:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def get(self, *a, **kw):
                return FakeResp()

        with patch("scrapers.shopkz.requests.Session", return_value=FakeSess()):
            items = ShopKzScraper().scrape_yml("https://shop.kz/bitrix/catalog_export/yandex.php")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["city"], "Астана / Казахстан")
        offer_identity.assign_offer_ids(items)
        self.assertEqual(items[0]["id"], "shopkz_99911@kz")


class KaspiAndMagnumMultiCityTest(unittest.TestCase):
    def setUp(self):
        # Механику обхода проверяем при пройденном контроле города; сам контроль — в test_city_control.py
        from scrapers.base import CityControl
        patcher = patch.object(CityControl, "check", return_value=(True, "контроль города пройден"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_kaspi_default_scans_astana_and_almaty_separately(self):
        scraper = KaspiScraper()
        self.assertEqual(scraper.cities, ("Астана", "Алматы"))
        seen_cities = []

        def fake_get(url, params=None, headers=None, **kw):
            code = (params or {}).get("c")
            seen_cities.append((code, (headers or {}).get("X-KS-City")))
            page = (params or {}).get("page", 0)
            if page > 0:
                return SimpleNamespace(status_code=200, json=lambda: {"data": []})
            price = 520000 if code == "710000000" else 495000
            return SimpleNamespace(
                status_code=200,
                json=lambda p=price: {
                    "data": [{
                        "id": "1001",
                        "title": "Apple iPhone 16 128GB",
                        "unitSalePrice": p,
                        "unitPrice": p,
                        "shopLink": "/p/iphone-16-1001/",
                        "stock": 5,
                    }]
                },
            )

        with patch("scrapers.kaspi.requests.get", side_effect=fake_get), patch("time.sleep"):
            res = scraper._scrape_sync("Смартфоны", "https://kaspi.kz/shop/c/smartphones/", max_pages=2)

        self.assertTrue(res.complete)
        self.assertIsNone(res.error)
        self.assertIn(("710000000", "710000000"), seen_cities)
        self.assertIn(("750000000", "750000000"), seen_cities)
        offer_identity.assign_offer_ids(res)
        by_id = {x["id"]: x for x in data_quality.dedupe(res)}
        self.assertEqual(by_id["kaspi_1001@astana"]["price"], 520000)
        self.assertEqual(by_id["kaspi_1001@almaty"]["price"], 495000)

    def test_kaspi_explicit_city_code_stays_single_city(self):
        scraper = KaspiScraper(city_code="750000000")
        self.assertEqual(scraper.cities, ("Алматы",))
        self.assertEqual(scraper.city_name, "Алматы")

    def test_magnum_default_scans_almaty_and_astana_and_protects_complete_on_partial_error(self):
        scraper = MagnumScraper()
        self.assertEqual(scraper.cities, ("Алматы", "Астана"))
        seen_urls = []

        def fake_get(url, **kw):
            seen_urls.append(url)
            if "city=almaty" in url:
                return SimpleNamespace(status_code=200, json=lambda: [{"id": 77, "name": "Молоко 1л", "final_price": 550, "start_price": 650}])
            if "city=astana" in url:
                return SimpleNamespace(status_code=200, json=lambda: [{"id": 77, "name": "Молоко 1л", "final_price": 590, "start_price": 690}])
            raise AssertionError(url)

        fake_session = MagicMock()
        fake_session.get.side_effect = fake_get
        # Сессия сбрасывается между городами, поэтому подменяется её фабрика
        session_patch = patch.object(MagnumScraper, "_get_session", return_value=fake_session)
        session_patch.start()
        self.addCleanup(session_patch.stop)
        res = scraper._scrape_sync("Молочка", "https://magnum.kz/catalog?category=milk")
        self.assertTrue(res.complete)
        self.assertIsNone(res.error)
        offer_identity.assign_offer_ids(res)
        by_id = {x["id"]: x for x in data_quality.dedupe(res)}
        self.assertEqual(by_id["magnum_77@almaty"]["price"], 550)
        self.assertEqual(by_id["magnum_77@astana"]["price"], 590)

        # Частичный сбой Астаны → общий complete=False
        def fake_get_fail(url, **kw):
            if "city=almaty" in url:
                return SimpleNamespace(status_code=200, json=lambda: [{"id": 77, "name": "Молоко 1л", "final_price": 550, "start_price": 650}])
            return SimpleNamespace(status_code=503, json=lambda: [])

        fake_session.get.side_effect = fake_get_fail
        with patch("time.sleep"):
            res_partial = scraper._scrape_sync("Молочка", "https://magnum.kz/catalog?category=milk")
        self.assertFalse(res_partial.complete)
        self.assertIn("Астана", res_partial.error or "")
        self.assertEqual([x["city"] for x in res_partial], ["Алматы"])


class TechnodomSulpakMechtaMultiCityTest(unittest.TestCase):
    def test_technodom_scans_astana_and_almaty_with_slug_urls(self):
        scraper = TechnodomScraper()
        seen = []

        def fake_get(url, cookies=None, **kw):
            seen.append((url, (cookies or {}).get("city")))
            if "page=2" in url:
                items = []
            else:
                price = 199990 if "/astana/" in url else 189990
                items = [{"sku": "302075", "title": "Смартфон Тест", "price": price, "uri": "smartfon-test"}]
            html = _technodom_html(items, (cookies or {}).get("city"))
            return SimpleNamespace(status_code=200, text=html)

        with patch("scrapers.technodom.requests.get", side_effect=fake_get), patch("time.sleep"):
            res = scraper._scrape_sync("Смартфоны", "https://www.technodom.kz/astana/catalog/smartfony", max_pages=2)

        self.assertTrue(res.complete)
        self.assertTrue(any("/almaty/catalog/smartfony" in u and c == "almaty" for u, c in seen))
        self.assertTrue(any("/astana/catalog/smartfony" in u and c == "astana" for u, c in seen))
        offer_identity.assign_offer_ids(res)
        by_id = {x["id"]: x for x in data_quality.dedupe(res)}
        self.assertEqual(by_id["td_302075@astana"]["price"], 199990)
        self.assertEqual(by_id["td_302075@almaty"]["price"], 189990)
        self.assertIn("/almaty/p/smartfon-test", by_id["td_302075@almaty"]["url"])

    def test_sulpak_uses_verified_city_ids_1_almaty_and_3_astana(self):
        self.assertEqual(config.CITIES_KZ["almaty"]["sulpak_id"], 1)
        self.assertEqual(config.CITIES_KZ["astana"]["sulpak_id"], 3)
        scraper = SulpakScraper()
        seen_cids = []

        def fake_get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            seen_cids.append(cid)
            price = "150 000 ₸" if cid == "1" else "165 000 ₸"
            html = f"""
            <button class="subscribe-btn" data-city-id="{cid}"></button>
            <div class="product__item product__item-js" data-code="888">
              <div class="product__item-name"><a href="/g/phone-888">Смартфон Sulpak 888</a></div>
              <div class="product__item-price">{price}</div>
            </div>
            <div id="paginator" data-pagescount="1"></div>
            """
            return SimpleNamespace(status_code=200, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=fake_get), patch("time.sleep"):
            res = scraper._scrape_sync("Смартфоны", "https://www.sulpak.kz/f/smartfoniy", max_pages=1)

        self.assertTrue(res.complete)
        self.assertEqual(seen_cids, ["1", "3"])
        offer_identity.assign_offer_ids(res)
        by_id = {x["id"]: x for x in data_quality.dedupe(res)}
        self.assertEqual(by_id["sulpak_888@almaty"]["price"], 150000)
        self.assertEqual(by_id["sulpak_888@astana"]["price"], 165000)

    def test_mechta_default_is_single_city_until_confirmed(self):
        self.assertEqual(MechtaScraper().cities, ("Астана",))
        self.assertEqual(config.SHOP_REGIONAL_POLICY["mechta"]["scan_cities"], ("Астана",))

    def test_mechta_scans_astana_and_almaty_headers(self):
        scraper = MechtaScraper(cities=("Астана", "Алматы"))
        seen_codes = []

        def fake_get(url, headers=None, params=None, **kw):
            code = (headers or {}).get("x-city-code")
            seen_codes.append(code)
            if (params or {}).get("page", 1) > 1:
                return SimpleNamespace(status_code=204)
            price = 210000 if code == "astana" else 199000
            return SimpleNamespace(
                status_code=200,
                json=lambda p=price: {
                    "products": [{"id": 505, "name": "Планшет Тест", "slug": "planshet-505", "prices": {"finalPrice": p, "basePrice": p}}]
                },
            )

        fake_session = MagicMock()
        fake_session.get.side_effect = fake_get
        scraper.session = fake_session
        with patch("time.sleep"):
            res = scraper._scrape_sync("Планшеты", "https://www.mechta.kz/section/planshety/", max_pages=2)

        self.assertTrue(res.complete)
        self.assertIn("astana", seen_codes)
        self.assertIn("almaty", seen_codes)
        offer_identity.assign_offer_ids(res)
        by_id = {x["id"]: x for x in data_quality.dedupe(res)}
        self.assertEqual(by_id["mechta_505@astana"]["price"], 210000)
        self.assertEqual(by_id["mechta_505@almaty"]["price"], 199000)


if __name__ == "__main__":
    unittest.main()
