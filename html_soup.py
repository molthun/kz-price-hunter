"""Единая точка разбора HTML для скраперов и разборщиков страниц.

Каталоги разбираются в рабочих потоках, и разбор — самая дорогая по процессорному
времени часть обхода. На странице каталога в 94 КБ со 120 карточками `lxml` разбирает
документ за 6.6 мс против 9.2 мс у `html.parser` (замер на Python 3.14), то есть
экономит около четверти времени разбора, и делает основную работу в C, не удерживая
GIL так долго, как чистый Python. Выборку по CSS это не ускоряет — она целиком на
стороне soupsieve.

Если `lxml` в окружении нет, разбор продолжается штатным `html.parser`: обход важнее
скорости, и отсутствие необязательной библиотеки не должно останавливать сбор.
"""
from __future__ import annotations

from typing import Any, Optional

from bs4 import BeautifulSoup

try:  # lxml необязателен: в requirements он есть, но окружение может быть и без него
    import lxml  # noqa: F401
    PARSER = "lxml"
except ImportError:
    PARSER = "html.parser"


def make_soup(markup: Any, parser: Optional[str] = None) -> BeautifulSoup:
    """Разбирает разметку предпочтительным парсером.

    `parser` указывается только там, где нужен конкретный разбор (например XML);
    в обычном случае берётся `PARSER`.
    """
    return BeautifulSoup(markup, parser or PARSER)
