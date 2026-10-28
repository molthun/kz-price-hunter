"""Подтверждение конца каталога у Alser, MasterOK и Эврики (офлайн, без обращений к магазинам).

Зачем: оба адаптера не могли отчитаться `complete` ни при каком размере каталога. Вторая страница
карточек не содержала, базовый класс помечал обход `limited`, и по правилу P02 такой обход не обучает
норму источника — baseline у этих магазинов не строился никогда, а в мониторинге они навсегда
оставались «собран не полностью», неотличимо от настоящего обрыва.

Оба сайта сами печатают число товаров, и теперь адаптеры его читают. Проверяется и обратное:
когда подтвердить полноту нечем или карточки сломаны, ответ «нет» / ошибка, а не «да» —
неизвестность и сбой парсинга не выдаются за порядок.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlparse

import config
from scrapers.alser import AlserScraper
from scrapers.base import UnconfirmedEnd
from scrapers.evrika import EvrikaScraper
from scrapers.masterok import MasterOkScraper

ALSER_CARD = """
<a href="/p/noutbuk-{n}">
  <article class="product-card">
    <div class="product-card__title">Ноутбук {n}</div>
    <div class="info-container-product-price">499 990 ₸</div>
  </article>
</a>
"""


def alser_page(cards: int, total_text: str = "", start: int = 0) -> str:
    # start — номер первого товара: у следующей страницы настоящего каталога свои товары, а полнота
    # считается по уникальным карточкам (A02)
    counter = f'<p class="mb-16 total-text">{total_text}</p>' if total_text else ""
    return "<html><body>" + counter + "".join(
        ALSER_CARD.format(n=start + i) for i in range(cards)) + "</body></html>"


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
                                    [alser_page(12, "18 товаров"), alser_page(6, start=12)])
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
                                Mock(status_code=200, text=alser_page(6, start=12))]):
            big_first = scraper._fetch_page("Мониторы", self.URL + "/monitory", 1)
            small = scraper._fetch_page("Планшеты", self.URL + "/planshety", 1)
            big_second = scraper._fetch_page("Мониторы", self.URL + "/monitory", 2)
        self.assertFalse(big_first.complete)
        self.assertTrue(small.complete, "своя маленькая категория закрыта")
        self.assertTrue(big_second.complete, "чужая категория не сбила счёт страниц")

    def test_broken_card_markup_raises_error_instead_of_claiming_complete(self):
        """Если карточки в DOM есть, но из-за смены вёрстки не разобралась ни одна, это ошибка, а не конец каталога."""
        broken_page = (
            "<html><body>"
            + "".join('<article class="product-card"><div class="broken">---</div></article>' for _ in range(6))
            + "</body></html>"
        )
        scraper = AlserScraper()
        scraper.page_delay = (0, 0)
        with patch("scrapers.alser.requests.get",
                   side_effect=[Mock(status_code=200, text=alser_page(12, "18 товаров")),
                                Mock(status_code=200, text=broken_page)]):
            result = scraper._scrape_sync("Ноутбуки", self.URL)
        self.assertFalse(result.complete, "сломанная вторая страница не может считаться полным обходом")
        self.assertEqual(result.error, "Страница 2: ValueError")

    def test_alser_configured_categories_match_live_catalog_tree(self):
        """В конфигурации Alser нет удалённых 404-разделов и старого слага /c/pylesosy."""
        slugs = {urlparse(cat["url"]).path.rstrip("/").split("/")[-1] for cat in config.ALSER_CATEGORIES}
        removed_404_slugs = {
            "pylesosy",
            "smartfony",
            "televizory",
            "monitory",
            "planshety",
            "vse-holodilniki",
            "vse-kondicioneri",
        }
        self.assertTrue(slugs.isdisjoint(removed_404_slugs), f"В ALSER_CATEGORIES остались 404-слаги: {slugs & removed_404_slugs}")
        self.assertIn("vse-pilesosy", slugs)


EVRIKA_NEXT_CARD = """
<div class="productCard-ui">
  <div class="ProductCard_productCard__content__abc">
    <a class="ProductCard_productCard__imageContainer__abc" href="/catalog/smartfony/apple-iphone-{n}/p{pid}">
      <img src="/_next/image?url=https%3A%2F%2Fcdn.evrika.com%2Fimg-{pid}.jpg&amp;w=384&amp;q=100"
           data-original-src="https://cdn.evrika.com/img-{pid}.jpg"
           alt="Смартфон {n}" />
    </a>
    <div class="ProductCard_productCard__infoContainer__abc">
      <div>
        <div class="ProductCard_productCard__infoContainer__oldPrice__abc">
          <span class="ProductCard_productCard__infoContainer__oldPriceProcent__abc"> - 18 %</span>
        </div>
        <a class="ProductCard_productCard__infoContainer__titleOfProduct__abc"
           href="/catalog/smartfony/apple-iphone-{n}/p{pid}">
          Смартфон Apple iPhone {n}
        </a>
      </div>
      <div>
        <div class="ProductCard_productCard__infoContainer__costContainer__abc">
          <span class="ProductCard_productCard__infoContainer__currentPrice__abc">399 990 ₸</span>
          <span class="ProductCard_productCard__infoContainer__oldPrice__abc">489 990 ₸</span>
        </div>
        <div class="ProductCard_productCard__infoContainer__oldPriceContainer__abc">
          <span>до 39 999</span>
        </div>
      </div>
    </div>
  </div>
