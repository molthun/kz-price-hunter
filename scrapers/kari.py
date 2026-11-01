"""Скрапер сети обуви и аксессуаров Kari (kz.kari.com).

Платформа: Next.js, товары страницы лежат в JSON `__NEXT_DATA__`.
Пагинация: ?page=N (по 60 карточек), конец каталога подтверждает `totalPages`.
Цена `price.current` доступна любому покупателю; `price.kari_p` — цена для участников
программы лояльности и в выдачу не попадает.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from scrapers import http as requests
from scrapers.base import (
    PagedScraper, ScanResult, UnconfirmedEnd, price_value, validate_product_item,
)

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class KariScraper(PagedScraper):
    SHOP_NAME = "Kari"
    SHOP_EMOJI = "👟"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 1.0

    def __init__(self):
        self.base_url = "https://kz.kari.com"
        self.session: Optional[requests.Session] = None
        self.headers = {"Accept-Language": "ru-RU,ru;q=0.9", "Referer": "https://kz.kari.com/"}

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    @staticmethod
    def _catalog_state(html: str) -> Dict[str, Any]:
        match = NEXT_DATA_RE.search(html)
        if not match:
            raise RuntimeError("На странице нет данных каталога __NEXT_DATA__")
        state = json.loads(match.group(1))["props"]["initialState"]["products"]
        if not isinstance(state.get("pagination"), dict) or not isinstance(state.get("data"), dict):
            raise ValueError("Неожиданная структура каталога Kari")
        return state

    def _item(self, entity: Dict[str, Any], category_name: str) -> Optional[Dict[str, Any]]:
        articul = str(entity.get("articul") or "").strip()
        price_info = entity.get("price") if isinstance(entity.get("price"), dict) else {}
        price = price_value(price_info.get("current"))
        old_price = price_value(price_info.get("first"))
        title = str(entity.get("title") or "").strip()
        brand = str((entity.get("brand") or {}).get("name") or "").strip()
        if brand and brand.lower() not in title.lower():
            title = f"{brand} {title}".strip()
        return validate_product_item({
            "shop": self.SHOP_NAME,
            "id": f"kari_{articul}" if articul else "",
            "sku": articul,
            "title": title,
            "category": category_name,
            "url": f"{self.base_url}/product/{articul}/",
            "image_url": entity.get("preview") or "",
            "price": price,
            "old_price_on_site": old_price if old_price > price else 0,
            "city": "Алматы / Казахстан",
        }, default_shop=self.SHOP_NAME)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = self.page_url(category_url, page_num)
        resp = self._get_session().get(url, headers=self.headers, timeout=30)
        if resp.status_code == 404 and page_num > 1:
            raise UnconfirmedEnd("HTTP 404 после последней страницы")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} on {url}")

        state = self._catalog_state(resp.text)
        total_pages = int(state["pagination"].get("totalPages") or 0)
        entities = state["data"].get("entities") or {}
        ids = state["data"].get("ids") or []
        if not ids:
            # Пустая страница в пределах totalPages — сбой выдачи, а не конец каталога
            if page_num <= total_pages:
                raise UnconfirmedEnd("пустая страница внутри каталога")
            if total_pages == 0 and page_num == 1:
                return ScanResult([], complete=True)
            raise UnconfirmedEnd("карточки не найдены на следующей странице")

        products = [self._item(entities[i], category_name) for i in ids if isinstance(entities.get(i), dict)]
        return ScanResult([p for p in products if p], complete=page_num >= total_pages)
