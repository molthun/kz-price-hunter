"""Регрессии приёмки 6.41.3 (R1-A01..A03): подтверждение города, неразобранные карточки,
структурированная причина сбоя при объединении городов. Только синтетические ответы."""
from tests import test_support  # noqa: F401
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import data_quality
import database
import offer_identity
import source_failure
from scrapers import http
from scrapers.base import PagedScraper, ScanResult, merge_city_results
from scrapers.sulpak import SulpakScraper
from scrapers.technodom import TechnodomScraper
from scrapers.twelve_months import TwelveMonthsScraper
from tests.scrapers.test_regional_pricing import _aspro_page_html, _technodom_html

URL = "https://12.kz/catalog/radiatory/"
REGION_MARK = '<span class="regions__name">{}</span>'
BROKEN_CARD = ('<div class="catalog-block__item"><div data-item=\'{"ID":573274,"NAME":"Second radiator",'
               '"DETAIL_PAGE_URL":"/catalog/radiator-0575074/"}\'></div><span class="new-cost">35000</span></div>')
SECOND_CARD = ('<div class="catalog-block__item"><div data-item=\'{"ID":573274,"NAME":"Second radiator",'
               '"DETAIL_PAGE_URL":"/catalog/radiator-0575074/"}\'></div><span class="price__new-val">35 000</span></div>')
OUT_OF_STOCK_CARD = ('<div class="catalog-block__item"><div data-item=\'{"ID":573275,"NAME":"Third radiator",'
                     '"DETAIL_PAGE_URL":"/catalog/radiator-0575075/"}\'></div><span>Нет в наличии</span></div>')


def _twelve(pages_by_region, cities):
    """pages_by_region: cookie current_region -> HTML."""
    def get(url, cookies=None, **kw):
        return SimpleNamespace(status_code=200, text=pages_by_region[(cookies or {}).get("current_region")])
    session = MagicMock()
    session.get.side_effect = get
    with patch.object(TwelveMonthsScraper, "_get_session", return_value=session), patch("time.sleep"):
        return TwelveMonthsScraper(cities=cities)._scrape_sync("C", URL, 1)


