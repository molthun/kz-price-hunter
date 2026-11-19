"""Контроль различия городов для источников без маркера города в ответе (политика владельца 19.11.2026).

Kaspi, Magnum и JSON-распродажа Sulpak: город только запрошен. Второй город обходится, только если
контрольная категория в двух городах различается; иначе он пропускается, а сверка ничего не снимает.
"""
from tests import test_support  # noqa: F401
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import source_failure
from scrapers.base import CityControl, PagedScraper, ScanResult
from scrapers.kaspi import KaspiScraper
from scrapers.magnum import MagnumScraper
from scrapers.sulpak import SulpakScraper

SAME = {"a": 100, "b": 200, "c": 300}


class CityControlVerdictTest(unittest.TestCase):
    def setUp(self):
        CityControl.reset()
        self.addCleanup(CityControl.reset)

    def _check(self, snaps, shop="S"):
        return CityControl.check(shop, "A", "B", lambda city: snaps[city])

    def test_different_items_or_prices_pass(self):
        self.assertTrue(self._check({"A": SAME, "B": {**SAME, "d": 1}})[0])
        CityControl.reset()
        self.assertTrue(self._check({"A": SAME, "B": {**SAME, "a": 101}})[0])

    def test_identical_small_or_failed_responses_do_not_pass(self):
        self.assertFalse(self._check({"A": SAME, "B": dict(SAME)})[0])
        CityControl.reset()
        self.assertFalse(self._check({"A": {"a": 1}, "B": {"b": 2}})[0])
        CityControl.reset()

        def boom(city):
            raise RuntimeError("HTTP 403")
        ok, reason = CityControl.check("S", "A", "B", boom)
        self.assertFalse(ok)
        self.assertIn("RuntimeError", reason)

    def test_verdict_is_cached_per_shop_and_city(self):
        calls = []

        def snap(city):
            calls.append(city)
            return SAME if city == "A" else {**SAME, "z": 9}
        for _ in range(3):
            self.assertTrue(CityControl.check("S", "A", "B", snap)[0])
        self.assertEqual(calls, ["A", "B"])
        CityControl.check("T", "A", "B", snap)
        self.assertEqual(len(calls), 4)

    def test_skipped_result_is_incomplete_with_reason(self):
        res = CityControl.skipped("контроль города не пройден")
        self.assertFalse(res.complete)
        self.assertEqual(list(res), [])
        self.assertEqual(source_failure.result_failure(res)["error_code"], "city_unconfirmed")


