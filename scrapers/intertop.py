"""Скрапер мультибрендовой сети обуви и одежды «Интертоп» (intertop.kz).

Платформа: SSR HTML на базе Vue/Nuxt.
Селектор карточек: .in-product-tile.
Пагинация: ?page=N (по 48 карточек на страницу).
Конец каталога: подтверждается номером последней страницы в пагинации (pagination_last_page).
"""
from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import (
    PagedScraper, price_value, validate_product_item,
    UnconfirmedEnd, ScanResult, pagination_last_page
)


class IntertopScraper(PagedScraper):
    SHOP_NAME = "Интертоп"
    SHOP_EMOJI = "👞"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://intertop.kz"
        self.session: Optional[requests.Session] = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://intertop.kz/",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = self.page_url(category_url, page_num)
        session = self._get_session()

        resp = None
        for attempt in range(2):
            try:
                resp = session.get(url, headers=self.headers, timeout=20)
                if resp.status_code in (500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0)
                    continue
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise

        if resp is None:
            raise RuntimeError(f"Не удалось получить ответ от {url}")

        if resp.status_code == 404:
            if page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней страницы")
            raise RuntimeError(f"HTTP 404 on {url}")

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} on {url}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".in-product-tile")
        if not cards:
            if page_num > 1:
                raise UnconfirmedEnd("карточки не найдены на следующей странице")
            raise RuntimeError(f"Карточки товаров не найдены на первой странице {url}")

        last_page = pagination_last_page(resp.text, category_url, param=self.PAGE_PARAM)

        products: List[Dict[str, Any]] = []
        for card in cards:
            pid = card.get("data-product-id")
            if not pid:
                continue

            sku = card.get("data-product-sku") or pid

            a_el = card.select_one("a[href]")
            if not a_el:
                continue
            href = a_el.get("href") or ""
            product_url = urljoin(self.base_url, href)

            brand_el = card.select_one(".in-product-tile__product-brand")
            name_el = card.select_one(".in-product-tile__product-name")
            brand = brand_el.text.strip() if brand_el else ""
            name = name_el.text.strip() if name_el else ""
            title = f"{brand} {name}".strip()
            if not title:
                img_el = card.select_one("img[alt]")
                alt = (img_el.get("alt") or "") if img_el else ""
                title = alt.replace("Фото", "").strip()

            actual_el = card.select_one(".in-price__actual")
            regular_el = card.select_one(".in-price__regular")
            if actual_el:
                price = price_value(actual_el.text)
            else:
                price_box = card.select_one(".in-price")
                price = price_value(price_box.text) if price_box else 0

            old_price = price_value(regular_el.text) if regular_el else 0

            img = card.select_one("img")
            img_src = (img.get("src") or img.get("data-src") or "") if img else ""

            item = {
                "shop": self.SHOP_NAME,
                "id": f"intertop_{pid}",
                "sku": sku,
                "title": title,
                "category": category_name,
                "url": product_url,
                "image_url": img_src,
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": "Алматы / Казахстан",
            }

            validated = validate_product_item(item, default_shop=self.SHOP_NAME)
            if validated:
                products.append(validated)

        is_complete = bool(last_page and page_num >= last_page)
        return ScanResult(products, complete=is_complete)
