"""Технодом: каталог берется из данных Next.js, встроенных в HTML страницы категории.

Раньше страницы открывались через playwright (около 2 минут на цикл). Те же данные
(артикул, название, цена, старая цена, фото) лежат в `__NEXT_DATA__`, поэтому
достаточно обычного HTTP-запроса.
"""
import re
import json
from typing import Any, Dict, List

from scrapers import http as requests
from scrapers.base import ScanResult, PagedScraper, price_value

class TechnodomScraper(PagedScraper):
    SHOP_NAME = "Технодом"
    SHOP_EMOJI = "🔴"
    PAGE_PARAM = "page"

    IMAGE_URL = "https://api.technodom.kz/f3/api/v1/images/272/272/{}.jpg"
    PRODUCT_URL = "https://www.technodom.kz/astana/p/{}"

    def __init__(self):
        self.base_url = "https://www.technodom.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        self.cookies = {"city": "astana", "city_id": "1"}

    @staticmethod
    def _price(value: Any) -> int:
        # 199990.0 — тенге с тиынами, а не 1999900
        return price_value(value)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = self.page_url(category_url, page_num)
        r = requests.get(url, headers=self.headers, cookies=self.cookies, impersonate="chrome124", timeout=30)
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
                "url": self.PRODUCT_URL.format(uri) if uri else f"{self.base_url}/astana/search?text={sku}",
                "image_url": self.IMAGE_URL.format(images[0]) if images else "",
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": "Астана"
            })
        return ScanResult(products, complete=not product_list["items"])
