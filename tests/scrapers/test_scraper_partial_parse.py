"""Аудит 6.4.14 (A01, A02): Эврика и Alser не объявляют неполный сбор полным.

Сценарии идут через настоящий _scrape_sync базового PagedScraper; HTTP подменён HTML-фикстурами.
"""

from tests import test_support  # noqa: F401
import unittest
from unittest.mock import Mock, patch

from scrapers.alser import AlserScraper
from scrapers.evrika import EvrikaScraper
from tests.scrapers.test_scraper_completeness import ALSER_CARD, EVRIKA_NEXT_CARD, alser_page, evrika_next_page

EVRIKA_URL = "https://evrika.com/catalog/nur-sultan-astana/smartfony"
ALSER_URL = "https://alser.kz/astana/c/noutbuki"


def evrika_page(cards, total_text="", pager_pages=(), start=0, broken_price=0, out_of_stock=0):
    """Страница Эврики: pager_pages — номера страниц, на которые ведут ссылки пагинатора ЭТОЙ страницы."""
    html = evrika_next_page(cards, total_text, start=start)
    if pager_pages:
        pager = '<ul class="pagination">' + "".join(
            f'<li><a href="/catalog/nur-sultan-astana/smartfony{"" if p == 1 else f"?page={p}"}">{p}</a></li>'
            for p in pager_pages) + "</ul>"
        html = html.replace("</body>", pager + "</body>")
    for _ in range(broken_price):  # селектор цены сменился: элемента цены нет
        html = html.replace("ProductCard_productCard__infoContainer__currentPrice__abc",
                            "ProductCard_productCard__infoContainer__renamedPrice__abc", 1)
    for _ in range(out_of_stock):  # цена есть, но не числовая — намеренный пропуск
        html = html.replace(">399 990 ₸</span>", ">Нет в наличии</span>", 1)
    return html


def scrape(scraper_cls, module, url, pages):
    scraper = scraper_cls()
    scraper.PAGE_DELAY_SECONDS = 0
    responses = [Mock(status_code=200, text=html) for html in pages]
    responses.append(Mock(status_code=404, text=""))  # за пределами каталога
    with patch(f"scrapers.{module}.requests.get", side_effect=responses):
        return scraper._scrape_sync("Категория", url)


