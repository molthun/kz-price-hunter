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
from scrapers.shopkz import ShopKzScraper
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


if __name__ == "__main__":
    unittest.main()
