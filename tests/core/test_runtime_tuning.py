"""Разбор HTML и размер пула потоков: настройки, влияющие на нагрузку. Без сети."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""

import config  # noqa: E402
import html_soup  # noqa: E402


class HtmlParserTest(unittest.TestCase):
    def test_lxml_is_used_when_available(self):
        self.assertEqual(html_soup.PARSER, "lxml",
                         "lxml закреплён в requirements: обход должен разбирать им")

    def test_broken_markup_is_parsed(self):
        """Каталоги приходят с незакрытыми тегами — разбор обязан их переживать."""
        soup = html_soup.make_soup(
            "<article class=card><h3 class=title>Смартфон<div class=price>199 900 &#8376;")
        self.assertEqual(soup.select_one("h3.title").get_text(strip=True), "Смартфон199 900 ₸")
        self.assertEqual(soup.select_one(".price").get_text(strip=True), "199 900 ₸")

    def test_cards_are_found_by_selector(self):
        page = "<main>" + "".join(
            f'<article class="product-card"><h3>Товар {i}</h3>'
            f'<span class="price price--current">{1000 + i}</span></article>' for i in range(5)) + "</main>"
        cards = html_soup.make_soup(page).select("article.product-card")
        self.assertEqual(len(cards), 5)
        self.assertEqual(cards[2].select_one("span.price--current").get_text(strip=True), "1002")

    def test_explicit_parser_wins(self):
        self.assertEqual(html_soup.make_soup("<p>x</p>", "html.parser").select_one("p").get_text(), "x")

    def test_falls_back_without_lxml(self):
        """Окружение без lxml не должно ронять обход — разбор идёт штатным парсером."""
        real_import = __import__

        def no_lxml(name, *args, **kwargs):
            if name == "lxml":
                raise ImportError("нет lxml")
            return real_import(name, *args, **kwargs)

        # Восстановление делается уже без подмены импорта, иначе модуль так и останется
        # с запасным парсером и утянет за собой остальные тесты.
        self.addCleanup(importlib.reload, sys.modules["html_soup"])
        with patch("builtins.__import__", side_effect=no_lxml):
            reloaded = importlib.reload(html_soup)
            self.assertEqual(reloaded.PARSER, "html.parser")
            self.assertEqual(reloaded.make_soup("<p>ок</p>").select_one("p").get_text(), "ок")


class WorkerThreadCountTest(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("WORKER_THREADS")
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("WORKER_THREADS", None)
        else:
            os.environ["WORKER_THREADS"] = self._saved

    def test_scales_with_cores(self):
        os.environ.pop("WORKER_THREADS", None)
        with patch("os.cpu_count", return_value=4):
            self.assertEqual(config.worker_thread_count(), 16)

    def test_never_drops_below_floor(self):
        """На одноядерной машине пул всё равно должен обслуживать запросы и запись."""
        os.environ.pop("WORKER_THREADS", None)
        with patch("os.cpu_count", return_value=1):
            self.assertEqual(config.worker_thread_count(), config.WORKER_THREADS_MIN)

    def test_capped_on_large_hosts(self):
        os.environ.pop("WORKER_THREADS", None)
        with patch("os.cpu_count", return_value=64):
            self.assertEqual(config.worker_thread_count(), config.WORKER_THREADS_MAX)

    def test_unknown_core_count_is_survived(self):
        os.environ.pop("WORKER_THREADS", None)
        with patch("os.cpu_count", return_value=None):
            self.assertEqual(config.worker_thread_count(), config.WORKER_THREADS_MIN)

    def test_environment_overrides_calculation(self):
        os.environ["WORKER_THREADS"] = "12"
        with patch("os.cpu_count", return_value=4):
            self.assertEqual(config.worker_thread_count(), 12)

    def test_garbage_in_environment_is_ignored(self):
        with patch("os.cpu_count", return_value=4):
            for raw in ("", "   ", "0", "-5", "много", "8.5"):
                os.environ["WORKER_THREADS"] = raw
                self.assertEqual(config.worker_thread_count(), 16, f"значение {raw!r} не должно применяться")


if __name__ == "__main__":
    unittest.main()
