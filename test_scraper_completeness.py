"""Подтверждение конца каталога у Alser и MasterOK (офлайн, без обращений к магазинам).

Зачем: оба адаптера не могли отчитаться `complete` ни при каком размере каталога. Вторая страница
карточек не содержала, базовый класс помечал обход `limited`, и по правилу P02 такой обход не обучает
норму источника — baseline у этих магазинов не строился никогда, а в мониторинге они навсегда
оставались «собран не полностью», неотличимо от настоящего обрыва.

Оба сайта сами печатают число товаров, и теперь адаптеры его читают. Проверяется и обратное:
когда подтвердить полноту нечем, ответ «нет», а не «да» — неизвестность не выдаётся за порядок.
"""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import unittest
from unittest.mock import Mock, patch

from scrapers.alser import AlserScraper
from scrapers.masterok import MasterOkScraper

ALSER_CARD = """
<a href="/p/noutbuk-{n}">
  <article class="product-card">
    <div class="product-card__title">Ноутбук {n}</div>
    <div class="info-container-product-price">499 990 ₸</div>
  </article>
</a>
"""


def alser_page(cards: int, total_text: str = "") -> str:
    counter = f'<p class="mb-16 total-text">{total_text}</p>' if total_text else ""
    return "<html><body>" + counter + "".join(
        ALSER_CARD.format(n=i) for i in range(cards)) + "</body></html>"


MASTEROK_CARD = """
<div class="catalog-item-card" id="bx_40480796_{n}_52eccb44">
  <span itemprop="name">Штабелёр {n}</span>
  <a class="item-title" href="/catalog/skladskoe-oborudovanie/shtabeler-{n}/">Штабелёр {n}</a>
  <meta itemprop="price" content="150000">
</div>
"""


def masterok_page(cards: int, total: str = "", last_page: int = 0) -> str:
    counter = f'<div class="count_items"><label>Товаров:</label><span>{total}</span></div>' if total else ""
    pager = "".join(
        f'<a href="/catalog/skladskoe-oborudovanie/?PAGEN_1={p}">{p}</a>'
        for p in range(2, last_page + 1)) if last_page else ""
    return "<html><body>" + counter + "".join(
        MASTEROK_CARD.format(n=i) for i in range(cards)) + pager + "</body></html>"


class AlserCompletenessTest(unittest.TestCase):
    URL = "https://alser.kz/astana/c/noutbuki"

    def _fetch(self, scraper, pages):
        """Возвращает результаты _fetch_page для последовательности страниц."""
        results = []
        with patch("scrapers.alser.requests.get",
                   side_effect=[Mock(status_code=200, text=html) for html in pages]):
            for number, _ in enumerate(pages, start=1):
                results.append(scraper._fetch_page("Ноутбуки", self.URL, number))
        return results

    def test_small_category_fully_collected_is_complete(self):
        """5 карточек при счётчике «5 товаров» — каталог собран целиком."""
        (page,) = self._fetch(AlserScraper(), [alser_page(5, "5 товаров")])
        self.assertEqual(len(page), 5)
        self.assertTrue(page.complete, "маленькая категория собрана полностью")

    def test_multipage_category_confirms_only_on_last_page(self):
        """18 товаров по 12 на страницу: полнота подтверждается на второй странице, не на первой."""
        first, second = self._fetch(AlserScraper(),
                                    [alser_page(12, "18 товаров"), alser_page(6)])
        self.assertFalse(first.complete, "на первой странице собрано не всё")
        self.assertTrue(second.complete, "вторая страница закрывает каталог")

    def test_without_counter_completeness_is_not_claimed(self):
        """Счётчика нет — подтвердить конец нечем, и «полно» не выставляется."""
        (page,) = self._fetch(AlserScraper(), [alser_page(5)])
        self.assertEqual(len(page), 5)
        self.assertFalse(page.complete, "неизвестность не выдаётся за полный обход")

    def test_categories_do_not_borrow_each_others_totals(self):
        """Ожидаемое число страниц хранится по категории: параллельный обход их не смешивает."""
        scraper = AlserScraper()
        with patch("scrapers.alser.requests.get",
                   side_effect=[Mock(status_code=200, text=alser_page(12, "18 товаров")),
                                Mock(status_code=200, text=alser_page(4, "4 товара")),
                                Mock(status_code=200, text=alser_page(6))]):
            big_first = scraper._fetch_page("Мониторы", self.URL + "/monitory", 1)
            small = scraper._fetch_page("Планшеты", self.URL + "/planshety", 1)
            big_second = scraper._fetch_page("Мониторы", self.URL + "/monitory", 2)
        self.assertFalse(big_first.complete)
        self.assertTrue(small.complete, "своя маленькая категория закрыта")
        self.assertTrue(big_second.complete, "чужая категория не сбила счёт страниц")


class MasterOkCompletenessTest(unittest.TestCase):
    URL = "https://masterok.kz/catalog/skladskoe-oborudovanie/"

    def _fetch(self, scraper, html, page_num=1):
        session = Mock()
        session.get.return_value = Mock(status_code=200, text=html)
        with patch.object(scraper, "_get_session", return_value=session):
            return scraper._fetch_page("Склад", self.URL, page_num)

    def test_last_page_of_paginator_is_complete(self):
        scraper = MasterOkScraper()
        middle = self._fetch(scraper, masterok_page(12, "690", last_page=3), page_num=2)
        last = self._fetch(scraper, masterok_page(6, "690", last_page=3), page_num=3)
        self.assertFalse(middle.complete, "середина пагинатора — ещё не конец")
        self.assertTrue(last.complete, "последняя страница пагинатора закрывает каталог")

    def test_single_page_category_uses_shop_counter(self):
        """Пагинатора нет, а счётчик совпал с числом карточек — категория собрана."""
        page = self._fetch(MasterOkScraper(), masterok_page(10, "10"))
        self.assertEqual(len(page), 10)
        self.assertTrue(page.complete)

    def test_counter_larger_than_page_is_not_complete(self):
        """Счётчик больше собранного — значит есть ещё, полнота не подтверждается."""
        page = self._fetch(MasterOkScraper(), masterok_page(12, "690"))
        self.assertFalse(page.complete)

    def test_without_counter_and_paginator_completeness_is_not_claimed(self):
        page = self._fetch(MasterOkScraper(), masterok_page(12))
        self.assertFalse(page.complete, "нечем подтвердить — значит не подтверждено")

    def test_http_error_is_reported_as_error_not_as_empty_category(self):
        """Раньше ответ 500 возвращал пустой список, и обход выглядел как «ничего не нашлось»."""
        scraper = MasterOkScraper()
        session = Mock()
        session.get.return_value = Mock(status_code=500, text="")
        with patch.object(scraper, "_get_session", return_value=session):
            with self.assertRaises(RuntimeError) as caught:
                scraper._fetch_page("Склад", self.URL, 1)
        self.assertIn("500", str(caught.exception))

    def test_network_failure_is_reported_as_error(self):
        scraper = MasterOkScraper()
        session = Mock()
        session.get.side_effect = TimeoutError("таймаут")
        with patch.object(scraper, "_get_session", return_value=session):
            with self.assertRaises(RuntimeError) as caught:
                scraper._fetch_page("Склад", self.URL, 1)
        self.assertIn("TimeoutError", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
