"""Регрессии конца каталога Sulpak (6.42.4): живая фикстура пагинатора, редирект на page-1,
защита от повтора без редиректа, HTTP 404 и подтверждение города R1-A01. Только синтетические ответы."""
from tests import test_support  # noqa: F401
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import source_failure
from scrapers.sulpak import SulpakScraper
from tests.scrapers.test_city_confirmation_r1a import _retired_after

CAT_URL = "https://www.sulpak.kz/f/planshetiy"


def _sulpak_page_html(
    city_id,
    items,
    *,
    current_page=1,
    pages_count=9,
    include_swiper_noise=True,
):
    """Живая структура HTML sulpak.kz: .swiper-pagination перед #paginator + data-city-id."""
    city_mark = f'<button class="subscribe-btn" data-city-id="{city_id}"></button>' if city_id is not None else ""
    swiper = '<div class="swiper-pagination pagination"></div>' if include_swiper_noise else ""
    cards = "\n".join(
        f'<div class="product__item" data-code="{code}" data-name="{name}" data-price="{price}">'
        f'<a href="/g/tablet-{code}">{name}</a></div>'
        for code, name, price in items
    )
    paginator = ""
    if pages_count is not None or current_page is not None:
        attrs = []
        if current_page is not None:
            attrs.append(f'data-currentpage="{current_page}"')
        if pages_count is not None:
            attrs.append(f'data-pagescount="{pages_count}"')
        paginator = f'<div id="paginator" class="pagination" {" ".join(attrs)}></div>'
    return f"<html><body>{city_mark}{swiper}{cards}{paginator}</body></html>"


