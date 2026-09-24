"""Адаптер парсера гипермаркета мебели и товаров для дома «Mebel.kz» (mebel.kz).

Крупнейший мебельный интернет-магазин Казахстана: диваны, кровати, матрасы,
шкафы, столы, стулья, текстиль и освещение.
Каталог: SSR HTML с карточками ProductCardMain, пагинацией /category/.../page-N
и резервной разметкой schema.org ItemList.
Поиск в реальном времени: официальный backend API proxy.mebel.kz/backend/search/get-products.
"""
import json
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


class MebelScraper(PagedScraper):
    SHOP_NAME = "Mebel.kz"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://mebel.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://mebel.kz/",
        }
        self.session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def page_url(self, category_url: str, page: int) -> str:
        clean = category_url.rstrip("/")
        if page <= 1:
            return clean
        return f"{clean}/page-{page}"

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        a_tag = card.find("a", href=lambda h: h and "/product/" in h)
        if not a_tag:
            return None
        href = a_tag.get("href", "")
        if not href:
            return None

        product_url = href if href.startswith("http") else urljoin(self.base_url, href)
        slug = href.rstrip("/").split("/")[-1]

        # Название товара
        title_el = card.find(class_=lambda x: x and "ProductName" in x)
        if not title_el:
            title_el = card.find("div", class_=lambda x: x and "Name-module" in x)
        raw_title = title_el.text if title_el else a_tag.text
        title = re.sub(r"\s+", " ", raw_title or "").strip()
        if not title:
            img_alt = card.select_one("img[alt]")
            title = re.sub(r"\s+", " ", (img_alt.get("alt") or "")).strip() if img_alt else ""
        if not title:
            return None

        # Актуальная цена
        price_el = card.select_one('[class*="actual"]')
        if not price_el:
            price_el = card.find(attrs={"data-testid": "price"})
        price = parse_price(price_el.text) if price_el else 0
        if not price:
            return None

        # Старая цена (если есть скидка)
        old_price = 0
        old_el = card.select_one('[class*="expired"]')
        if old_el:
            parsed_old = parse_price(old_el.text)
            if parsed_old > price:
                old_price = parsed_old

        # Фото товара
        img_el = card.select_one("img[src]")
        img_src = ""
        if img_el:
            img_src = (img_el.get("src") or img_el.get("data-src") or "").strip()

        item = {
            "shop": self.SHOP_NAME,
            "id": f"mebel_{slug}",
            "sku": slug,
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "city": "Алматы / Казахстан",
        }
        return validate_product_item(item, default_shop=self.SHOP_NAME)

    def _parse_itemlist_schema(self, soup: BeautifulSoup, category_name: str) -> List[Dict[str, Any]]:
        """Резервный парсинг разметки schema.org ItemList со страницы каталога."""
        results: List[Dict[str, Any]] = []
        for s in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(s.string or "")
                if data.get("@type") == "ItemList" and "itemListElement" in data:
                    for el in data["itemListElement"]:
                        it = el.get("item", {})
                        if not it or it.get("@type") != "Product":
                            continue
                        name = it.get("name") or ""
                        url = it.get("url") or ""
                        image = it.get("image") or ""
                        offers = it.get("offers", {})
                        price = price_value(offers.get("price"))
                        if not name or not url or price <= 0:
                            continue
                        slug = url.rstrip("/").split("/")[-1]
                        item = {
                            "shop": self.SHOP_NAME,
                            "id": f"mebel_{slug}",
                            "sku": slug,
                            "title": name,
                            "category": category_name,
                            "url": url,
                            "image_url": image,
                            "price": price,
                            "old_price_on_site": 0,
                            "city": "Алматы / Казахстан",
                        }
                        validated = validate_product_item(item, default_shop=self.SHOP_NAME)
                        if validated:
                            results.append(validated)
            except Exception:
                continue
        return results

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
            raise UnconfirmedEnd(f"Не удалось получить ответ для страницы {page_num}")

        if resp.status_code == 404:
            if page_num > 1:
                return ScanResult([], complete=True)
            raise UnconfirmedEnd("Категория не найдена (404 на первой странице)")

        if resp.status_code != 200:
            raise UnconfirmedEnd(f"Mebel.kz HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. Поиск карточек в HTML
        cards = soup.find_all(
            "div",
            class_=lambda c: c and "ProductCardMain" in c and "container" in c,
        )

        products: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for card in cards:
            validated = self._parse_card(card, category_name)
            if validated and validated["url"] not in seen_urls:
                seen_urls.add(validated["url"])
                products.append(validated)

        # 2. Если HTML-карточки не распарсились, используем schema.org ItemList
        if not products:
            schema_products = self._parse_itemlist_schema(soup, category_name)
            for p in schema_products:
                if p["url"] not in seen_urls:
                    seen_urls.add(p["url"])
                    products.append(p)

        if not products:
            if page_num == 1:
                raise UnconfirmedEnd("Товары не найдены на первой странице")
            return ScanResult([], complete=True)

        # 3. Определение максимальной страницы пагинации
        page_links = soup.find_all("a", href=lambda h: h and "/page-" in h)
        pages_found: List[int] = []
        for pl in page_links:
            m = re.search(r"/page-(\d+)", pl.get("href", ""))
            if m:
                pages_found.append(int(m.group(1)))

        max_page = max(pages_found) if pages_found else None
        is_complete = bool(max_page and page_num >= max_page)
        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой опрос поиска Mebel.kz через backend API proxy.mebel.kz."""
        url = f"https://proxy.mebel.kz/backend/search/get-products?ProductSearch[name]={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            payload = resp.json()
            raw_products = payload.get("data", {}).get("products", [])
            results: List[Dict[str, Any]] = []

            for p in raw_products:
                pid = p.get("id")
                link = p.get("link") or ""
                if not pid or not link:
                    continue

                full_url = link if link.startswith("http") else urljoin(self.base_url, link)
                p_type = p.get("type") or ""
                p_name = p.get("name") or ""
                title = f"{p_type} {p_name}".strip() if p_type else p_name
                if not title:
                    meta_p = p.get("meta", {}).get("product", [])
                    for mp in meta_p:
                        if mp.get("itemprop") == "name":
                            title = mp.get("content") or ""
                            break

                price_info = p.get("price", {})
                price = price_value(price_info.get("actual"))
                if price <= 0:
                    continue

                old_price = 0
                expired = price_value(price_info.get("expired"))
                if expired > price:
                    old_price = expired

                images = p.get("images", [])
                image_url = images[0].get("src", "") if images else ""

                item = {
                    "shop": self.SHOP_NAME,
                    "id": f"mebel_{pid}",
                    "sku": str(pid),
                    "title": title or "Товар Mebel.kz",
                    "category": p.get("category") or "Поиск",
                    "url": full_url,
                    "image_url": image_url,
                    "price": price,
                    "old_price_on_site": old_price,
                    "city": "Алматы / Казахстан",
                }
                validated = validate_product_item(item, default_shop=self.SHOP_NAME)
                if validated:
                    results.append(validated)
                    if len(results) >= limit:
                        break
            return results
        except Exception:
            return []
