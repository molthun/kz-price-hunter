"""Адаптер парсера аптечной сети «Europharma» (europharma.kz).

Крупнейшая аптечная сеть в Республике Казахстан (более 800 аптек, доставка медикаментов,
БАДов, уходовой и лечебной косметики, витаминов и изделий медицинского назначения).
Каталог: SSR HTML с карточками div.card-product, идентификаторами data-id,
ценами .card-product__price_discount / data-price и пагинацией ?page={page}.
Поиск в реальном времени: https://europharma.kz/search?q={query}.
"""
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    parse_price,
    validate_product_item,
)


class EuropharmaScraper(PagedScraper):
    SHOP_NAME = "Europharma"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://europharma.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://europharma.kz/",
        }
        self.session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def page_url(self, category_url: str, page: int) -> str:
        if page <= 1:
            return category_url
        parsed = urlparse(category_url)
        params = parse_qs(parsed.query)
        params["page"] = [str(page)]
        new_query = urlencode(params, doseq=True)
        return parsed._replace(query=new_query).geturl()

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        # Проверка наличия товара: .card-product__price_empty или явная пометка
        if card.select_one(".card-product__price_empty"):
            return None

        # ID товара / SKU
        prod_id = card.get("data-id") or ""
        link_el = card.select_one(".card-product__title a, a.card-product__link, a[data-pjax='0']")
        href = link_el.get("href") if link_el else ""
        if not href:
            return None
        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        if not prod_id:
            m = re.search(r"-(\d+)$", product_url)
            if m:
                prod_id = m.group(1)
            else:
                prod_id = re.sub(r"[^a-zA-Z0-9_-]", "", urlparse(product_url).path.strip("/"))

        if not prod_id:
            return None

        # Название товара
        title_el = card.select_one(".card-product__title a, a.card-product__link, .card-product__title")
        title = title_el.get_text(separator=" ", strip=True) if title_el else ""
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        # Описание / Производитель
        desc_el = card.select_one(".card-product__desc")
        description = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

        # Цена товара
        price = 0
        if card.get("data-price"):
            price = parse_price(card.get("data-price"))
        if not price:
            cur_price_el = card.select_one(".card-product__price_discount, .card-product__price, [class*='price']")
            if cur_price_el:
                price = parse_price(cur_price_el.get_text(strip=True))
        if not price:
            return None

        # Старая цена (до скидки)
        old_price = 0
        old_price_el = card.select_one(".card-product__price_original, [class*='price_old']")
        if old_price_el:
            old_price = parse_price(old_price_el.get_text(strip=True))
            if old_price <= price:
                old_price = 0

        # Изображение товара
        img_el = card.select_one("img.card-product__img, img")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-src") or ""
            if raw_src:
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        item = {
            "shop": self.SHOP_NAME,
            "id": f"europharma_{prod_id}",
            "sku": str(prod_id),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "description": description,
            "in_stock": True,
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
            raise UnconfirmedEnd(f"Europharma HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".card-product")

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

        # Определение окончания каталога
        pag = soup.select_one(".pagination")
        if not pag:
            # Единственная страница
            return ScanResult(products, complete=True)

        next_li = pag.select_one("li.pagination__item.next")
        is_complete = False
        if not next_li or "disabled" in next_li.get("class", []):
            is_complete = True

        # Проверка активной страницы: если сервер зациклил на меньшую страницу
        active_link = pag.select_one("li.pagination__item.active a[data-page]")
        if active_link:
            try:
                active_page_idx = int(active_link.get("data-page", "0"))
                # data-page 0-индексирован (0 = 1 страница, 1 = 2 страница)
                if (active_page_idx + 1) < page_num:
                    is_complete = True
            except ValueError:
                pass

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Живой поиск лекарств и товаров для здоровья через Europharma."""
        clean_query = query.strip()
        if not clean_query:
            return []
        effective_limit = max_results if max_results is not None else limit

        search_url = f"{self.base_url}/search?q={quote_plus(clean_query)}"
        session = self._get_session()

        try:
            resp = session.get(search_url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
        except Exception:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".card-product")

        results: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()

        for card in cards:
            validated = self._parse_card(card, category_name="Поиск")
            if validated and validated["id"] not in seen_ids:
                seen_ids.add(validated["id"])
                results.append(validated)
                if len(results) >= effective_limit:
                    break

        return results

    def scrape(
        self,
        category_name: str,
        category_url: str,
        max_pages: Optional[int] = None,
    ) -> ScanResult:
        """Постраничный обход каталога Europharma с проверкой завершения."""
        return super().scrape(category_name, category_url, max_pages=max_pages)
