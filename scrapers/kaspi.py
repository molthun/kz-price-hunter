"""Kaspi Магазин: каталог берется из того же JSON, что использует сам сайт.

Раньше страницы открывались через playwright и отдавали около 70 товаров на весь магазин.
Эндпоинт `/yml/product-view/pl/results` листается как угодно глубоко (12 товаров на страницу)
и отдает цену, ссылку, фото и остаток.
"""
import re
import asyncio
import urllib.parse
from typing import Any, Dict, List, Optional

from scrapers import http as requests
from scrapers.base import ScanResult, PagedScraper, price_value

class KaspiScraper(PagedScraper):
    SHOP_NAME = "Kaspi Магазин"
    SHOP_EMOJI = "🔴"
    PAGE_DELAY_SECONDS = 0.4

    API_URL = "https://kaspi.kz/yml/product-view/pl/results"
    CITY_CODE = "710000000"  # Астана

    def __init__(self, city_code="710000000"):
        from config import CITIES_KZ
        self.CITY_CODE = str(city_code)
        self.city_name = next((c["name"] for c in CITIES_KZ.values() if c["kaspi_code"] == self.CITY_CODE), "Астана")
        self.base_url = "https://kaspi.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-KS-City": self.CITY_CODE,
        }

    @staticmethod
    def _category_code(category_url: str) -> str:
        """Код категории для запроса: /shop/c/smart%20watches/ -> smart watches."""
        path = urllib.parse.urlparse(category_url).path if category_url.startswith("http") else category_url
        slug = path.rstrip("/").split("/c/")[-1]
        return urllib.parse.unquote(slug)

    @staticmethod
    def _cards(payload: Any) -> List[Dict[str, Any]]:
        """Kaspi отдает карточки либо списком в `data`, либо внутри `data.cards`."""
        data = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict) and c.get("id")]
        if isinstance(data, dict):
            return [c for c in (data.get("cards") or []) if isinstance(c, dict) and c.get("id")]
        return []

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        code = self._category_code(category_url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(category_url).query).get("text", [""])[0]
        params = {
            "page": page_num - 1,  # у Kaspi нумерация страниц с нуля
            "q": "" if query else f":category:{code}",
            "text": query,
            "sort": "relevance",
            "qs": "",
            "ui": "d",
            "i": "-1",
            "c": self.CITY_CODE,
        }
        headers = dict(self.headers, Referer=category_url)
        r = requests.get(self.API_URL, params=params, headers=headers, impersonate="chrome124", timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        payload = r.json()
        data = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(data, list) and not (isinstance(data, dict) and isinstance(data.get("cards"), list)):
            raise ValueError("Неожиданная структура каталога Kaspi")
        cards = self._cards(payload)

        products: List[Dict[str, Any]] = []
        for card in cards:
            title = (card.get("title") or "").strip()
            price = price_value(card.get("unitSalePrice") or card.get("unitPrice"))
            if not title or price <= 0:
                continue

            # Товары не в наличии в выбранном городе цены не показывают
            if card.get("stock") is not None and int(card.get("stock") or 0) <= 0:
                continue

            base_price = price_value(card.get("unitPrice"))
            link = card.get("shopLink") or ""
            if link:
                if link.startswith("/p/"):
                    link = f"{self.base_url}/shop{link}"
                elif link.startswith("/shop/"):
                    link = f"{self.base_url}{link}"
                elif link.startswith("/"):
                    link = f"{self.base_url}/shop{link}"
                elif "kaspi.kz/p/" in link and "kaspi.kz/shop/p/" not in link:
                    link = link.replace("kaspi.kz/p/", "kaspi.kz/shop/p/")
                elif not link.startswith("http"):
                    link = f"{self.base_url}/shop/{link.lstrip('/')}"

            images = card.get("previewImages") or []
            image_url = ""
            if images and isinstance(images[0], dict):
                image_url = images[0].get("medium") or images[0].get("large") or images[0].get("small") or ""

            categories = card.get("categoryRu") or card.get("category") or []
            category = categories[-1] if categories else category_name

            products.append({
                "shop": self.SHOP_NAME,
                "id": f"kaspi_{card['id']}",
                "title": title,
                "category": category or category_name,
                "url": link or f"{self.base_url}/shop/search/?text={urllib.parse.quote(title)}",
                "image_url": image_url,
                "price": price,
                "old_price_on_site": base_price if base_price > price else 0,
                "city": self.city_name
            })
        return ScanResult(products, complete=not cards)


    async def search(self, query: str, max_items: int = 15):
        if not query.strip() or max_items <= 0:
            return []
        url = f"{self.base_url}/shop/search/?text={urllib.parse.quote(query)}"
        items, seen = [], set()
        for page in range(1, (max_items + 11) // 12 + 1):
            batch = await asyncio.to_thread(self._fetch_page, "Поиск", url, page)
            fresh = [p for p in batch if p["id"] not in seen]
            if not fresh:
                break
            items.extend(fresh)
            seen.update(p["id"] for p in fresh)
            if len(items) >= max_items or getattr(batch, "complete", False):
                break
            await asyncio.sleep(self.PAGE_DELAY_SECONDS)
        return items[:max_items]

    async def search_live(self, query: str, city: Optional[str] = None, max_items: int = 15):
        """Unified live-search contract alias for search_engine."""
        return await self.search(query, max_items=max_items)
