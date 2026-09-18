"""iSpace (ispace.kz): официальный Apple Premium Partner.

В сетке категорий цены не отдаются (подгружаются в браузере), поэтому обход идет в два шага:
1. страницы категории `?page=N` дают ссылки на товары (карточки `.entity-card`), конец выдачи —
   пустая страница;
2. карточка товара содержит JSON-LD `Product` с названием, артикулом Apple (sku), ценой,
   наличием и фото. Карточки открываются в несколько потоков.
"""
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests
from scrapers.base import DEFAULT_MAX_PAGES, PagedScraper, ScanResult, parse_price

BASE_URL = "https://ispace.kz"

class ISpaceScraper(PagedScraper):
    SHOP_NAME = "iSpace"
    SHOP_EMOJI = "🍏"
    PAGE_DELAY_SECONDS = 0.3
    PRODUCT_WORKERS = 4

    def _get(self, url: str) -> requests.Response:
        return requests.get(url, impersonate="chrome124", timeout=45, headers={"Accept-Language": "ru-RU,ru;q=0.9"})

    @staticmethod
    def parse_listing(html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.select(".entity-card a.entity-card_name[href]"):
            href = urljoin(BASE_URL, a["href"].split("?")[0])
            if href not in links:
                links.append(href)
        return links

    @classmethod
    def parse_product(cls, html: str, url: str, category_name: str) -> Optional[Dict[str, Any]]:
        """Товар из JSON-LD карточки; None, если его нет в наличии или нет цены."""
        for block in re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            for item in (data if isinstance(data, list) else [data]):
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue
                offers = item.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = parse_price(str(offers.get("price") or ""))
                title = (item.get("name") or "").strip()
                if not title or price <= 0 or "InStock" not in str(offers.get("availability") or ""):
                    return None
                images = item.get("image") or []
                image = images[0] if isinstance(images, list) and images else (images if isinstance(images, str) else "")
                sku = (item.get("sku") or "").strip()
                pid = re.sub(r"[^A-Za-z0-9]", "", sku) or url.rstrip("/").rsplit("/", 1)[-1]
                # Артикул Apple в названии помогает сопоставлять ту же модель в других магазинах
                full_title = f"{title} ({sku})" if sku and sku not in title else title
                return {
                    "shop": cls.SHOP_NAME,
                    "id": f"ispace_{pid}",
                    "title": full_title,
                    "category": category_name,
                    "url": url,
                    "image_url": image,
                    "price": price,
                    "old_price_on_site": 0,
                    "city": "Астана",
                }
        return None

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> ScanResult:
        limit = max_pages if max_pages and max_pages > 0 else DEFAULT_MAX_PAGES
        links: List[str] = []
        listing_complete = False

        for page in range(1, limit + 1):
            if page > 1:
                time.sleep(self.PAGE_DELAY_SECONDS)
            url = category_url if page == 1 else f"{category_url}?page={page}"
            try:
                r = self._get(url)
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {page} категории {category_name}: {e}")
                break
            page_links = self.parse_listing(r.text)
            fresh = [l for l in page_links if l not in links]
            if not page_links:
                listing_complete = page > 1
                break
            if not fresh:
                break
            links.extend(fresh)

        def fetch(link):
            try:
                resp = self._get(link)
                if resp.status_code != 200:
                    return "error"
                return self.parse_product(resp.text, link, category_name)
            except Exception:
                return "error"

        with ThreadPoolExecutor(self.PRODUCT_WORKERS) as pool:
            results = list(pool.map(fetch, links))

        products = [p for p in results if isinstance(p, dict)]
        failed = sum(1 for p in results if p == "error")
        if failed:
            return ScanResult(products, error=f"Не открылись карточки: {failed} из {len(links)}")
        if not links:
            return ScanResult([], error="Карточки не найдены на первой странице")
        return ScanResult(products, complete=listing_complete, limited=not listing_complete)
