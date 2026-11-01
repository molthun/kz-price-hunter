"""Общая основа парсеров: постраничный обход категории до конца выдачи."""
import hashlib
import random
import re
import time
import asyncio
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

# Глубина обхода категории по умолчанию (страниц)
DEFAULT_MAX_PAGES = 50

MAX_PRICE_KZT = 10_000_000
# Явная иная валюта: такую цену не выдаём за тенге
_FOREIGN_CURRENCY = re.compile(r"[$€₽]|\b(?:usd|eur|rub|руб)\b", re.I)
# Разделители разрядов: пробел, неразрывный (U+00A0), узкий неразрывный (U+202F, Flip), тонкий (U+2009)
_THOUSANDS_SEPARATORS = " \u00a0\u202f\u2009"
_PRICE_BLOCK = re.compile(r"(?:\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+|\d+)")
# Скомпилированы один раз: parse_price вызывается на каждой карточке обхода.
_CENTS_OR_FRACTION = re.compile(r"[,.]\d{1,2}(?!\d)")
_NON_DIGITS = re.compile(r"[^\d]")
_INSTALLMENT_AFTER = re.compile(r"(?:₸|тг\.?|тенге)?\s*(?:/|в)\s*мес", re.I)

# Retry только временных сбоев: сеть и HTTP 5xx. 4xx, 429/cooldown и UnconfirmedEnd
# не повторяем — транспорт уже держит паузу по 429, а повтор 403/404 усиливает бан.
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_JITTER_SECONDS = 0.5
_TRANSIENT_EXCEPTION_NAMES = {"Timeout", "ConnectTimeout", "ReadTimeout", "ConnectionError",
                              "CurlError", "SSLError", "ConnectionResetError"}


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
    # Быстрый выход: без цифр в строке цены нет.
    if not any(char.isdigit() for char in text):
        return 0

    # 1. Отсекаем копейки/дробные части вида .00, ,00, .0, ,0 (чтобы 72228.00 или 72228.0 не превращались в 722280)
    text = _CENTS_OR_FRACTION.sub('', text)

    # 2. Поиск блоков чисел, разделенных пробелами (например '179 990 ₸ 200 650 ₸' -> '179 990' и '200 650')
    # Берем первый валидный ценовой блок, а не склеиваем несколько цен в одну
    for match in _PRICE_BLOCK.finditer(text):
        before = text[:match.start()].rstrip()
        after = text[match.end():].lstrip()
        if before.endswith(("-", "−", "–")) or after.startswith("%"):
            continue
        if _INSTALLMENT_AFTER.match(after):
            continue
        val = _in_range(int(_NON_DIGITS.sub("", match.group())))
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


def slug_id(prefix: str, slug: str, max_len: int = 80) -> str:
    """Устойчивый id из slug без обрезки: длинный slug сокращается с хэшем полного значения,
    поэтому товары с общим началом адреса не получают одинаковый id (аудит M07)."""
    slug = (slug or "").strip("/")
    if len(slug) <= max_len:
        return f"{prefix}_{slug}"
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{slug[:max_len - 13]}_{digest}"


def pagination_last_page(html: str, category_url: str, param: str = "page") -> Optional[int]:
    """Наибольший номер страницы этой категории в ссылках пагинации; None, если ссылок нет."""
    from urllib.parse import parse_qs, urljoin
    from html_soup import make_soup
    path = urlsplit(category_url).path.rstrip("/")
    pages = []
    for a in make_soup(html).select("a[href]"):
        link = urlsplit(urljoin(category_url, a["href"]))
        if link.path.rstrip("/") != path:
            continue
        pages += [int(v) for v in parse_qs(link.query).get(param, []) if v.isdigit()]
    return max(pages) if pages else None


class UnconfirmedEnd(RuntimeError):
    """HTTP succeeded, but HTML cannot prove whether the catalog ended."""