class PartialParseTest(unittest.TestCase):
    """A01: часть карточек не разобрана → обход не полный, сверка ничего не снимет."""

    def test_evrika_one_broken_card_is_not_complete(self):
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, [evrika_page(2, "2 товара", broken_price=1)])
        self.assertEqual(len(result), 1)
        self.assertFalse(result.complete)
        self.assertTrue(result.limited)
        self.assertEqual(result.unparsed, 1)

    def test_alser_one_broken_card_is_not_complete(self):
        html = alser_page(2, "2 товара").replace("info-container-product-price", "renamed-price", 1)
        result = scrape(AlserScraper, "alser", ALSER_URL, [html])
        self.assertEqual(len(result), 1)
        self.assertFalse(result.complete)
        self.assertEqual(result.unparsed, 1)

    def test_broken_card_on_earlier_page_also_blocks_completeness(self):
        pages = [evrika_page(24, "30 товаров", pager_pages=(1, 2), broken_price=1),
                 evrika_page(6, "30 товаров", pager_pages=(1, 2), start=24)]
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, pages)
        self.assertEqual(len(result), 29)
        self.assertFalse(result.complete, "конец каталога на второй странице не отменяет поломку на первой")

    def test_fully_broken_page_stays_an_error(self):
        html = evrika_page(2, "2 товара", broken_price=2)
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, [html])
        self.assertFalse(result.complete)
        self.assertEqual(result.error, "Страница 1: ValueError")

    def test_intentionally_skipped_out_of_stock_card_keeps_completeness(self):
        """Цена «Нет в наличии» — не поломка: карточка учтена счётчиком, обход полный."""
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, [evrika_page(3, "3 товара", out_of_stock=1)])
        self.assertEqual(len(result), 2)
        self.assertTrue(result.complete)

    def test_fully_parsed_categories_are_complete(self):
        evrika = scrape(EvrikaScraper, "evrika", EVRIKA_URL, [evrika_page(5, "5 товаров")])
        alser = scrape(AlserScraper, "alser", ALSER_URL, [alser_page(5, "5 товаров")])
        self.assertTrue(evrika.complete and alser.complete)

    def test_evrika_broken_card_then_out_of_stock_only_last_page_is_limited_with_unparsed(self):
        """A01 (пустая последняя страница): поломка на 1-й странице не теряется, если на 2-й только «Нет в наличии»."""
        pages = [
            evrika_page(2, "3 товара", broken_price=1),
            evrika_page(1, "3 товара", start=2, out_of_stock=1),
        ]
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, pages)
        self.assertEqual(len(result), 1)
        self.assertFalse(result.complete)
        self.assertTrue(result.limited)
        self.assertEqual(result.unparsed, 1)

    def test_alser_broken_card_then_out_of_stock_only_last_page_is_limited_with_unparsed(self):
        """A01 (пустая последняя страница): для Alser накопленный unparsed блокирует complete и при пустом items."""
        first = alser_page(2, "3 товара").replace("info-container-product-price", "renamed-price", 1)
        last = alser_page(1, "3 товара", start=2).replace("499 990 ₸", "Нет в наличии")
        result = scrape(AlserScraper, "alser", ALSER_URL, [first, last])
        self.assertEqual(len(result), 1)
        self.assertFalse(result.complete)
        self.assertTrue(result.limited)
        self.assertEqual(result.unparsed, 1)

    def test_out_of_stock_only_last_page_after_clean_first_page_is_complete(self):
        """Допустимый пропуск «Нет в наличии» на последней странице без поломок даёт complete=True."""
        evrika = scrape(
            EvrikaScraper,
            "evrika",
            EVRIKA_URL,
            [evrika_page(2, "3 товара"), evrika_page(1, "3 товара", start=2, out_of_stock=1)],
        )
        self.assertEqual(len(evrika), 2)
        self.assertTrue(evrika.complete)
        self.assertFalse(evrika.limited)
        self.assertEqual(evrika.unparsed, 0)

        alser = scrape(
            AlserScraper,
            "alser",
            ALSER_URL,
            [
                alser_page(2, "3 товара"),
                alser_page(1, "3 товара", start=2).replace("499 990 ₸", "Нет в наличии"),
            ],
        )
        self.assertEqual(len(alser), 2)
        self.assertTrue(alser.complete)
        self.assertFalse(alser.limited)
        self.assertEqual(alser.unparsed, 0)

    def test_unparsed_preserved_across_all_non_complete_exit_branches(self):
        """Накопленный unparsed сохраняется во всех ветках выхода PagedScraper (UnconfirmedEnd, ошибка, дубли, лимит)."""
        page1_broken = evrika_page(2, "4 товара", broken_price=1)

        # 1. UnconfirmedEnd на второй странице (404)
        res_unconfirmed = scrape(EvrikaScraper, "evrika", EVRIKA_URL, [page1_broken])
        self.assertFalse(res_unconfirmed.complete)
        self.assertTrue(res_unconfirmed.limited)
        self.assertEqual(res_unconfirmed.unparsed, 1)

        # 2. ValueError на полностью сломанной второй странице
        res_err = scrape(
            EvrikaScraper,
            "evrika",
            EVRIKA_URL,
            [page1_broken, evrika_page(2, "4 товара", start=2, broken_price=2)],
        )
        self.assertFalse(res_err.complete)
        self.assertEqual(res_err.error, "Страница 2: ValueError")
        self.assertEqual(res_err.unparsed, 1)

        # 3. Дубликаты на второй странице (not fresh)
        res_dup = scrape(
            EvrikaScraper,
            "evrika",
            EVRIKA_URL,
            [page1_broken, evrika_page(1, "4 товара", start=0)],
        )
        self.assertFalse(res_dup.complete)
        self.assertTrue(res_dup.limited)
        self.assertEqual(res_dup.unparsed, 1)

        # 4. Достигнут лимит страниц (max_pages=1)
        scraper = EvrikaScraper()
        scraper.PAGE_DELAY_SECONDS = 0
        with patch("scrapers.evrika.requests.get", return_value=Mock(status_code=200, text=page1_broken)):
            res_limit = scraper._scrape_sync("Категория", EVRIKA_URL, max_pages=1)
        self.assertFalse(res_limit.complete)
        self.assertTrue(res_limit.limited)
        self.assertEqual(res_limit.unparsed, 1)


