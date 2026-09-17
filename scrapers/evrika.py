"""Эврика: каталог берется из JSON-ответа back.evrika.com (тот же источник, что и у сайта).

HTML-страницы отдают только первую страницу категории, а этот ответ содержит
общее число товаров и корректно листается параметром `page`.
"""
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

from curl_cffi import requests
from bs4 import BeautifulSoup
from scrapers.base import ScanResult, PagedScraper, parse_price

class EvrikaScraper(PagedScraper):
    SHOP_NAME = "Эврика"
    SHOP_EMOJI = "🔷"
    PAGE_PARAM = "page"

    API_HOST = "https://back.evrika.com"
    CITY_SLUG = "nur-sultan-astana"

    def __init__(self):
        self.base_url = "https://evrika.com"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _api_url(self, category_url: str, page: int) -> str:
        path = urlparse(category_url).path if category_url.startswith("http") else category_url
        # Гарантируем город Астана в пути каталога
        if f"/catalog/{self.CITY_SLUG}/" not in path:
            path = path.replace("/catalog/", f"/catalog/{self.CITY_SLUG}/", 1)
        path = path.rstrip("/") + "/"
        return f"{self.API_HOST}{path}?page={page}"

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = self._api_url(category_url, page_num)
        r = requests.get(url, headers=self.headers, impersonate="chrome124", timeout=30, verify=False)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json()
        if not isinstance(data, dict) or "products" not in data:
            raise ValueError("Неожиданная структура каталога Эврика")

        soup = BeautifulSoup(data.get("products") or "", "html.parser")
        products: List[Dict[str, Any]] = []

        for tile in soup.select(".goods-tile"):
            raw = tile.get("data-json") or ""
            link = tile.select_one('a[href*="/p"]')
            if not link:
                continue

            href = link.get("href", "")
            pid_match = re.search(r"/p(\d+)", href)
            if not pid_match:
                continue
            pid = pid_match.group(1)

            name_match = re.search(r"'name':\s*'(.*?)',\s*\n", raw, re.S)
            title = (name_match.group(1) if name_match else link.get_text(" ", strip=True)).strip()
            if not title:
                continue

            price_match = re.search(r"'price':\s*'?(\d+)", raw)
            price = int(price_match.group(1)) if price_match else parse_price(tile.get_text(" ", strip=True))
            if price <= 0:
                continue

            image_url = ""
            img = tile.find("img")
            if img:
                for attr in ("data-original", "data-src", "data-lazy", "src"):
                    src = img.get(attr) or ""
                    if src and "empty.gif" not in src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"
                        break

            products.append({
                "shop": self.SHOP_NAME,
                "id": f"evrika_{pid}",
                "title": title,
                "category": category_name,
                "url": href.replace(self.API_HOST, self.base_url),
                "image_url": image_url,
                "price": price,
                "old_price_on_site": 0,
                "city": "Астана"
            })
        return ScanResult(products, complete=not data["products"])
