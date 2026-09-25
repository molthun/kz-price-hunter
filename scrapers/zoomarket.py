"""Адаптер парсера интернет-зоомагазина «ZooMarket» (zoomarket.kz).

Крупнейший интернет-зоомагазин Казахстана с доставкой по Алматы и РК.
Широкий ассортимент кормов для кошек и собак, ветеринарных препаратов,
лакомств, наполнителей, аксессуаров, товаров для птиц, грызунов и рыб.
Каталог: SSR HTML Bitrix с карточками .catalog_item, пагинацией ?PAGEN_1=N
и проверкой номеров страниц в пагинаторе .nums.
Поиск в реальном времени: https://zoomarket.kz/catalog/?q={query}.
"""
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
    validate_product_item,
)


class ZooMarketScraper(PagedScraper):
    SHOP_NAME = "Зоомаркет"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://zoomarket.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://zoomarket.kz/",
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
        return f"{clean}/?PAGEN_1={page}"

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        # Извлечение ID товара
        prod_id = card.get("data-param-id")
        if not prod_id:
            card_id = card.get("id") or ""
            m = re.search(r"_(\d+)$", card_id)
            if m:
                prod_id = m.group(1)
        if not prod_id:
            fast_view = card.select_one("[data-param-id]")
            if fast_view:
                prod_id = fast_view.get("data-param-id")

        # Заголовок и ссылка на товар
        title_el = card.select_one(".item-title a.link-product-page, .item-title a.dark_link")
        if not title_el:
            title_el = card.select_one(".item-title a[href*='/catalog/']")
        if not title_el:
            return None

        title_span = title_el.select_one("span")
        title = (title_span.get_text(strip=True) if title_span else title_el.get_text(strip=True)) or ""
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        href = title_el.get("href") or ""
        if not href:
            return None
        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        if not prod_id:
            # Fallback на числовой ID из URL: /catalog/.../235908/
            m_url = re.search(r"/(\d+)/?$", product_url)
            if m_url:
                prod_id = m_url.group(1)
            else:
                return None

        # Актуальная цена
        price = 0
        price_el = card.select_one(".price[data-value]")
        if price_el and price_el.get("data-value"):
            try:
                price = int(float(price_el.get("data-value")))
            except (ValueError, TypeError):
                price = 0
        if not price:
            val_el = card.select_one(".price_value")
            if val_el:
                price = parse_price(val_el.text)
        if not price:
            cost_el = card.select_one(".cost, .prices, .price")
            if cost_el:
                price = parse_price(cost_el.text)
        if not price:
            return None

        # Старая цена (если есть скидка)
        old_price = 0
        old_el = card.select_one(".price_old, [class*='price_old'], .price.old")
        if old_el:
            if old_el.get("data-value"):
                try:
                    old_price = int(float(old_el.get("data-value")))
                except (ValueError, TypeError):
                    old_price = parse_price(old_el.text)
            else:
                old_price = parse_price(old_el.text)
            if old_price <= price:
                old_price = 0

        # Изображение
        img_el = card.select_one("a.thumb img, .image_wrapper_block img")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-src") or ""
            if raw_src and not raw_src.endswith("logo.svg"):
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        item = {
            "shop": self.SHOP_NAME,
            "id": f"zoomarket_{prod_id}",
            "sku": str(prod_id),
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
            raise UnconfirmedEnd(f"ZooMarket HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".catalog_item")

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

        # Определение завершения каталога по ссылкам пагинатора .nums
        max_page = None
        for a in soup.select(".nums a"):
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
        """Живой поиск по каталогу ZooMarket (zoomarket.kz)."""
        url = f"{self.base_url}/catalog/?q={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select(".catalog_item")
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
