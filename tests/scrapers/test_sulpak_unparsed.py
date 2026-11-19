"""SULPAK-A01 (приёмка 6.42.4): видимая, но не разобранная карточка Sulpak не разрешает снятие.

Сломанная разметка цены/названия/ссылки учитывается в unparsed: обход неполный, сверка ничего
не снимает — при завершении по пагинатору, по редиректу и при объединении двух городов.
Рекламные вставки сетки и «нет в наличии» ошибками разбора не считаются. Только синтетические ответы.
"""
from tests import test_support  # noqa: F401
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scrapers.sulpak import SulpakScraper
from tests.scrapers.test_city_confirmation_r1a import _retired_after
from tests.scrapers.test_sulpak_pagination import CAT_URL, _sulpak_page_html

GOOD = ("101", "Планшет A", 100000)
SECOND = ("999", "Планшет B", 120000)
BREAKS = {
    "price": lambda h: h.replace('data-price="120000"', 'data-new-price="120000"'),
    "title": lambda h: h.replace('data-name="Планшет B"', "").replace(">Планшет B</a>", "></a>"),
    "link": lambda h: h.replace('href="/g/tablet-999"', 'href="/catalog/tablet-999"'),
}
BANNER = '<div class="product__item" data-url="/Filter/BannerBlock?categoryId=62"></div>'
OUT_OF_STOCK = ('<div class="product__item" data-code="555" data-name="Кофемашина" data-price="0">'
                '<a href="/g/coffee-555">Кофемашина</a><span>Нет в наличии</span></div>')


def _crawl(cities, mode, page_html_for):
    """mode: pages_count — одна страница по пагинатору; redirect — ?page=2 -> страница 1."""
    def get(url, cookies=None, **kw):
        cid = (cookies or {}).get("city_id")
        base = url.split("?")[0]
        html = page_html_for(cid, pages_count=1 if mode == "pages_count" else 9)
        return SimpleNamespace(status_code=200, url=base, text=html)
    with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
        return SulpakScraper(cities=cities)._scrape_sync("Планшеты", CAT_URL, 5)


def _page(items, transform=lambda h: h, extra=""):
    def build(cid, pages_count):
        html = transform(_sulpak_page_html(cid, items, current_page=1, pages_count=pages_count))
        return html.replace("</body>", extra + "</body>")
    return build


class SulpakUnparsedCardTest(unittest.TestCase):
    ASTANA = ("sulpak_101@astana", "sulpak_999@astana")

    def _assert_kept(self, res, ids):
        self.assertFalse(res.complete)
        retired, active, assessment = _retired_after(res, ids, "sulpak", CAT_URL)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertEqual(active, set(ids))

    def test_broken_card_blocks_retirement_on_both_end_paths(self):
        for mode in ("pages_count", "redirect"):
            for broken, transform in BREAKS.items():
                with self.subTest(mode=mode, broken=broken):
                    res = _crawl(("Астана",), mode, _page([GOOD, SECOND], transform))
                    self.assertEqual(len(res), 1)
                    self.assertEqual(res.unparsed, 1)
                    self._assert_kept(res, self.ASTANA)

    def test_broken_card_in_one_of_two_cities_blocks_retirement(self):
        def page(cid, pages_count):
            transform = BREAKS["price"] if cid == "3" else (lambda h: h)
            return _page([GOOD, SECOND], transform)(cid, pages_count)
        for mode in ("pages_count", "redirect"):
            with self.subTest(mode=mode):
                res = _crawl(("Алматы", "Астана"), mode, page)
                self.assertEqual(res.unparsed, 1)
                ids = ("sulpak_101@almaty", "sulpak_999@almaty") + self.ASTANA
                self._assert_kept(res, ids)

    def test_banners_and_out_of_stock_are_not_parse_errors(self):
        for mode in ("pages_count", "redirect"):
            with self.subTest(mode=mode):
                res = _crawl(("Астана",), mode, _page([GOOD, SECOND], extra=BANNER * 3 + OUT_OF_STOCK))
                self.assertEqual(res.unparsed, 0)
                self.assertTrue(res.complete, res.error)
                self.assertEqual(len(res), 2)

    def test_positive_control_still_retires_really_missing_offer(self):
        """Без сломанных карточек обход полный и действительно исчезнувший товар снимается."""
        for mode in ("pages_count", "redirect"):
            with self.subTest(mode=mode):
                res = _crawl(("Астана",), mode, _page([GOOD]))
                self.assertTrue(res.complete, res.error)
                self.assertEqual(res.unparsed, 0)
                retired, active, assessment = _retired_after(res, self.ASTANA, "sulpak", CAT_URL)
                self.assertTrue(assessment["may_retire"])
                self.assertEqual(retired, ["sulpak_999@astana"])
                self.assertEqual(active, {"sulpak_101@astana"})


if __name__ == "__main__":
    unittest.main()
