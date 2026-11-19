from tests import test_support  # noqa: F401
"""Стабилизация Bitrix-адаптеров kimex.kz, french-house.kz, komfort.kz (v6.40.31).

Проверяет щадящую паузу между страницами, таймаут 30 с, повтор при Cloudflare 522 / 5xx и таймаутах,
а также немедленный выход без повтора при HostCooldown.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scrapers import http as scraper_http
from scrapers.french_house import FrenchHouseScraper
from scrapers.kimex import KimexScraper
from scrapers.komfort import KomfortScraper


KIMEX_OK_HTML = """
<div class="cataloge__cards">
  <a class="card" data-entity="item" data-id="101" href="/product/sneakers-101/">
    <div class="card__title">Кроссовки Rieker 101</div>
    <div class="price-current">35 990 ₸</div>
  </a>
</div>
"""

FRENCH_HOUSE_OK_HTML = """
<div class="goodCard cardType1">
  <a href="/catalog/item-202/">
    <span class="name">Dior Sauvage 100ml</span>
  </a>
  <button data-id="202"></button>
  <div class="cardCost new">62 000 ₸</div>
</div>
"""

KOMFORT_OK_HTML = """
<div class="catalog-block-view__item" data-id="303">
  <div class="item-title"><a href="/catalog/drill-303/">Дрель Bosch GSR 120</a></div>
  <div class="price" data-value="48500">48 500 ₸</div>
</div>
"""


class BitrixRetryStabilityTest(unittest.TestCase):
    CASES = (
        ("kimex", KimexScraper, "https://kimex.kz/catalog/zhenskoe/obuv/krossovki/", KIMEX_OK_HTML),
        ("french_house", FrenchHouseScraper, "https://french-house.kz/catalog/parfyumeriya/zhenskaya-parfyumeriya/", FRENCH_HOUSE_OK_HTML),
        ("komfort", KomfortScraper, "https://komfort.kz/catalog/instrumenty/elektroistrumenty/dreli_shurupoverty/", KOMFORT_OK_HTML),
    )

    def test_polite_pacing_and_522_in_retry_codes(self):
        for name, cls, _, _ in self.CASES:
            with self.subTest(shop=name):
                self.assertGreaterEqual(cls.PAGE_DELAY_SECONDS, 1.0)
                self.assertGreaterEqual(cls.REQUEST_TIMEOUT_SECONDS, 30)
                self.assertIn(522, cls.RETRY_STATUS_CODES)
                self.assertIn(500, cls.RETRY_STATUS_CODES)

    def test_retries_once_on_cloudflare_522_and_recovers(self):
        for name, cls, url, html in self.CASES:
            with self.subTest(shop=name):
                scraper = cls()
                session = MagicMock()
                session.get.side_effect = [
                    SimpleNamespace(status_code=522, text="Cloudflare 522"),
                    SimpleNamespace(status_code=200, text=html),
                ]
                scraper.session = session
                with patch("time.sleep") as mock_sleep:
                    res = scraper._fetch_page("Тест", url, 1)
                items = res.items if hasattr(res, "items") else res
                self.assertEqual(len(items), 1)
                self.assertEqual(session.get.call_count, 2)
                self.assertEqual(session.get.call_args.kwargs.get("timeout"), 30)
                mock_sleep.assert_called_once_with(cls.RETRY_DELAY_SECONDS)

    def test_retries_once_on_timeout_exception_and_recovers(self):
        for name, cls, url, html in self.CASES:
            with self.subTest(shop=name):
                scraper = cls()
                session = MagicMock()
                session.get.side_effect = [
                    TimeoutError("read timed out"),
                    SimpleNamespace(status_code=200, text=html),
                ]
                scraper.session = session
                with patch("time.sleep") as mock_sleep:
                    res = scraper._fetch_page("Тест", url, 1)
                items = res.items if hasattr(res, "items") else res
                self.assertEqual(len(items), 1)
                self.assertEqual(session.get.call_count, 2)
                mock_sleep.assert_called_once_with(cls.RETRY_DELAY_SECONDS)

    def test_does_not_retry_on_host_cooldown(self):
        for name, cls, url, _ in self.CASES:
            with self.subTest(shop=name):
                scraper = cls()
                session = MagicMock()
                session.get.side_effect = scraper_http.HostCooldown("example.kz", 120)
                scraper.session = session
                with patch("time.sleep") as mock_sleep:
                    with self.assertRaises(scraper_http.HostCooldown):
                        scraper._fetch_page("Тест", url, 1)
                self.assertEqual(session.get.call_count, 1)
                mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