class ScanResult(list):
    """List-compatible result with explicit coverage; partial data is still usable.

    unparsed — карточки страницы, которые адаптер видел, но не смог разобрать (сломанный селектор).
    Такая страница не даёт обходу права называться полным (A01).
    """
    def __init__(self, items=(), *, complete=False, error=None, limited=False, unparsed=0):
        super().__init__(items)
        self.complete = complete
        self.error = error
        self.limited = limited
        self.unparsed = unparsed


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

    parts = urlsplit(url)
    if not raw_id or not title or price <= 0 or parts.scheme not in ("http", "https") or not parts.hostname:
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

    Транспорт наследники не создают сами: запросы идут через `scrapers/http.py`
    (curl_cffi), где уже есть лимит соединений на домен, cooldown по 429/503,
    Retry-After и телеметрия. Отдельная сессия с другим пулом обошла бы эти
    ограничения и разошлась бы с общим бюджетом запросов.

    Временные сбои (сеть, HTTP 5xx) повторяются с экспоненциальной задержкой;
    4xx, cooldown и UnconfirmedEnd не повторяются.
    """

    SHOP_NAME = "Магазин"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.3
    RETRY_ATTEMPTS = DEFAULT_RETRY_ATTEMPTS
    RETRY_BASE_DELAY = DEFAULT_RETRY_BASE_DELAY
    RETRY_JITTER_SECONDS = DEFAULT_RETRY_JITTER_SECONDS

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

    def _is_retryable(self, exc: BaseException) -> bool:
        """Повторяем только временный сбой: сеть или HTTP 5xx."""
        from scrapers.http import HostCooldown
        if isinstance(exc, (UnconfirmedEnd, HostCooldown)):
            return False
        status = re.search(r"HTTP\s+(\d{3})", str(exc))
        if status:
            return int(status.group(1)) >= 500
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
            return True
        return type(exc).__name__ in _TRANSIENT_EXCEPTION_NAMES

    def _fetch_page_with_retry(self, category_name: str, category_url: str, page: int) -> List[Dict[str, Any]]:
        """Обёртка над _fetch_page: экспоненциальная пауза только для временных ошибок."""
        for attempt in range(max(1, int(self.RETRY_ATTEMPTS))):
            try:
                return self._fetch_page(category_name, category_url, page)
            except UnconfirmedEnd:
                raise  # конец каталога не подтверждён — повтор бессмыслен
            except Exception as exc:
                if attempt >= self.RETRY_ATTEMPTS - 1 or not self._is_retryable(exc):
                    raise
                delay = self.RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, self.RETRY_JITTER_SECONDS)
                print(f"[{self.SHOP_NAME}] Повтор {attempt + 1}/{self.RETRY_ATTEMPTS - 1} "
                      f"для страницы {page} категории {category_name} "
                      f"({type(exc).__name__}), пауза {delay:.1f}с")
                time.sleep(delay)
        raise RuntimeError("retry loop exhausted")  # недостижимо, защита от пустого RETRY_ATTEMPTS

    def _report_scrape(self, category_name: str, products: List[Dict[str, Any]],
                       pages: int, started: float, outcome: str) -> None:
        duration = time.monotonic() - started
        print(f"[{self.SHOP_NAME}] {category_name}: {len(products)} товаров за {pages} стр. "
              f"({duration:.1f}с) — {outcome}")

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        limit = max_pages if max_pages and max_pages > 0 else DEFAULT_MAX_PAGES
        products: List[Dict[str, Any]] = []
        seen_ids = set()
        pages_scraped = 0
        unparsed = 0  # неразобранные карточки во всей категории (A01)
        started = time.monotonic()

        def _finish_end_of_catalog() -> ScanResult:
            if unparsed:
                # Магазин подтвердил конец, но часть карточек не разобрана: отсутствие товара
                # могло быть ошибкой разбора — обход не полный, сверка ничего не снимет (A01)
                self._report_scrape(category_name, products, pages_scraped, started,
                                    f"конец каталога, но не разобрано карточек: {unparsed}")
                return ScanResult(products, limited=True, unparsed=unparsed)
            self._report_scrape(category_name, products, pages_scraped, started, "завершено")
            return ScanResult(products, complete=True)

        for page in range(1, limit + 1):
            if page > 1 and self.PAGE_DELAY_SECONDS:
                time.sleep(self.PAGE_DELAY_SECONDS)
            try:
                items = self._fetch_page_with_retry(category_name, category_url, page)
                pages_scraped += 1
            except UnconfirmedEnd:
                result = ScanResult(products, limited=bool(products), unparsed=unparsed,
                                    error=None if products else "Карточки не найдены на первой странице")
                self._report_scrape(category_name, products, pages_scraped,
                                    started, "конец каталога не подтверждён")
                return result
            except Exception as e:
                self._report_scrape(category_name, products, pages_scraped,
                                    started, f"ошибка: {type(e).__name__}")
                return ScanResult(products, unparsed=unparsed, error=f"Страница {page}: {type(e).__name__}")

            unparsed += int(getattr(items, "unparsed", 0) or 0)
            if not items:
                if products and getattr(items, "complete", False):
                    return _finish_end_of_catalog()
                self._report_scrape(category_name, products, pages_scraped, started,
                                    "пустая выдача: полнота не подтверждена")
                return ScanResult(products, limited=bool(products), unparsed=unparsed,
                                  error="Пустая выдача: полнота не подтверждена")

            fresh = [i for i in items if i.get("id") and i["id"] not in seen_ids]
            if not fresh:
                # Страница повторяет уже собранные товары — каталог закончился
                self._report_scrape(category_name, products, pages_scraped, started,
                                    "дубликаты, остановка")
                return ScanResult(products, limited=True, unparsed=unparsed)

            seen_ids.update(i["id"] for i in fresh)
            products.extend(fresh)
            if getattr(items, "complete", False):
                return _finish_end_of_catalog()

        self._report_scrape(category_name, products, pages_scraped, started,
                            "достигнут лимит страниц")
        return ScanResult(products, limited=True, unparsed=unparsed)
