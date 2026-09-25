"""Адаптер парсера сети магазинов электроники и бытовой техники «Планета Электроники» (planeta.kz).

Одна из старейших и крупнейших сетей бытовой техники и электроники в Казахстане.
Широкий ассортимент смартфонов, ноутбуков, телевизоров, крупной и мелкой бытовой техники,
климатического оборудования и аудио.
Каталог: SSR HTML с карточками .unit-item-block, кодами товаров .code, актуальными
ценами .price-block и пагинацией /ru/site/search/term/{term}/page/{page}/.
Поиск в реальном времени: https://planeta.kz/ru/site/search/?term={query}.
"""
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote, unquote, urljoin

from bs4 import BeautifulSoup
from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    parse_price,
    validate_product_item,
)


class PlanetaScraper(PagedScraper):
    SHOP_NAME = "Планета Электроники"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://planeta.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://planeta.kz/",
        }
        self.session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def page_url(self, category_url: str, page: int) -> str:
        clean = category_url.rstrip("/")
        if "term=" in clean:
            term = clean.split("term=")[1].split("&")[0]
            term = unquote(term)
            q = quote(term)
            if page <= 1:
                return f"{self.base_url}/ru/site/search/?term={q}"
            return f"{self.base_url}/ru/site/search/term/{q}/page/{page}/"
        elif "/term/" in clean:
            term = clean.split("/term/")[1].split("/")[0]
            term = unquote(term)
            q = quote(term)
            if page <= 1:
                return f"{self.base_url}/ru/site/search/?term={q}"
            return f"{self.base_url}/ru/site/search/term/{q}/page/{page}/"
        else:
            if page <= 1:
                return clean + "/"
            return f"{clean}/page/{page}/"

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        # Извлечение кода товара / SKU
        sku = ""
        code_el = card.select_one(".code")
        if code_el:
            m = re.search(r"\d+", code_el.get_text(strip=True))
            if m:
                sku = m.group(0)

        # Ссылка на товар
        link_el = card.select_one(".title a") or card.select_one("a.pic")
        if not link_el:
            return None
        href = link_el.get("href") or ""
        if not href:
            return None
        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        if not sku:
            m_url = re.search(r"-ct-(\d+)/?$", product_url)
            if m_url:
                sku = m_url.group(1)

        prod_id = sku or re.sub(r"[^a-zA-Z0-9_-]", "_", product_url.split("/")[-2])

        # Название товара
        title_el = card.select_one(".title")
        if title_el:
            title = re.sub(r"\s+", " ", title_el.get_text(separator=" ", strip=True)).strip()
        else:
            title = re.sub(r"\s+", " ", link_el.get_text(separator=" ", strip=True)).strip()
        if not title:
            return None

        # Актуальная цена
        price = 0
        price_el = card.select_one(".price-block .price-big, .price-block .price, .price-big")
        if price_el:
            price = parse_price(price_el.get_text(strip=True))
        if not price:
            val_el = card.select_one("[class*='price']")
            if val_el:
                price = parse_price(val_el.get_text(strip=True))
        if not price:
            return None

        # Старая цена (если есть скидка)
        old_price = 0
        old_el = card.select_one(".price-old, .old-price, .price-block .old")
        if old_el:
            old_price = parse_price(old_el.get_text(strip=True))
            if old_price <= price:
                old_price = 0

        # Изображение товара
        img_el = card.select_one("a.pic img, .unit-item-block img")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-src") or ""
            if raw_src and not raw_src.endswith("no-image.png"):
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        # Наличие
        status_el = card.select_one(".status-block, .status")
        status_text = status_el.get_text(strip=True).lower() if status_el else ""
        is_available = True
        if status_text and any(k in status_text for k in ["нет в наличии", "нет на складе", "снят с"]):
            is_available = False

        item = {
            "shop": self.SHOP_NAME,
            "id": f"planeta_{prod_id}",
            "sku": str(sku or prod_id),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "in_stock": is_available,
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
            raise UnconfirmedEnd(f"Planeta HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".unit-item-block")

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

        # Определение окончания каталога по ссылкам пагинации
        max_page = None
        for a in soup.select("ul.pagination li a[data-page]"):
            dp = a.get("data-page", "").strip()
            if dp.isdigit():
                val = int(dp)
                if 1 <= val < 1000:
                    max_page = max(max_page or 0, val)

        is_complete = bool(max_page and page_num >= max_page)

        next_li = soup.select_one("ul.pagination li.next")
        if next_li:
            next_a = next_li.select_one("a[href]")
            if not next_a or next_a.get("disabled") is not None:
                is_complete = True

        if not is_complete and len(products) < 12 and page_num > 1:
            is_complete = True

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск по каталогу Планета Электроники (planeta.kz)."""
        url = f"{self.base_url}/ru/site/search/?term={quote(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".unit-item-block")
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

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
