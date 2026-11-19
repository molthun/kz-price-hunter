"""Технодом: каталог берется из данных Next.js, встроенных в HTML страницы категории.

Раньше страницы открывались через playwright (около 2 минут на цикл). Те же данные
(артикул, название, цена, старая цена, фото) лежат в `__NEXT_DATA__`, поэтому
достаточно обычного HTTP-запроса.
"""
import re
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from scrapers import http as requests
from scrapers.base import ScanResult, PagedScraper, merge_city_results, price_value

class TechnodomScraper(PagedScraper):
    SHOP_NAME = "Технодом"
    SHOP_EMOJI = "🔴"
    PAGE_PARAM = "page"

    IMAGE_URL = "https://api.technodom.kz/f3/api/v1/images/800/800/{}.jpg"
    PRODUCT_URL = "https://www.technodom.kz/{}/p/{}"
    CITY_SLUGS: Dict[str, str] = {
        "Астана": "astana",
        "Алматы": "almaty",
        "Шымкент": "shymkent",
        "Караганда": "karaganda",
    }
    DEFAULT_CITIES: Tuple[str, ...] = ("Астана", "Алматы")

    def __init__(self, cities: Optional[Iterable[str]] = None):
        self.base_url = "https://www.technodom.kz"
        self.cities: Tuple[str, ...] = tuple(cities) if cities is not None else self.DEFAULT_CITIES
        self._active_city: str = self.cities[0] if self.cities else "Астана"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        slug = self.CITY_SLUGS.get(self._active_city, "astana")
        self.cookies = {"city": slug, "city_id": "1"}

    @staticmethod
    def _city_url(category_url: str, slug: str) -> str:
        return re.sub(
            r"(https?://(?:www\.)?technodom\.kz/)(?:astana|almaty|shymkent|karaganda)(/)",
            rf"\g<1>{slug}\g<2>",
            category_url,
            count=1,
        )

    @staticmethod
    def _price(value: Any) -> int:
        # 199990.0 — тенге с тиынами, а не 1999900
        return price_value(value)

    @staticmethod
    def _confirm_city(data: Dict[str, Any], city: str, slug: str) -> None:
        """R1-A01: цена записывается под городом, только если страница сама подтвердила этот город.

        Отсутствие initialState.core.currentCity.slug или чужой город — ошибка разбора.
        """
        state = ((data.get("props") or {}).get("pageProps") or {}).get("initialState") or {}
        current = (state.get("core") or {}).get("currentCity") if isinstance(state, dict) else None
        actual = str(current.get("slug") or "").strip().lower() if isinstance(current, dict) else ""
        if not actual:
            raise ValueError(f"Технодом не подтвердил город «{city}» в ответе")
        if actual != slug:
            raise ValueError(f"Технодом отдал город «{actual}» вместо запрошенного «{slug}»")

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        city = self._active_city
        slug = self.CITY_SLUGS.get(city, "astana")
        url = self.page_url(self._city_url(category_url, slug), page_num)
        cookies = {**self.cookies, "city": slug}
        r = requests.get(url, headers=self.headers, cookies=cookies, impersonate="chrome124", timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")

        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
        if not match:
            print(f"[{self.SHOP_NAME}] Данные каталога не найдены на странице {url}")
            raise ValueError("Не найдены данные каталога Технодом")

        try:
            data = json.loads(match.group(1))
            product_list = data["props"]["pageProps"]["initialState"]["productList"]
        except (KeyError, ValueError) as e:
            print(f"[{self.SHOP_NAME}] Неожиданная структура данных ({e}) на странице {url}")
            raise ValueError("Не найдены данные каталога Технодом")

        if product_list.get("items"):
            self._confirm_city(data, city, slug)

        products: List[Dict[str, Any]] = []
        for item in product_list["items"] or []:
            sku = str(item.get("sku") or "").strip()
            title = (item.get("title") or "").strip()
            price = self._price(item.get("price") or item.get("default_price"))
            if not sku or not title or price <= 0:
                continue

            old_price = self._price(item.get("oldPrice"))
            images = item.get("images") or []
            uri = item.get("uri") or item.get("urlHandle") or ""

            products.append({
                "shop": self.SHOP_NAME,
                "id": f"td_{sku}",
                "title": title,
                "category": category_name,
                "url": self.PRODUCT_URL.format(slug, uri) if uri else f"{self.base_url}/{slug}/search?text={sku}",
                "image_url": self.IMAGE_URL.format(images[0]) if images else "",
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": city,
            })
        return ScanResult(products, complete=not product_list["items"])

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> ScanResult:
        """Обходит категорию по городам из self.cities; complete=True только если все города завершились полностью."""
        cities = self.cities or ("Астана",)
        if len(cities) == 1:
            self._active_city = cities[0]
            return super()._scrape_sync(category_name, category_url, max_pages=max_pages)

        results: List[Tuple[str, Any]] = []
        orig_city = self._active_city
        try:
            for city in cities:
                self._active_city = city
                res = super()._scrape_sync(category_name, category_url, max_pages=max_pages)
                results.append((city, res))
        finally:
            self._active_city = orig_city
        return merge_city_results(results)
