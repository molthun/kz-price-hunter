"""Скрапер сети магазинов книг, игрушек, гейминга и подарков «Меломан / MARWIN» (marwin.kz).

Платформа: Magento 2 (SSR HTML).
Селектор карточек: .product-item-info (внутри li.product-item).
Пагинация: ?p=N.
Конец каталога: подтверждается номером последней страницы в пагинации (pagination_last_page).
Поддерживается прямой живой поиск через /catalogsearch/result/?q=.
"""
from __future__ import annotations

import time
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import (
    PagedScraper, price_value, validate_product_item,
    UnconfirmedEnd, ScanResult, pagination_last_page
)


class MarwinScraper(PagedScraper):
    SHOP_NAME = "Меломан"
    SHOP_EMOJI = "📚"
    PAGE_PARAM = "p"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://www.marwin.kz"
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
            "Referer": "https://www.marwin.kz/",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _extract_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        pid = card.get("data-product-id") or card.get("data-pid")
        a_link = card.select_one("a.product-item-link")
        if not a_link:
            a_link = card.select_one("a.product-item-photo, a[href]")
        if not a_link:
            return None
        href = a_link.get("href") or ""
        if not href or href.startswith("#") or "javascript:" in href:
            return None
        product_url = urljoin(self.base_url, href)

        if not pid:
            # Fallback к извлечению числового ID из data-role/data-price-box или ссылки
            price_box = card.select_one("[data-price-box*='product-id-']")
            if price_box:
                pbox_val = price_box.get("data-price-box") or ""
                pid = pbox_val.replace("product-id-", "").strip()
            if not pid:
                pid = a_link.get("data-id-product") or ""
            if not pid:
                pid = str(abs(hash(product_url)))[:10]

        title = (
            (a_link.get("data-product-name") or "").strip()
            or (a_link.get("title") or "").strip()
            or a_link.text.strip()
        )
        if not title:
            name_el = card.select_one(".product-item-name, .name")
            if name_el:
                title = name_el.text.strip()
        if not title:
            img_alt = card.select_one("img.product-image-photo[alt], img[alt]")
            title = (img_alt.get("alt") or "").strip() if img_alt else ""
        if not title:
            return None

        # Актуальная цена
        final_wrap = card.select_one("[data-price-type='finalPrice']")
        price = 0
        if final_wrap and final_wrap.get("data-price-amount"):
            price = price_value(final_wrap.get("data-price-amount"))
        elif final_wrap:
            price = price_value(final_wrap.text)
        else:
            price_el = card.select_one(".special-price .price, .price-box .price, .price")
            price = price_value(price_el.text) if price_el else 0

        # Старая зачеркнутая цена
        old_wrap = card.select_one("[data-price-type='oldPrice'], .old-price .price")
        old_price = 0
        if old_wrap and old_wrap.get("data-price-amount"):
            old_price = price_value(old_wrap.get("data-price-amount"))
        elif old_wrap:
            old_price = price_value(old_wrap.text)

        # Фото товара (игнорируем стикеры и бэйджи astrio_specialproducts)
        img_el = card.select_one("img.product-image-photo, .product-image-container img")
        if not img_el:
            img_el = card.select_one("img")
        img_src = ""
        if img_el:
            img_src = (img_el.get("data-src") or img_el.get("src") or "").strip()
            if img_src.endswith(".svg") or "specialproducts" in img_src:
                # Поищем реальное фото среди остальных картинок
                for other_img in card.select("img"):
                    src = (other_img.get("data-src") or other_img.get("src") or "").strip()
                    if src and not src.endswith(".svg") and "specialproducts" not in src:
                        img_src = src
                        break

        item = {
            "shop": self.SHOP_NAME,
            "id": f"marwin_{pid}",
            "sku": str(pid),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price if old_price > price else 0,
            "city": "Алматы / Казахстан",
        }
        return validate_product_item(item, default_shop=self.SHOP_NAME)

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
        cards = soup.select(".product-item-info")
        if not cards:
            if page_num > 1:
                raise UnconfirmedEnd("карточки не найдены на следующей странице")
            raise RuntimeError(f"Карточки товаров не найдены на первой странице {url}")

        last_page = pagination_last_page(resp.text, category_url, param=self.PAGE_PARAM)

        products: List[Dict[str, Any]] = []
        for card in cards:
            validated = self._extract_card(card, category_name)
            if validated:
                products.append(validated)

        is_complete = bool(last_page and page_num >= last_page)
        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск товаров через форму поиска Magento (/catalogsearch/result/?q=)."""
        url = f"{self.base_url}/catalogsearch/result/?q={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".product-item-info")
            results: List[Dict[str, Any]] = []
            for card in cards:
                validated = self._extract_card(card, "Поиск")
                if validated:
                    results.append(validated)
                    if len(results) >= limit:
                        break
            return results
        except Exception:
            return []
