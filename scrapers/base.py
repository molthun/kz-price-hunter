"""Общая основа парсеров: постраничный обход категории до конца выдачи."""
import re
import time
import asyncio
from typing import Any, Dict, List, Optional

# Глубина обхода категории по умолчанию (страниц)
DEFAULT_MAX_PAGES = 50

def parse_price(price_str: Any) -> int:
    """Извлекает целочисленную цену в тенге из строки любого формата.
    Защищен от склейки нескольких цен (например '179 990 ₸ 200 650 ₸' -> 179990),
    от копеек/десятичных дробей (например '72 228.00' или '72228.0' -> 72228),
    и от абсурдных выбросов (> 10 000 000 ₸).
    """
    if not price_str:
        return 0
    text = str(price_str).strip()
    if not text or "нет в наличии" in text.lower():
        return 0

    # 1. Отсекаем копейки/дробные части вида .00, ,00, .0, ,0 (чтобы 72228.00 или 72228.0 не превращались в 722280)
    text = re.sub(r'[,.]\d{1,2}(?!\d)', '', text)

    # 2. Поиск блоков чисел, разделенных пробелами (например '179 990 ₸ 200 650 ₸' -> '179 990' и '200 650')
    # Берем первый валидный ценовой блок, а не склеиваем несколько цен в одну
    blocks = re.findall(r'(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)', text)
    if blocks:
        for block in blocks:
            digits = re.sub(r'[^\d]', '', block)
            if digits:
                val = int(digits)
                if 0 < val <= 10_000_000:
                    return val

    digits = re.sub(r'[^\d]', '', text)
    if digits:
        val = int(digits)
        if 0 < val <= 10_000_000:
            return val
    return 0

class UnconfirmedEnd(RuntimeError):
    """HTTP succeeded, but HTML cannot prove whether the catalog ended."""


class ScanResult(list):
    """List-compatible result with explicit coverage; partial data is still usable."""
    def __init__(self, items=(), *, complete=False, error=None, limited=False):
        super().__init__(items)
        self.complete = complete
        self.error = error
        self.limited = limited


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
