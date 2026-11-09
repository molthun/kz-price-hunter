"""Wildberries Казахстан (wildberries.kz) scraper.

Поддерживает парсинг каталога и поисковой выдачи через шлюз API WB
с региональными складами Казахстана (dest=-1257786) и ценами в тенге (curr=kzt).
Каждый оффер содержит данные о фактическом продавце (supplier, supplierId)
для привязки к модели продавцов и каналам маркетплейса.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from offer_identity import NATIONWIDE
from scrapers import http as requests
from scrapers.base import (
    PagedScraper, ScanResult, price_value, validate_product_item,
)
from scrapers.showcase_scanners import wb_image_url


class WildberriesScraper(PagedScraper):
    SHOP_NAME = "Wildberries KZ"
    SHOP_EMOJI = "🟣"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.5

    # Казахстанский региональный склад / хаб доставки
    DEFAULT_DEST = "-1257786"
    SEARCH_API_URL = "https://search.wb.ru/exactmatch/ru/common/v7/search"

    def __init__(self, dest: str = DEFAULT_DEST):
        self.dest = dest
        self.base_url = "https://www.wildberries.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.wildberries.kz/",
            "Origin": "https://www.wildberries.kz",
        }

    @staticmethod
    def extract_query(category_url: str) -> str:
        """Извлекает поисковый запрос или категорию из ссылки."""
        parsed = urllib.parse.urlparse(category_url)
        qs = urllib.parse.parse_qs(parsed.query)
        if "search" in qs and qs["search"]:
            return qs["search"][0].strip()
        if "query" in qs and qs["query"]:
            return qs["query"][0].strip()
        # Попытка извлечь слаг из пути (например /catalog/elektronika/smartfony)
        path = parsed.path.strip("/").split("/")
        if len(path) > 1 and path[0] == "catalog":
            return path[-1].replace("-", " ")
        return ""

    def _fetch_page(self, category_name: str, category_url: str, page: int) -> List[Dict[str, Any]]:
        query = self.extract_query(category_url) or category_name
        params = {
            "curr": "kzt",
            "dest": self.dest,
            "query": query,
            "page": page,
            "resultset": "catalog",
            "sort": "popular",
            "spp": "30",
        }
        resp = requests.get(self.SEARCH_API_URL, params=params, headers=self.headers, timeout=25)
        if resp.status_code == 403:
            raise RuntimeError(f"WB API returned status 403 (wbaas protection active)")
        if resp.status_code != 200:
            raise RuntimeError(f"WB API returned status {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as e:
            raise ValueError(f"Failed to decode WB JSON: {e}")

        return self.parse_payload(payload, category_name, category_url, page)

    @classmethod
    def parse_payload(cls, payload: Dict[str, Any], category_name: str, category_url: str, page: int) -> ScanResult:
        if not isinstance(payload, dict) or "data" not in payload or not isinstance(payload.get("data"), dict) or "products" not in payload.get("data", {}):
            from scrapers.base import UnconfirmedEnd
            raise UnconfirmedEnd("Неизвестная структура ответа Wildberries: отсутствует data.products")

        data = payload["data"]
        products = data.get("products")
        if not isinstance(products, list):
            from scrapers.base import UnconfirmedEnd
            raise UnconfirmedEnd("Неизвестная структура ответа Wildberries: products не является списком")

        items: List[Dict[str, Any]] = []
        unparsed = 0
        for p in products:
            if not isinstance(p, dict):
                unparsed += 1
                continue
            wb_id = p.get("id")
            name = (p.get("name") or "").strip()
            brand = (p.get("brand") or "").strip()
            title = f"{brand} {name}".strip() if brand and brand.lower() not in name.lower() else name
            if not wb_id or not title:
                unparsed += 1
                continue

            sale_price = 0
            base_price = 0
            sizes = p.get("sizes") or []
            if sizes and isinstance(sizes[0], dict) and isinstance(sizes[0].get("price"), dict):
                pr_obj = sizes[0]["price"]
                sale_price = price_value(pr_obj.get("total", 0) / 100)
                base_price = price_value(pr_obj.get("basic", 0) / 100)
            if sale_price <= 0:
                sale_price = price_value(p.get("salePriceU", 0) / 100)
                base_price = price_value(p.get("priceU", 0) / 100)

            if sale_price <= 0:
                unparsed += 1
                continue

            supplier_name = (p.get("supplier") or brand or "").strip()
            supplier_id = str(p.get("supplierId") or "")

            item = {
                "id": f"wb_{wb_id}",
                "shop": cls.SHOP_NAME,
                "title": title,
                "category": category_name,
                "url": f"https://www.wildberries.kz/catalog/{wb_id}/detail.aspx",
                "image_url": wb_image_url(wb_id),
                "price": sale_price,
                "old_price_on_site": base_price if base_price > sale_price else 0,
                "city": NATIONWIDE,
                "seller": supplier_name,
                "seller_id": f"wildberries:{supplier_id}" if supplier_id else None,
                "marketplace": "wildberries",
                "external_sku": str(wb_id),
                "rating": float(p.get("reviewRating", 0)) if p.get("reviewRating") else None,
                "reviews_count": int(p.get("feedbacks", 0)) if p.get("feedbacks") else None,
            }
            validated = validate_product_item(item, cls.SHOP_NAME)
            if validated:
                items.append(validated)
            else:
                unparsed += 1

        is_last = (len(products) < 20) or not products
        complete = is_last and (unparsed == 0)
        return ScanResult(items, complete=complete, unparsed=unparsed)