class WindowedPagerTest(unittest.TestCase):
    """A02: наибольшая видимая ссылка пагинатора — не доказательство последней страницы."""

    def test_counter_wins_over_windowed_pager(self):
        pages = [evrika_page(24, "120 товаров", pager_pages=(1, 2), start=0),
                 evrika_page(24, "120 товаров", pager_pages=(1, 2, 3), start=24),
                 evrika_page(24, "120 товаров", pager_pages=(2, 3, 4), start=48),
                 evrika_page(24, "120 товаров", pager_pages=(3, 4, 5), start=72),
                 evrika_page(24, "120 товаров", pager_pages=(3, 4, 5), start=96)]
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, pages)
        self.assertEqual(len(result), 120)
        self.assertTrue(result.complete)

    def test_contradiction_between_counter_and_pages_is_not_complete(self):
        """Счётчик 120, страницы кончились на 48 — честная неполнота, а не «полно»."""
        pages = [evrika_page(24, "120 товаров", pager_pages=(1, 2), start=0),
                 evrika_page(24, "120 товаров", pager_pages=(1, 2), start=24)]
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, pages)
        self.assertEqual(len(result), 48)
        self.assertFalse(result.complete)

    def test_without_counter_forward_link_decides(self):
        pages = [evrika_page(24, pager_pages=(1, 2), start=0),
                 evrika_page(24, pager_pages=(1, 2, 3), start=24),
                 evrika_page(10, pager_pages=(1, 2, 3), start=48)]
        result = scrape(EvrikaScraper, "evrika", EVRIKA_URL, pages)
        self.assertEqual(len(result), 58)
        self.assertTrue(result.complete, "на третьей странице нет ссылки вперёд — конец")


class AlserCityTest(unittest.TestCase):
    """Город выдачи берётся из переключателя городов, а не из SEO-заголовка."""

    def page(self, city, state_city=None):
        # Как на живом alser.kz: перед переключателем городов есть другие main_button (B2B, Акции)
        buttons = "".join(f'<button type="button" name="main_button" aria-label="{label}">{label}</button>'
                          for label in ("B2B", "Акции", city))
        state = ""
        if state_city:  # состояние Nuxt: currentCity ссылается на объект города
            state = ('<script>window.__NUXT__=(function(ci,z){z={name:"Алматы",keyword:"almaty"};'
                     f'ci.name="{state_city}";ci.keyword="x";return {{currentCity:ci}}}}({{}}))</script>')
        return alser_page(2, "2 товара").replace(
            "<body>", "<body><h1>Все ноутбуки в Алматы</h1>" + buttons + state, 1)

    def test_astana_selector_with_almaty_h1_is_accepted(self):
        result = scrape(AlserScraper, "alser", ALSER_URL, [self.page("Астана")])
        self.assertTrue(result.complete)
        self.assertEqual({p["city"] for p in result}, {"Астана"})

    def test_page_state_city_is_authoritative(self):
        result = scrape(AlserScraper, "alser", ALSER_URL, [self.page("Астана", state_city="Алматы")])
        self.assertEqual(result.error, "Страница 1: RuntimeError", "currentCity из состояния главнее подписи кнопки")
        ok = scrape(AlserScraper, "alser", ALSER_URL, [self.page("Алматы", state_city="Астана")])
        self.assertTrue(ok.complete)

    def test_other_city_is_not_labelled_astana(self):
        result = scrape(AlserScraper, "alser", ALSER_URL, [self.page("Алматы")])
        self.assertEqual(list(result), [])
        self.assertFalse(result.complete)
        self.assertEqual(result.error, "Страница 1: RuntimeError")


if __name__ == "__main__":
    unittest.main()
