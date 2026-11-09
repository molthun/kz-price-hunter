"""Ozon Казахстан (ozon.kz) scraper.

Поддерживает парсинг каталога и поисковой выдачи с ценами в тенге (KZT).
Офферы связываются с продавцами маркетплейса (seller, seller_id)
для интеграции в кросс-канальную матрицу цен.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from domain.image_quality import upgrade_image_url
from offer_identity import NATIONWIDE
from scrapers import http as requests
from scrapers.base import (
    PagedScraper, ScanResult, price_value, validate_product_item,
)


class OzonScraper(PagedScraper):
    SHOP_NAME = "Ozon KZ"
    SHOP_EMOJI = "🔵"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.5

    API_URL = "https://api.ozon.kz/composer-api.bx/page/json/v2"

    def __init__(self):
        self.base_url = "https://ozon.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://ozon.kz/",
            "Origin": "https://ozon.kz",
        }

    @staticmethod
    def extract_search_path(category_url: str, page: int) -> str:
        """Формирует относительный путь к поиску или категории Ozon с пагинацией."""
        parsed = urllib.parse.urlparse(category_url)
        path = parsed.path or "/search/"
        qs = urllib.parse.parse_qs(parsed.query)
        qs["page"] = [str(page)]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return f"{path}?{new_query}"

    def _fetch_page(self, category_name: str, category_url: str, page: int) -> List[Dict[str, Any]]:
        search_path = self.extract_search_path(category_url, page)
        resp = requests.get(self.API_URL, params={"url": search_path}, headers=self.headers, timeout=25)
        if resp.status_code != 200:
            raise RuntimeError(f"Ozon API returned status {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as e:
            raise ValueError(f"Failed to decode Ozon JSON: {e}")

        return self.parse_payload(payload, category_name, category_url, page)

    @classmethod
    def parse_payload(cls, payload: Dict[str, Any], category_name: str, category_url: str, page: int) -> ScanResult:
        widget_states = payload.get("widgetStates") or {}
        items_data = []

        for k, v in widget_states.items():
            if "searchResults" in k or "megaPaginator" in k or "catalog" in k:
                try:
                    state_obj = json.loads(v) if isinstance(v, str) else v
                    sub_items = state_obj.get("items") or []
                    if sub_items:
                        items_data.extend(sub_items)
                except Exception:
                    continue

        items: List[Dict[str, Any]] = []
        for it in items_data:
            if not isinstance(it, dict):
                continue
            sku = str(it.get("id") or it.get("sku") or "")
            tracking = it.get("cellTrackingInfo") or {}
            title = (tracking.get("title") or it.get("title") or "").strip()
            price_val = price_value(tracking.get("price") or it.get("price"))
            old_price_val = price_value(tracking.get("originalPrice") or it.get("oldPrice") or it.get("old_price"))
            if not sku or not title or price_val <= 0:
                continue

            link = it.get("action", {}).get("link") or f"/product/{sku}"
            if not link.startswith("http"):
                link = f"https://ozon.kz{link}"

            img_url = it.get("tileImage", {}).get("image", {}).get("link") or it.get("image") or ""
            img_url = upgrade_image_url(img_url)

            # Продавец
            seller_name = tracking.get("sellerName") or it.get("sellerName") or ""
            seller_id = str(tracking.get("sellerId") or it.get("sellerId") or "")

            item = {
                "id": f"ozon_{sku}",
                "shop": cls.SHOP_NAME,
                "title": title,
                "category": category_name,
                "url": link,
                "image_url": img_url,
                "price": price_val,
                "old_price_on_site": old_price_val if old_price_val > price_val else 0,
                "city": NATIONWIDE,
                "seller": seller_name or cls.SHOP_NAME,
                "seller_id": f"ozon:{seller_id}" if seller_id else None,
                "marketplace": "ozon",
                "external_sku": sku,
            }
            validated = validate_product_item(item, cls.SHOP_NAME)
            if validated:
                items.append(validated)

        is_last = (len(items_data) < 12) or not items_data
        return ScanResult(items, complete=is_last)
