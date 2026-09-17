"""Общая основа парсеров: постраничный обход категории до конца выдачи."""
import re
import time
import asyncio
from typing import Any, Dict, List, Optional

# Глубина обхода категории по умолчанию (страниц)
DEFAULT_MAX_PAGES = 50

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str or "")
    return int(digits) if digits else 0

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
            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {page} категории {category_name}: {e}")
                break

            if not items:
                break

            fresh = [i for i in items if i.get("id") and i["id"] not in seen_ids]
            if not fresh:
                # Страница повторяет уже собранные товары — каталог закончился
                break

            seen_ids.update(i["id"] for i in fresh)
            products.extend(fresh)

        return products
