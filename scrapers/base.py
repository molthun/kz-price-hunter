"""Общая основа парсеров: постраничный обход категории до конца выдачи."""
import re
import time
import asyncio
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# Глубина обхода категории по умолчанию (страниц)
DEFAULT_MAX_PAGES = 50

MAX_PRICE_KZT = 10_000_000
# Явная иная валюта: такую цену не выдаём за тенге
_FOREIGN_CURRENCY = re.compile(r"[$€₽]|\b(?:usd|eur|rub|руб)\b", re.I)
# Разделители разрядов: пробел, неразрывный (U+00A0), узкий неразрывный (U+202F, Flip), тонкий (U+2009)
_THOUSANDS_SEPARATORS = " \u00a0\u202f\u2009"
_PRICE_BLOCK = re.compile(r"(?:\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+|\d+)")


def _in_range(value: int) -> int:
    return value if 0 < value <= MAX_PRICE_KZT else 0


def parse_price(price_str: Any) -> int:
    """Извлекает целочисленную цену в тенге из строки любого формата.
    Защищен от склейки нескольких цен (например '179 990 ₸ 200 650 ₸' -> 179990),
    от копеек/десятичных дробей (например '72 228.00' или '72228.0' -> 72228),
    и от абсурдных выбросов (> 10 000 000 ₸).
    Пропускает блоки, которые не являются полной ценой: скидку «-20 000 ₸», процент «15%»,
    платёж рассрочки «5 990 ₸/мес». Явная иная валюта ($, €, ₽, USD…) даёт 0.
    """
    if price_str is None or isinstance(price_str, bool):
        return 0
    if isinstance(price_str, (int, float, Decimal)):
        return price_value(price_str)
    text = str(price_str).strip()
    if not text or "нет в наличии" in text.lower() or _FOREIGN_CURRENCY.search(text):
        return 0

    # 1. Отсекаем копейки/дробные части вида .00, ,00, .0, ,0 (чтобы 72228.00 или 72228.0 не превращались в 722280)
    text = re.sub(r'[,.]\d{1,2}(?!\d)', '', text)

    # 2. Поиск блоков чисел, разделенных пробелами (например '179 990 ₸ 200 650 ₸' -> '179 990' и '200 650')
    # Берем первый валидный ценовой блок, а не склеиваем несколько цен в одну
    for match in _PRICE_BLOCK.finditer(text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        if before.endswith(("-", "−", "–")) or after.startswith("%"):
            continue
        if re.match(r"(?:₸|тг\.?|тенге)?\s*(?:/|в)\s*мес", after, re.I):
            continue
        val = _in_range(int(re.sub(r"[^\d]", "", match.group())))
        if val:
            return val
    return 0


def price_value(value: Any) -> int:
    """Цена из числового поля JSON/атрибута: 199990, 199990.0, "199990.50", "199 990".

    Дробная часть — тиыны, отбрасывается (а не приписывается к цене). Отрицательное,
    нечисловое, бесконечное или вне диапазона значение даёт 0.
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return _in_range(value)
    if isinstance(value, (float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return 0
    else:
        text = str(value).strip()
        for separator in _THOUSANDS_SEPARATORS:
            text = text.replace(separator, "")
        if not re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return parse_price(value)
        number = Decimal(text.replace(",", "."))
    if not number.is_finite() or number <= 0:
        return 0
    return _in_range(int(number))


class UnconfirmedEnd(RuntimeError):
    """HTTP succeeded, but HTML cannot prove whether the catalog ended."""


class ScanResult(list):
    """List-compatible result with explicit coverage; partial data is still usable."""
    def __init__(self, items=(), *, complete=False, error=None, limited=False):
        super().__init__(items)
        self.complete = complete
        self.error = error
        self.limited = limited


@runtime_checkable
class Scraper(Protocol):
    """Формальный контракт парсера магазина для KZ Price Hunter."""
    SHOP_NAME: str

    async def scrape(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        ...

    def close(self) -> None:
        ...


def validate_product_item(item: Dict[str, Any], default_shop: str = "") -> Optional[Dict[str, Any]]:
    """Проверяет соответствие карточки товара контракту данных.

    Возвращает очищенный словарь или None, если позиция невалидна (нет id, названия,
    некорректная цена <= 0 или битая ссылка).
    """
    if not isinstance(item, dict):
        return None
    raw_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    price = price_value(item.get("price"))

    if not raw_id or not title or price <= 0 or not url.startswith("http"):
        return None

    item["id"] = raw_id
    item["title"] = title
    item["price"] = price
    item["url"] = url
    if not item.get("shop") and default_shop:
        item["shop"] = default_shop
    return item


class PagedScraper:
    """Листает категорию, пока страницы отдают новые товары.

    Наследник реализует `_fetch_page(category_name, category_url, page)` и возвращает
    список товаров со страницы. Обход останавливается, если страница пустая или
    содержит только уже собранные товары (так ведут себя магазины, у которых
    номер страницы выходит за пределы каталога).
    """

    SHOP_NAME = "Магазин"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.3

    def close(self) -> None:
        """Закрывает HTTP-сессию адаптера (curl-хэндлы не ждут сборки мусора) (M09)."""
        for attr in ("_session", "session"):
            session = getattr(self, attr, None)
            if session is not None and hasattr(session, "close"):
                try:
                    session.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    async def scrape(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def page_url(self, category_url: str, page: int) -> str:
        if page <= 1:
            return category_url
        separator = "&" if "?" in category_url else "?"
        return f"{category_url}{separator}{self.PAGE_PARAM}={page}"

    def _fetch_page(self, category_name: str, category_url: str, page: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = max_pages if max_pages and max_pages > 0 else DEFAULT_MAX_PAGES
        products: List[Dict[str, Any]] = []
        seen_ids = set()

        for page in range(1, limit + 1):
            if page > 1 and self.PAGE_DELAY_SECONDS:
                time.sleep(self.PAGE_DELAY_SECONDS)
            try:
                items = self._fetch_page(category_name, category_url, page)
            except UnconfirmedEnd:
                return ScanResult(products, limited=bool(products),
                                  error=None if products else "Карточки не найдены на первой странице")
            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {page} категории {category_name}: {e}")
                return ScanResult(products, error=f"Страница {page}: {type(e).__name__}")

            if not items:
                complete = bool(products) and getattr(items, "complete", False)
                return ScanResult(products, complete=complete,
                                  error=None if complete else "Пустая выдача: полнота не подтверждена")

            fresh = [i for i in items if i.get("id") and i["id"] not in seen_ids]
            if not fresh:
                # Страница повторяет уже собранные товары — каталог закончился
                return ScanResult(products, limited=True)

            seen_ids.update(i["id"] for i in fresh)
            products.extend(fresh)
            if getattr(items, "complete", False):
                return ScanResult(products, complete=True)

        return ScanResult(products, limited=True)
