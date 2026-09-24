"""Скрапер агрегатора аптек и аптечных сетей Казахстана «i-Teka» (i-teka.kz).

Агрегирует предложения всех ключевых аптек Казахстана: Europharma, Биосфера, Садыхан, Рауза-АДЕ, Аптека плюс.
Платформа: SSR HTML на базе Yii2 + Alpine.js.
Селектор карточек: div.rounded-16 (содержащие a[href*="/medicaments/"]).
Пагинация: ?page=N.
Определение последней страницы: через ссылки пагинатора с параметром GlossaryTnfull_page или page.
Поддерживается моментальный живой опрос поиска через /search?query=.
"""
from __future__ import annotations

import re
import time
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urljoin, quote_plus
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import (
    PagedScraper, price_value, validate_product_item,
    UnconfirmedEnd, ScanResult, pagination_last_page
)


class ITekaScraper(PagedScraper):
    SHOP_NAME = "i-Teka"
    SHOP_EMOJI = "💊"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, city: str = "Астана"):
        self.base_url = "https://i-teka.kz"
        self.city_display = city or "Астана"
        self.city_slug = "almaty" if "алмат" in self.city_display.lower() else "astana"
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
            "Referer": "https://i-teka.kz/",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _extract_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        a_link = card.select_one("a[href*='/medicaments/']")
        if not a_link:
            return None
        href = a_link.get("href") or ""
        if not href:
            return None
        product_url = urljoin(self.base_url, href)

        # ID товара из Alpine x-data или slug URL
        pid = ""
        x_data_el = card.select_one("[x-data*='drugId']")
        x_data_str = x_data_el.get("x-data") if x_data_el else ""
        if x_data_str:
            m_id = re.search(r"drugId:\s*'(\d+)'", x_data_str) or re.search(r"drugId:\s*(\d+)", x_data_str)
            if m_id:
                pid = m_id.group(1)
        if not pid:
            pid = href.rstrip("/").split("/")[-1]

        # Название товара
        title_el = card.select_one(".body-md.font-bold a, a.hover\\:text-primary-80")
        raw_title = title_el.text if title_el else a_link.text
        title = re.sub(r"\s+", " ", raw_title or "").strip()
        if not title:
            img_alt = card.select_one("img[alt]")
            title = re.sub(r"\s+", " ", (img_alt.get("alt") or "")).strip() if img_alt else ""
        if not title:
            return None

        # Актуальная цена
        price = 0
        if x_data_str:
            m_price = re.search(r"drugPrice:\s*(\d+)", x_data_str)
            if m_price:
                price = int(m_price.group(1))
        if not price:
            price_el = card.select_one(".label-block, span.body-md.font-semibold")
            price = price_value(price_el.text) if price_el else 0

        # Старая цена (если товар со скидкой)
        old_price = 0
        old_el = card.select_one(".line-through, strike, del, s")
        if old_el:
            parsed_old = price_value(old_el.text)
            if parsed_old > price:
                old_price = parsed_old

        # Фото товара
        img_el = card.select_one("img")
        img_src = ""
        if img_el:
            img_src = (img_el.get("data-src") or img_el.get("src") or "").strip()

        item = {
            "shop": self.SHOP_NAME,
            "id": f"iteka_{pid}",
            "sku": str(pid),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "city": self.city_display,
        }
        return validate_product_item(item, default_shop=self.SHOP_NAME)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        # Подставляем выбранный город в URL категории, если он указан в пути
        effective_url = category_url
        if "/astana/" in category_url and self.city_slug != "astana":
            effective_url = category_url.replace("/astana/", f"/{self.city_slug}/")

        url = self.page_url(effective_url, page_num)
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
        med_links = soup.select("a[href*='/medicaments/']")
        if not med_links:
            if page_num > 1:
                raise UnconfirmedEnd("карточки лекарств не найдены на следующей странице")
            raise RuntimeError(f"Карточки товаров не найдены на первой странице {url}")

        # Определение последней страницы (поддерживаем оба параметра в ссылках пагинатора)
        last_page = pagination_last_page(resp.text, effective_url, param="GlossaryTnfull_page")
        if not last_page:
            last_page = pagination_last_page(resp.text, effective_url, param=self.PAGE_PARAM)

        products: List[Dict[str, Any]] = []
        seen_cards: Set[int] = set()

        for a in med_links:
            card = a.find_parent("div", class_="rounded-16")
            if not card or id(card) in seen_cards:
                continue
            seen_cards.add(id(card))

            validated = self._extract_card(card, category_name)
            if validated:
                products.append(validated)

        is_complete = bool(last_page and page_num >= last_page)
        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой опрос поиска портала i-Teka (/search?query=)."""
        url = f"{self.base_url}/{self.city_slug}/search?query={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            med_links = soup.select("a[href*='/medicaments/']")
            results: List[Dict[str, Any]] = []
            seen_cards: Set[int] = set()
            for a in med_links:
                card = a.find_parent("div", class_="rounded-16")
                if not card or id(card) in seen_cards:
                    continue
                seen_cards.add(id(card))
                validated = self._extract_card(card, "Поиск")
                if validated:
                    results.append(validated)
                    if len(results) >= limit:
                        break
            return results
        except Exception:
            return []
