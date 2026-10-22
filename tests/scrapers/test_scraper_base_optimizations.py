"""База скраперов: retry временных сбоев, быстрый parse_price и метрики категории.

Проверяем поведение: 5xx/сеть повторяются с паузой, 4xx/cooldown/конец каталога — нет;
метрики печатают количество товаров, страниц и время; быстрый выход не ломает разбор цен.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import contextlib
import io
import unittest

from scrapers.base import PagedScraper, ScanResult, UnconfirmedEnd, parse_price
from scrapers.http import HostCooldown


class FakeScraper(PagedScraper):
    SHOP_NAME = "Тест"
    PAGE_DELAY_SECONDS = 0
    RETRY_BASE_DELAY = 0
    RETRY_JITTER_SECONDS = 0

    def __init__(self, pages, failures=None):
        self.pages = pages
        self.failures = dict(failures or {})  # page -> количество ошибок перед успехом
        self.calls = 0
        self.errors = {}

    def _fetch_page(self, category_name, category_url, page):
        self.calls += 1
        remaining = self.failures.get(page, 0)
        if remaining > 0:
            self.failures[page] = remaining - 1
            raise self.errors.get(page, RuntimeError("HTTP 500"))
        return self.pages.get(page, [])


class RetryTest(unittest.TestCase):
    def test_transient_5xx_is_retried_then_succeeds(self):
        scraper = FakeScraper(pages={1: ScanResult([{"id": "a"}], complete=True)})
        scraper.failures = {1: 2}
        scraper.errors = {1: RuntimeError("HTTP 502 Bad Gateway")}
        result = scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, 3)
        self.assertEqual([item["id"] for item in result], ["a"])

    def test_last_error_is_returned_after_all_attempts(self):
        scraper = FakeScraper(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: RuntimeError("HTTP 500")}
        result = scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, scraper.RETRY_ATTEMPTS)
        self.assertIn("RuntimeError", result.error)

    def test_4xx_is_not_retried(self):
        scraper = FakeScraper(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: RuntimeError("HTTP 403")}
        scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, 1)

    def test_network_timeout_is_retried(self):
        scraper = FakeScraper(pages={1: []})
        scraper.failures = {1: 1}
        scraper.errors = {1: TimeoutError("таймаут")}
        result = scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, 2)
        self.assertFalse(result.complete)

    def test_unconfirmed_end_is_not_retried(self):
        scraper = FakeScraper(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: UnconfirmedEnd("конец не подтверждён")}
        result = scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, 1)
        self.assertEqual(result.error, "Карточки не найдены на первой странице")

    def test_host_cooldown_is_not_retried(self):
        scraper = FakeScraper(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: HostCooldown("shop.example", 600)}
        result = scraper._scrape_sync("Категория", "https://shop.example/c")
        self.assertEqual(scraper.calls, 1)
        self.assertIn("HostCooldown", result.error)

    def test_retry_parameters_are_overridable(self):
        class OneAttempt(FakeScraper):
            RETRY_ATTEMPTS = 1

        scraper = OneAttempt(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: RuntimeError("HTTP 503")}
        with self.assertRaises(RuntimeError):
            scraper._fetch_page_with_retry("Категория", "https://shop.example/c", 1)


class MetricsTest(unittest.TestCase):
    def test_completed_category_reports_products_pages_and_duration(self):
        scraper = FakeScraper(pages={1: ScanResult([{"id": "a"}], complete=True)})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = scraper._scrape_sync("Ноутбуки", "https://shop.example/c")
        text = output.getvalue()
        self.assertTrue(result.complete)
        self.assertIn("Тест", text)
        self.assertIn("Ноутбуки", text)
        self.assertIn("1 товаров за 1 стр.", text)
        self.assertIn("завершено", text)
        self.assertIn("с", text)

    def test_error_category_reports_failure(self):
        scraper = FakeScraper(pages={})
        scraper.failures = {1: 99}
        scraper.errors = {1: RuntimeError("HTTP 400")}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = scraper._scrape_sync("Смартфоны", "https://shop.example/c")
        self.assertIn("ошибка: RuntimeError", output.getvalue())
        self.assertIn("RuntimeError", result.error)


class ParsePriceFastPathTest(unittest.TestCase):
    def test_text_without_digits_returns_zero(self):
        self.assertEqual(parse_price("нет цены"), 0)
        self.assertEqual(parse_price("₸"), 0)
        self.assertEqual(parse_price("   "), 0)

    def test_regular_price_still_parsed(self):
        self.assertEqual(parse_price("179 990 ₸"), 179990)
        self.assertEqual(parse_price("72 228.00"), 72228)
        self.assertEqual(parse_price("Цена: 45 000 тг"), 45000)

    def test_installment_and_discount_blocks_still_skipped(self):
        self.assertEqual(parse_price("5 990 ₸/мес"), 0)
        self.assertEqual(parse_price("-20 000 ₸ 150 000 ₸"), 150000)
        self.assertEqual(parse_price("15% 99 990"), 99990)

    def test_foreign_currency_still_zero(self):
        self.assertEqual(parse_price("$199"), 0)
        self.assertEqual(parse_price("199 USD"), 0)


if __name__ == "__main__":
    unittest.main()