</div>
"""


def evrika_next_page(cards: int, total_text: str = "", last_page: int = 0, start: int = 0) -> str:
    counter = (
        f'<span class="FilterNavigation_filterCategory__filterButton__quantityOfProducts__abc">{total_text}</span>'
        if total_text else ""
    )
    pager = (
        '<ul class="pagination">'
        + "".join(
            f'<li><a href="/catalog/nur-sultan-astana/smartfony?page={p}">{p}</a></li>'
            for p in range(1, last_page + 1)
        )
        + "</ul>"
        if last_page else ""
    )
    body = "".join(EVRIKA_NEXT_CARD.format(n=start + i + 1, pid=48263 + start + i) for i in range(cards))
    return f"<!DOCTYPE html><html><body>{counter}<div class='productsList-ui'>{body}</div>{pager}</body></html>"


class EvrikaCompletenessTest(unittest.TestCase):
    URL = "https://back.evrika.com/catalog/nur-sultan-astana/smartfony/"

    def _html_response(self, html: str, status_code: int = 200) -> Mock:
        resp = Mock(status_code=status_code, text=html)
        resp.json.side_effect = json.JSONDecodeError("Expecting value", html, 0)
        return resp

    def test_evrika_nextjs_html_parses_cards_old_price_and_confirms_completeness(self):
        """Эврика отдаёт Next.js HTML вместо старого JSON; карточки, старая цена и конец каталога разбираются."""
        scraper = EvrikaScraper()
        with patch("scrapers.evrika.requests.get",
                   side_effect=[
                       self._html_response(evrika_next_page(24, "30 товаров", last_page=2)),
                       self._html_response(evrika_next_page(6, "30 товаров", last_page=2, start=24)),
                   ]):
            first = scraper._fetch_page("Смартфоны", self.URL, 1)
            second = scraper._fetch_page("Смартфоны", self.URL, 2)

        self.assertEqual(len(first), 24)
        self.assertFalse(first.complete, "первая страница из двух ещё не закрывает каталог")
        self.assertEqual(len(second), 6)
        self.assertTrue(second.complete, "вторая страница закрывает каталог")

        item = first[0]
        self.assertEqual(item["id"], "evrika_48263")
        self.assertEqual(item["title"], "Смартфон Apple iPhone 1")
        self.assertEqual(item["price"], 399990)
        self.assertEqual(item["old_price_on_site"], 489990, "старая цена не должна путаться со скидкой -18% или бонусами")
        self.assertEqual(item["url"], "https://evrika.com/catalog/smartfony/apple-iphone-1/p48263")
        self.assertEqual(item["image_url"], "https://cdn.evrika.com/img-48263.jpg")

        # Одностраничная категория без пагинатора подтверждает полноту по счётчику товаров
        with patch("scrapers.evrika.requests.get",
                   return_value=self._html_response(evrika_next_page(5, "5 товаров"))):
            single = scraper._fetch_page("Телевизоры", "https://evrika.com/catalog/televizory", 1)
        self.assertEqual(len(single), 5)
        self.assertTrue(single.complete)

    def test_evrika_broken_cards_or_empty_html_never_claim_complete(self):
        """Сломанная вёрстка карточек или пустой ответ Эврики не маскируются под конец каталога."""
        broken_cards_html = (
            '<html><body><span class="FilterNavigation_quantityOfProducts__x">2 товара</span>'
            '<div class="productCard-ui"><div class="broken">нет цены и ссылки</div></div>'
            '</body></html>'
        )
        scraper = EvrikaScraper()
        scraper.page_delay = (0, 0)
        with patch("scrapers.evrika.requests.get",
                   return_value=self._html_response(broken_cards_html)):
            res_broken = scraper._scrape_sync("Смартфоны", self.URL)
        self.assertFalse(res_broken.complete)
        self.assertEqual(res_broken.error, "Страница 1: ValueError")

        empty_html = "<html><body><div>Каталог временно недоступен</div></body></html>"
        with patch("scrapers.evrika.requests.get",
                   return_value=self._html_response(empty_html)):
            with self.assertRaises(UnconfirmedEnd):
                scraper._fetch_page("Смартфоны", self.URL, 1)


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