class SourceIntegrationTest(unittest.TestCase):
    """Второй город пропускается целиком; результат неполный, первый город сохранён."""

    def setUp(self):
        CityControl.reset()
        self.addCleanup(CityControl.reset)

    def _assert_second_skipped(self, res, first_city, second_city):
        self.assertFalse(res.complete)
        self.assertEqual({x["city"] for x in res}, {first_city})
        self.assertIn(f"{second_city}: контроль города не пройден", res.error)
        self.assertEqual(source_failure.result_failure(res)["error_code"], "city_unconfirmed")

    def test_kaspi_identical_control_skips_second_city(self):
        crawled = []

        def fake_paged(self_, name, url, max_pages=None):
            crawled.append(self_.city_name)
            return ScanResult([{"id": "kaspi_1", "price": 1, "city": self_.city_name}], complete=True)
        with patch.object(KaspiScraper, "_city_control_snapshot", return_value=dict(SAME)), \
             patch.object(PagedScraper, "_scrape_sync", fake_paged):
            res = KaspiScraper()._scrape_sync("C", "https://kaspi.kz/shop/c/headphones/")
        self.assertEqual(crawled, ["Астана"])
        self._assert_second_skipped(res, "Астана", "Алматы")

    def test_kaspi_differing_control_crawls_both(self):
        snaps = {"Астана": SAME, "Алматы": {**SAME, "a": 150}}

        def fake_paged(self_, name, url, max_pages=None):
            return ScanResult([{"id": "kaspi_1", "price": 1, "city": self_.city_name}], complete=True)
        with patch.object(KaspiScraper, "_city_control_snapshot", side_effect=lambda c: snaps[c]), \
             patch.object(PagedScraper, "_scrape_sync", fake_paged):
            res = KaspiScraper()._scrape_sync("C", "https://kaspi.kz/shop/c/headphones/")
        self.assertTrue(res.complete)
        self.assertEqual({x["city"] for x in res}, {"Астана", "Алматы"})

    def test_kaspi_control_snapshot_uses_requested_city(self):
        seen = []

        def fake_get(url, params=None, headers=None, **kw):
            seen.append((params or {}).get("c"))
            return SimpleNamespace(status_code=200, json=lambda: {"data": []})
        with patch("scrapers.kaspi.requests.get", side_effect=fake_get):
            KaspiScraper()._city_control_snapshot("Алматы")
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"750000000"})

    def test_magnum_identical_control_skips_second_city(self):
        crawled = []

        def fake_city(self_, name, url, city):
            crawled.append(city)
            return ScanResult([{"id": "magnum_1", "price": 1, "city": city}], complete=True)
        with patch.object(MagnumScraper, "_city_control_snapshot", return_value=dict(SAME)), \
             patch.object(MagnumScraper, "_scrape_city", fake_city):
            res = MagnumScraper()._scrape_sync("C", "https://magnum.kz/catalog?category=milk")
        self.assertEqual(crawled, ["Алматы"])
        self._assert_second_skipped(res, "Алматы", "Астана")

    def test_magnum_resets_session_between_cities(self):
        sessions = []

        def fake_city(self_, name, url, city):
            sessions.append(self_._get_session())
            return ScanResult([{"id": "magnum_1", "price": 1, "city": city}], complete=True)
        with patch.object(CityControl, "check", return_value=(True, "ok")), \
             patch.object(MagnumScraper, "_scrape_city", fake_city), \
             patch("scrapers.magnum.requests.Session", side_effect=lambda **kw: MagicMock()):
            MagnumScraper()._scrape_sync("C", "https://magnum.kz/catalog?category=milk")
        self.assertEqual(len(sessions), 2)
        self.assertIsNot(sessions[0], sessions[1])

    def test_sulpak_sale_identical_control_skips_second_city(self):
        crawled = []

        def fake_paged(self_, name, url, max_pages=None):
            crawled.append(self_._active_city)
            return ScanResult([{"id": "sulpak_1", "price": 1, "city": self_._active_city}], complete=True)
        with patch.object(SulpakScraper, "_city_control_snapshot", return_value=dict(SAME)), \
             patch.object(PagedScraper, "_scrape_sync", fake_paged):
            res = SulpakScraper()._scrape_sync("C", "https://www.sulpak.kz/sale/1")
        self.assertEqual(crawled, ["Алматы"])
        self._assert_second_skipped(res, "Алматы", "Астана")

    def test_sulpak_html_catalog_does_not_need_control(self):
        """HTML-каталог Sulpak подтверждает город сам (data-city-id), контроль не нужен."""
        def fake_paged(self_, name, url, max_pages=None):
            return ScanResult([{"id": "sulpak_1", "price": 1, "city": self_._active_city}], complete=True)
        with patch.object(SulpakScraper, "_city_control_snapshot", side_effect=AssertionError("no control")), \
             patch.object(PagedScraper, "_scrape_sync", fake_paged):
            res = SulpakScraper()._scrape_sync("C", "https://www.sulpak.kz/f/smartfoniy")
        self.assertTrue(res.complete)
        self.assertEqual({x["city"] for x in res}, {"Алматы", "Астана"})

    def test_skipped_city_blocks_retirement(self):
        import data_quality
        with patch.object(KaspiScraper, "_city_control_snapshot", return_value=dict(SAME)), \
             patch.object(PagedScraper, "_scrape_sync",
                          lambda s, n, u, max_pages=None: ScanResult(
                              [{"id": "kaspi_1", "price": 1, "city": s.city_name}], complete=True)):
            res = KaspiScraper()._scrape_sync("C", "https://kaspi.kz/shop/c/headphones/")
        assessment = data_quality.assess(data_quality.measure(res), complete=res.complete,
                                         error=res.error, baseline={"valid": 2})
        self.assertFalse(assessment["may_retire"])


if __name__ == "__main__":
    unittest.main()
