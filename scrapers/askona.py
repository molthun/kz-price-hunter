"""Адаптер парсера магазина товаров для здорового сна «Askona Казахстан» (askona.kz).

Крупнейший производитель и ритейлер ортопедических и анатомических матрасов,
кроватей, подушек, одеял, диванов и мебели для спальни в Казахстане.
Каталог: SSR HTML с карточками div.card-v6, пагинацией /category/page/N/
и проверкой номеров страниц в пагинаторе .pagination-v3.
Поиск в реальном времени: живой опрос https://askona.kz/?digiSearch=true&term={query}.
"""
import math
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup
from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    parse_price,
    price_value,
    validate_product_item,
)


class AskonaScraper(PagedScraper):
    SHOP_NAME = "Askona"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://askona.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://askona.kz/",
        }
        self.session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def page_url(self, category_url: str, page: int) -> str:
        clean = category_url.rstrip("/")
        if page <= 1:
            return clean + "/"
        return f"{clean}/page/{page}/"

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        sku = card.get("data-cur-sku-id") or card.get("data-id")
        title_el = card.select_one(".card-v6__title")
        if not title_el or not sku:
            return None

        title = re.sub(r"\s+", " ", title_el.get_text(strip=True) or "").strip()
        if not title:
            return None

        href = title_el.get("href") or ""
        if not href:
            a_tag = card.select_one("a[href]")
            href = a_tag.get("href") if a_tag else ""
        if not href:
            return None

        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        # Актуальная цена
        price_el = card.select_one(".card-v6__price-actual")
        if not price_el:
            price_el = card.select_one('[class*="price-actual"]')
        price = parse_price(price_el.text) if price_el else 0
        if not price:
            return None

        # Старая цена (если есть скидка)
        old_price = 0
        old_el = card.select_one(".card-v6__price-old")
        if not old_el:
            old_el = card.select_one('[class*="price-old"]')
        if old_el:
            parsed_old = parse_price(old_el.text)
            if parsed_old > price:
                old_price = parsed_old

        # Изображение
        img_el = card.select_one("img")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-ll-scr-src") or ""
            if raw_src and not raw_src.endswith("logo.svg"):
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        item = {
            "shop": self.SHOP_NAME,
            "id": f"askona_{sku}",
            "sku": str(sku),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "city": "Алматы / Казахстан",
        }
        return validate_product_item(item, default_shop=self.SHOP_NAME)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> ScanResult:
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
            raise UnconfirmedEnd(f"Не удалось получить ответ для страницы {page_num}")

        if resp.status_code == 404:
            if page_num > 1:
                return ScanResult([], complete=True)
            raise UnconfirmedEnd("Категория не найдена (404 на первой странице)")

        if resp.status_code != 200:
            raise UnconfirmedEnd(f"Askona HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("div", class_="card-v6")

        products: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for card in cards:
            validated = self._parse_card(card, category_name)
            if validated and validated["url"] not in seen_urls:
                seen_urls.add(validated["url"])
                products.append(validated)

        if not products:
            if page_num == 1:
                raise UnconfirmedEnd("Товары не найдены на первой странице")
            return ScanResult([], complete=True)

        # Определение завершения каталога по ссылкам пагинатора .pagination-v3
        max_page = None
        for a in soup.select(".pagination-v3 a"):
            t = a.get_text(strip=True)
            if t.isdigit():
                val = int(t)
                if 1 < val < 500:
                    max_page = max(max_page or 0, val)

        is_complete = bool(max_page and page_num >= max_page)
        if not is_complete and len(products) < 20 and page_num > 1:
            is_complete = True

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск по каталогу Askona Казахстан (askona.kz)."""
        url = f"{self.base_url}/?digiSearch=true&term={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all("div", class_="card-v6")
            results: List[Dict[str, Any]] = []
            seen_urls: Set[str] = set()

            for card in cards:
                validated = self._parse_card(card, "Поиск")
                if validated and validated["url"] not in seen_urls:
                    seen_urls.add(validated["url"])
                    results.append(validated)
                    if len(results) >= limit:
                        break
            return results
        except Exception:
            return []