class SulpakPaginationTest(unittest.TestCase):
    EXISTING_ASTANA = ("sulpak_101@astana", "sulpak_102@astana", "sulpak_999@astana")

    def _assert_no_retirement(self, res, existing_ids=None):
        ids = existing_ids or self.EXISTING_ASTANA
        self.assertFalse(res.complete)
        retired, active, assessment = _retired_after(res, ids, "sulpak", CAT_URL)
        self.assertFalse(assessment["may_retire"])
        self.assertEqual(retired, [])
        self.assertEqual(active, set(ids))

    def test_city_url_appends_astana_and_keeps_almaty_canonical(self):
        self.assertEqual(
            SulpakScraper._city_url("https://www.sulpak.kz/f/planshetiy", "Астана"),
            "https://www.sulpak.kz/f/planshetiy/astana",
        )
        self.assertEqual(
            SulpakScraper._city_url("https://www.sulpak.kz/f/smartfoniy/", "Астана"),
            "https://www.sulpak.kz/f/smartfoniy/astana",
        )
        self.assertEqual(
            SulpakScraper._city_url("https://www.sulpak.kz/f/planshetiy/astana", "Алматы"),
            "https://www.sulpak.kz/f/planshetiy",
        )
        self.assertEqual(
            SulpakScraper._city_url("https://www.sulpak.kz/sale/1", "Астана"),
            "https://www.sulpak.kz/sale/1",
        )

    def test_confirmed_end_by_redirect_to_prev_page_with_swiper_noise(self):
        """Живой сценарий sulpak.kz: data-pagescount=9, но реальных страниц 2; ?page=3 -> 301 ?page=2."""
        seen_urls = []

        def get(url, cookies=None, **kw):
            seen_urls.append(url)
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            if "page=3" in url:
                html = _sulpak_page_html(cid, [("102", "Планшет B", 120000)], current_page=2, pages_count=9)
                return SimpleNamespace(status_code=200, url=f"{base}?page=2", text=html)
            if "page=2" in url:
                html = _sulpak_page_html(cid, [("102", "Планшет B", 120000)], current_page=2, pages_count=9)
                return SimpleNamespace(status_code=200, url=f"{base}?page=2", text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=9)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Алматы", "Астана"))._scrape_sync("Планшеты", CAT_URL, 10)

        self.assertTrue(res.complete, res.error)
        self.assertFalse(res.limited)
        self.assertIsNone(res.error)
        self.assertEqual(len(res), 4)
        self.assertEqual({x["city"] for x in res}, {"Алматы", "Астана"})
        self.assertTrue(any(u.startswith(f"{CAT_URL}/astana") for u in seen_urls))

    def test_confirmed_end_by_redirect_from_page_2_to_base_url(self):
        """Если реальная страница одна, а data-pagescount=3: ?page=2 -> 301 без ?page."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=3)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.complete, res.error)
        self.assertEqual(len(res), 1)

    def test_confirmed_end_single_page_without_paginator_redirects_to_page_1(self):
        """Живой сценарий igroviye_pristavki: #paginator нет, ?page=2 -> 301 ?page=1."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            html = _sulpak_page_html(cid, [("101", "Приставка A", 250000)], current_page=None, pages_count=None)
            final = f"{base}?page=1" if "page=2" in url else base
            return SimpleNamespace(status_code=200, url=final, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Алматы", "Астана"))._scrape_sync("Приставки", CAT_URL, 5)

        self.assertTrue(res.complete, res.error)
        self.assertEqual(len(res), 2)

    def test_single_page_without_paginator_and_without_page_1_redirect_is_not_complete(self):
        """Без #paginator и без явного редиректа ?page=2 -> ?page=1 повтор не считается концом."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            html = _sulpak_page_html(cid, [("101", "Приставка A", 250000)], current_page=None, pages_count=None)
            return SimpleNamespace(status_code=200, url=url, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Приставки", CAT_URL, 5)

        self.assertTrue(res.limited)
        self._assert_no_retirement(res)

    def test_confirmed_end_by_pages_count_exact(self):
        """Обычное завершение на page_num == data-pagescount с .swiper-pagination перед #paginator."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            if "page=2" in url:
                html = _sulpak_page_html(cid, [("102", "Планшет B", 120000)], current_page=2, pages_count=2)
                return SimpleNamespace(status_code=200, url=f"{base}?page=2", text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=2)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.complete, res.error)
        self.assertEqual(len(res), 2)

    def test_repeated_page_without_redirect_is_not_complete(self):
        """Повтор карточек без редиректа (r.url остался ?page=2) — НЕ конец каталога."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            return SimpleNamespace(status_code=200, url=url, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.limited)
        self._assert_no_retirement(res)

    def test_redirect_reset_to_page_1_from_page_3_is_not_complete(self):
        """Сброс ?page=3 на ?page=1 (шаг не равен page-1) — НЕ конец каталога."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            if "page=3" in url:
                html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=9)
                return SimpleNamespace(status_code=200, url=f"{CAT_URL}?page=1", text=html)
            if "page=2" in url:
                html = _sulpak_page_html(cid, [("102", "Планшет B", 120000)], current_page=2, pages_count=9)
                return SimpleNamespace(status_code=200, url=f"{CAT_URL}?page=2", text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=9)
            return SimpleNamespace(status_code=200, url=CAT_URL, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.limited)
        self._assert_no_retirement(res)

    def test_redirect_to_unrelated_path_is_not_complete(self):
        """Редирект ?page=2 на главную страницу вместо категории — НЕ конец каталога."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            final = "https://www.sulpak.kz/" if "page=2" in url else CAT_URL
            return SimpleNamespace(status_code=200, url=final, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.limited)
        self._assert_no_retirement(res)

    def test_http_404_on_next_page_is_unconfirmed_end(self):
        """HTTP 404 после последней страницы по-прежнему не считается complete=True."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            if "page=2" in url:
                return SimpleNamespace(status_code=404, url=url, text="Not found")
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            return SimpleNamespace(status_code=200, url=CAT_URL, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertTrue(res.limited)
        self.assertEqual(source_failure.result_failure(res)["error_code"], "end_unconfirmed")
        self._assert_no_retirement(res)

    def test_redirect_end_with_foreign_city_rejected_by_r1_a01(self):
        """R1-A01: чужой data-city-id на редирект-странице конца отклоняется ДО признания конца."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            if "page=2" in url:
                html = _sulpak_page_html("1", [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
                return SimpleNamespace(status_code=200, url=base, text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertEqual(source_failure.result_failure(res)["error_code"], "parse")
        self._assert_no_retirement(res)

    def test_redirect_end_with_missing_city_rejected_by_r1_a01(self):
        """R1-A01: отсутствующий data-city-id на редирект-странице конца отклоняется ДО признания конца."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            if "page=2" in url:
                html = _sulpak_page_html(None, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
                return SimpleNamespace(status_code=200, url=base, text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Астана",))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertEqual(source_failure.result_failure(res)["error_code"], "parse")
        self._assert_no_retirement(res)

    def test_second_city_foreign_city_on_redirect_blocks_complete(self):
        """Если у второго города на редирект-странице чужой город, объединённый обход неполный."""
        def get(url, cookies=None, **kw):
            cid = (cookies or {}).get("city_id")
            base = url.split("?")[0]
            if "page=2" in url:
                html = _sulpak_page_html("1", [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
                return SimpleNamespace(status_code=200, url=base, text=html)
            html = _sulpak_page_html(cid, [("101", "Планшет A", 100000)], current_page=1, pages_count=5)
            return SimpleNamespace(status_code=200, url=base, text=html)

        with patch("scrapers.sulpak.requests.get", side_effect=get), patch("time.sleep"):
            res = SulpakScraper(cities=("Алматы", "Астана"))._scrape_sync("Планшеты", CAT_URL, 5)

        self.assertIn("Астана:", res.error or "")
        ids = ("sulpak_101@almaty",) + self.EXISTING_ASTANA
        self._assert_no_retirement(res, existing_ids=ids)


if __name__ == "__main__":
    unittest.main()
