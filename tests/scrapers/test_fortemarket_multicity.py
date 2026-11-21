"""Тесты мультигородского сбора Forte Market за один проход Algolia API (v6.42.7).
Только синтетические ответы."""
from tests import test_support  # noqa: F401
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import config
import data_quality
import offer_identity
from scrapers.fortemarket import ForteMarketScraper, resolve_offers_for_cities
from tests.scrapers.test_city_confirmation_r1a import _retired_after

CAT_URL = "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Смартфоны"


def _hit(obj_id, name, price=100000, locations=None, url=None):
    h = {"objectID": str(obj_id), "Name": name, "Price": price}
    if locations is not None:
        h["Locations"] = {"Location": locations}
    if url is not None:
        h["URL"] = url
    return h


class ForteMarketMultiCityTest(unittest.TestCase):
    def test_config_policy_declares_both_cities(self):
        policy = config.SHOP_REGIONAL_POLICY["fortemarket"]
        self.assertEqual(policy["mode"], "multi_city_price")
        self.assertEqual(policy["scan_cities"], ("Астана", "Алматы"))
        self.assertTrue(policy["delivery_kz"])

    def test_single_response_emits_distinct_prices_for_astana_and_almaty(self):
        hits = [
            _hit(
                "101",
                "Samsung Galaxy S25 Ultra",
                price=650000,
                locations=[
                    {"ID": "KZ-AST-01", "Price": 648998},
                    {"ID": "KZ-ALA-01", "Price": 555198},
                ],
            ),
            _hit(
                "102",
                "Только Алматы + KZ",
                price=200000,
                locations=[
                    {"ID": "KZ-ALA-01", "Price": 189000},
                    {"ID": "KZ", "Price": 195000},
                ],
            ),
            _hit(
                "103",
                "Только Казахстан",
                price=50000,
                locations=[{"ID": "KZ", "Price": 50000}],
            ),
        ]
        session = MagicMock()
        session.post.return_value = SimpleNamespace(
            status_code=201,
            json=lambda: {"hits": hits, "nbHits": 3},
        )
        with patch.object(ForteMarketScraper, "_get_session", return_value=session), patch("time.sleep"):
            res = ForteMarketScraper()._scrape_sync("Смартфоны", CAT_URL, max_pages=5)

        self.assertTrue(res.complete)
        self.assertFalse(res.limited)
        self.assertEqual(res.unparsed, 0)
        # Ровно один HTTP-запрос на оба города!
        self.assertEqual(session.post.call_count, 1)

        offer_identity.assign_offer_ids(res)
        deduped = data_quality.dedupe(res)
        self.assertEqual(len(deduped), 5)
        by_id = {p["id"]: (p["city"], p["price"]) for p in deduped}
        self.assertEqual(
            by_id,
            {
                "forte_101@astana": ("Астана", 648998),
                "forte_101@almaty": ("Алматы", 555198),
                "forte_102@kz": ("Казахстан", 195000),
                "forte_102@almaty": ("Алматы", 189000),
                "forte_103@kz": ("Казахстан", 50000),
            },
        )

    def test_duplicate_hit_within_page_is_deduplicated(self):
        h = _hit("201", "Дубль", price=10000, locations=[{"ID": "KZ-AST", "Price": 10000}, {"ID": "KZ-ALA", "Price": 9500}])
        res = ForteMarketScraper.parse_response({"hits": [h, h], "nbHits": 1}, "C", 1, cities=("Астана", "Алматы"))
        self.assertEqual(len(res), 2)
        self.assertEqual({p["city"] for p in res}, {"Астана", "Алматы"})

    def test_backward_compatibility_single_city_init_and_parse(self):
        s = ForteMarketScraper(city="Алматы")
        self.assertEqual(s.cities, ("Алматы",))
        self.assertEqual(s.city, "Алматы")
        h = _hit("301", "Товар", price=10000, locations=[{"ID": "KZ-AST", "Price": 11000}, {"ID": "KZ-ALA", "Price": 9000}])
        res = ForteMarketScraper.parse_response({"hits": [h], "nbHits": 1}, "C", 1, city="Алматы")
        self.assertEqual([(p["city"], p["price"]) for p in res], [("Алматы", 9000)])

    def test_unparsed_hits_block_complete_and_retirement(self):
        """R1-A02: сломанный hit (без id/имени/цены/не-dict/битый URL) учитывается в unparsed и запрещает снятие."""
        good = _hit("401", "Хороший товар", price=50000, locations=[{"ID": "KZ-AST", "Price": 50000}, {"ID": "KZ-ALA", "Price": 48000}])
        bad_cases = [
            ("non_dict", "not-a-dict"),
            ("no_id", {"Name": "Без ID", "Price": 10000}),
            ("no_name", {"objectID": "402", "Name": "   ", "Price": 10000}),
            ("zero_price", {"objectID": "403", "Name": "Нулевая цена", "Price": 0}),
            ("bad_url", _hit("404", "Битый URL", price=10000, url="not-a-url")),
        ]
        existing = ("forte_401@astana", "forte_401@almaty", "forte_999@astana")
        for label, bad_hit in bad_cases:
            with self.subTest(case=label):
                session = MagicMock()
                session.post.return_value = SimpleNamespace(
                    status_code=201,
                    json=lambda bh=bad_hit: {"hits": [good, bh], "nbHits": 2},
                )
                with patch.object(ForteMarketScraper, "_get_session", return_value=session), patch("time.sleep"):
                    res = ForteMarketScraper()._scrape_sync("Смартфоны", CAT_URL, max_pages=5)
                self.assertFalse(res.complete)
                self.assertTrue(res.limited)
                self.assertGreaterEqual(res.unparsed, 1)
                retired, active, assessment = _retired_after(res, existing, "fortemarket", CAT_URL)
                self.assertFalse(assessment["may_retire"])
                self.assertEqual(retired, [])
                self.assertEqual(active, set(existing))

    def test_legitimate_retirement_on_clean_complete_multicity_scan(self):
        """Положительный контроль: чистый полный обход (unparsed=0) снимает только действительно исчезнувший товар."""
        good = _hit("501", "Активный товар", price=50000, locations=[{"ID": "KZ-AST", "Price": 50000}, {"ID": "KZ-ALA", "Price": 48000}])
        session = MagicMock()
        session.post.return_value = SimpleNamespace(
            status_code=201,
            json=lambda: {"hits": [good], "nbHits": 1},
        )
        with patch.object(ForteMarketScraper, "_get_session", return_value=session), patch("time.sleep"):
            res = ForteMarketScraper()._scrape_sync("Смартфоны", CAT_URL, max_pages=5)
        self.assertTrue(res.complete)
        existing = ("forte_501@astana", "forte_501@almaty", "forte_999@astana")
        retired, active, assessment = _retired_after(res, existing, "fortemarket", CAT_URL)
        self.assertTrue(assessment["may_retire"])
        self.assertEqual(retired, ["forte_999@astana"])
        self.assertEqual(active, {"forte_501@astana", "forte_501@almaty"})

    def test_resolve_offers_for_cities_deduplicates_nationwide_fallback(self):
        h = _hit("601", "KZ only", price=12345, locations=[{"ID": "KZ", "Price": 12345}])
        self.assertEqual(resolve_offers_for_cities(h, ("Астана", "Алматы")), [(12345, "Казахстан")])


if __name__ == "__main__":
    unittest.main()