def _retired_after(result, existing_ids):
    """Настоящая сверка снятия на временной БД; зеркалирование и watch-refresh подменены."""
    offer_identity.assign_offer_ids(result)
    assessment = data_quality.assess(data_quality.measure(result), complete=result.complete,
                                     error=result.error, baseline={"valid": len(existing_ids)})
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""CREATE TABLE products(id TEXT PRIMARY KEY,is_active INTEGER);
            CREATE TABLE product_sources(product_id TEXT,shop_key TEXT,source_url TEXT,active INTEGER,
            UNIQUE(product_id,shop_key,source_url));""")
        for pid in existing_ids:
            conn.execute("INSERT INTO products VALUES (?,1)", (pid,))
            conn.execute("INSERT INTO product_sources VALUES (?,?,?,1)", (pid, "twelve_months", URL))
        with patch.object(database, "_retire_mirrored_offers_conn"), \
             patch.object(database, "_queue_watch_refresh_conn"):
            retired = database._reconcile_source_conn(conn, "twelve_months", URL,
                                                      [x["id"] for x in result], assessment["may_retire"])
        active = {r[0] for r in conn.execute("SELECT id FROM products WHERE is_active=1")}
        return retired, active, assessment
    finally:
        conn.close()


class TwelveMonthsRegionConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.almaty = _aspro_page_html("Алматы", "27 300 теңге/шт")

    def _assert_rejected(self, res):
        self.assertFalse(res.complete)
        self.assertTrue(res.error)
        self.assertEqual(list(res), [])
        self.assertEqual(source_failure.result_failure(res)["error_code"], "parse")

    def test_missing_region_marker_records_no_price(self):
        html = self.almaty.replace(REGION_MARK.format("Алматы"), "")
        self._assert_rejected(_twelve({"471565": html}, ("Астана",)))

    def test_empty_region_marker_records_no_price(self):
        html = self.almaty.replace(REGION_MARK.format("Алматы"), REGION_MARK.format("  "))
        self._assert_rejected(_twelve({"471565": html}, ("Астана",)))

    def test_mismatched_region_records_no_price(self):
        self._assert_rejected(_twelve({"471565": self.almaty}, ("Астана",)))

    def test_second_city_unconfirmed_after_first_succeeds(self):
        astana_missing = _aspro_page_html("Астана", "35 000").replace(REGION_MARK.format("Астана"), "")
        res = _twelve({"471567": self.almaty, "471565": astana_missing}, ("Алматы", "Астана"))
        self.assertFalse(res.complete)
        self.assertEqual({x["city"] for x in res}, {"Алматы"})
        self.assertIn("Астана:", res.error)

    def test_confirmed_region_is_accepted(self):
        res = _twelve({"471565": _aspro_page_html("Астана", "35 000")}, ("Астана",))
        self.assertTrue(res.complete)
        self.assertEqual([(x["city"], x["price"]) for x in res], [("Астана", 35000)])


class TwelveMonthsUnparsedCardTest(unittest.TestCase):
    IDS = ("twelve_months_573273@almaty", "twelve_months_573274@almaty")

    def test_broken_card_blocks_complete_and_retirement(self):
        html = _aspro_page_html("Алматы", "27 300").replace("</body>", BROKEN_CARD + "</body>")
        res = _twelve({"471567": html}, ("Алматы",))
        self.assertEqual(len(res), 1)
        self.assertEqual(res.unparsed, 1)
        self.assertFalse(res.complete)
        retired, active, assessment = _retired_after(res, self.IDS)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertEqual(active, set(self.IDS))

    def test_broken_card_in_one_of_two_cities_blocks_retirement(self):
        ok = _aspro_page_html("Алматы", "27 300").replace("</body>", SECOND_CARD + "</body>")
        broken = _aspro_page_html("Астана", "35 000").replace("</body>", BROKEN_CARD + "</body>")
        res = _twelve({"471567": ok, "471565": broken}, ("Алматы", "Астана"))
        self.assertEqual(res.unparsed, 1)
        self.assertFalse(res.complete)
        ids = self.IDS + ("twelve_months_573273@astana", "twelve_months_573274@astana")
        retired, active, _ = _retired_after(res, ids)
        self.assertEqual(retired, [])
        self.assertEqual(active, set(ids))

    def test_out_of_stock_card_is_not_a_parse_error(self):
        html = _aspro_page_html("Алматы", "27 300").replace("</body>", OUT_OF_STOCK_CARD + "</body>")
        res = _twelve({"471567": html}, ("Алматы",))
        self.assertEqual(res.unparsed, 0)
        self.assertTrue(res.complete)
        self.assertEqual(len(res), 1)


class TechnodomSulpakCityConfirmationTest(unittest.TestCase):
    ITEMS = [{"sku": "302075", "title": "Смартфон Тест", "price": 189990, "uri": "smartfon-test"}]

    def _technodom(self, slug_in_response):
        html = _technodom_html(self.ITEMS, slug_in_response)
        with patch("scrapers.technodom.requests.get", return_value=SimpleNamespace(status_code=200, text=html)), \
             patch("time.sleep"):
            return TechnodomScraper(cities=("Астана",))._scrape_sync(
                "C", "https://www.technodom.kz/astana/catalog/smartfony", 1)

    def test_technodom_requires_confirmed_city(self):
        for slug in (None, "", "almaty"):
            with self.subTest(slug=slug):
                res = self._technodom(slug)
                self.assertEqual(list(res), [])
                self.assertFalse(res.complete)
                self.assertEqual(source_failure.result_failure(res)["error_code"], "parse")
        self.assertEqual([x["city"] for x in self._technodom("astana")], ["Астана"])

    def _sulpak(self, marker):
        html = f"""{marker}
            <div class="product__item" data-code="888" data-name="Смартфон" data-price="150000">
              <a href="/g/phone-888">Смартфон</a></div>
            <div id="paginator" data-pagescount="1"></div>"""
        with patch("scrapers.sulpak.requests.get", return_value=SimpleNamespace(status_code=200, text=html)), \
             patch("time.sleep"):
            return SulpakScraper(cities=("Астана",))._scrape_sync("C", "https://www.sulpak.kz/f/smartfoniy", 1)

    def test_sulpak_requires_confirmed_city(self):
        for marker in ("", '<b data-city-id=""></b>', '<b data-city-id="1"></b>',
                       '<b data-city-id="3"></b><i data-city-id="1"></i>'):
            with self.subTest(marker=marker):
                res = self._sulpak(marker)
                self.assertEqual(list(res), [])
                self.assertFalse(res.complete)
        res = self._sulpak('<button class="subscribe-btn" data-city-id="3"></button>')
        self.assertTrue(res.complete)
        self.assertEqual([x["city"] for x in res], ["Астана"])


class MergeCityResultsTest(unittest.TestCase):
    def test_preserves_failure_metadata_unparsed_and_limited(self):
        failure = source_failure.make("http_403", phase="fetch", page=2, http_status=403)
        res = merge_city_results([
            ("Астана", ScanResult([{"id": "x"}], error="Страница 2: RuntimeError", error_info=failure, unparsed=2)),
            ("Алматы", ScanResult([{"id": "y"}], limited=True, unparsed=1)),
        ])
        self.assertEqual(res.error_info, failure)
        self.assertEqual(source_failure.result_failure(res), failure)
        self.assertEqual(res.unparsed, 3)
        self.assertTrue(res.limited)
        self.assertFalse(res.complete)
        self.assertEqual(res.error, "Астана: Страница 2: RuntimeError")

    def test_all_complete_cities_merge_complete(self):
        res = merge_city_results([("A", ScanResult([{"id": "x"}], complete=True)),
                                  ("B", ScanResult([{"id": "y"}], complete=True))])
        self.assertTrue(res.complete)
        self.assertIsNone(res.error_info)
        self.assertEqual(res.unparsed, 0)

    def test_wrapper_keeps_failure_from_paged_scraper(self):
        failure = source_failure.make("http_403", phase="fetch", page=2, http_status=403)
        with patch.object(PagedScraper, "_scrape_sync", side_effect=[
            ScanResult([{"id": "x"}], error="Страница 2: RuntimeError", error_info=failure),
            ScanResult([{"id": "y"}], complete=True),
        ]):
            res = TechnodomScraper()._scrape_sync("C", "https://www.technodom.kz/astana/catalog/test")
        self.assertEqual(res.error_info, failure)

    def test_real_fetch_chain_keeps_failure_reason(self):
        """fetch -> PagedScraper -> обёртка городов -> диагностика категории; Алматы успешна."""
        def astana_403(url):
            source_failure.observe_http(403)
            return SimpleNamespace(status_code=403, text="")

        def astana_429(url):
            source_failure.observe_http(429)
            return SimpleNamespace(status_code=429, text="")

        def astana_timeout(url):
            raise http.exceptions.Timeout("simulated")

        def astana_cooldown(url):
            raise http.HostCooldown("www.technodom.kz", 30)

        cases = (("http_403", astana_403, 403), ("http_429", astana_429, 429),
                 ("timeout", astana_timeout, None), ("cooldown", astana_cooldown, None))
        for code, astana, status in cases:
            with self.subTest(code=code):
                def fake_get(url, **kw):
                    if "/astana/" in url:
                        return astana(url)
                    source_failure.observe_http(200)
                    items = [] if "page=2" in url else TechnodomSulpakCityConfirmationTest.ITEMS
                    return SimpleNamespace(status_code=200, text=_technodom_html(items, "almaty"))

                with patch("scrapers.technodom.requests.get", side_effect=fake_get), patch("time.sleep"):
                    with source_failure.capture() as observed:
                        res = TechnodomScraper()._scrape_sync(
                            "C", "https://www.technodom.kz/astana/catalog/test", 2)
                self.assertFalse(res.complete)
                self.assertEqual({x["city"] for x in res}, {"Алматы"})
                info = source_failure.result_failure(res, observed)
                self.assertEqual(info["error_code"], code)
                self.assertEqual(info["page"], 1)
                if status:
                    self.assertEqual(info["http_status"], status)


if __name__ == "__main__":
    unittest.main()
