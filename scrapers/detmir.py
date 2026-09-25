"""Адаптер парсера интернет-магазина детских товаров «Детский мир» (detmir.kz).

Крупнейшая сеть детских товаров в Казахстане: игрушки, конструкторы LEGO,
детское питание, подгузники и гигиена, одежда и обувь, коляски, автокресла,
мебель для детской комнаты и товары для творчества.
Каталог: SSR HTML с гидратацией window.appData, резервным разбором карточек section[data-product-id]
и постраничной пагинацией /catalog/index/name/{slug}/page/{page}/.
Поиск в реальном времени: живой опрос https://detmir.kz/search/results/?qt={query}.
"""
import json
import math
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
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


class DetmirScraper(PagedScraper):
    SHOP_NAME = "Детский мир"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://detmir.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://detmir.kz/",
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

    def _extract_app_data_items(self, html: str, category_name: str) -> Tuple[List[Dict[str, Any]], int]:
        """Извлекает товары и общее количество из внедренного window.appData."""
        idx = html.find("window.appData = JSON.parse(")
        if idx == -1:
            return [], 0

        content = html[idx + len("window.appData = JSON.parse("):]
        end_idx = content.find('")')
        if end_idx == -1:
            return [], 0

        arg = content[:end_idx + 1]
        try:
            parsed_str = json.loads(arg)
            app_data = json.loads(parsed_str)
            cat_data = (app_data.get("catalog") or {}).get("data") or {}
            raw_items = cat_data.get("items") or []
            meta = cat_data.get("meta") or {}
            total_products = meta.get("productsLength") or 0

            results: List[Dict[str, Any]] = []
            for it in raw_items:
                pid = it.get("id")
                title = (it.get("title") or "").strip()
                if not pid or not title:
                    continue

                price_dict = it.get("price") or {}
                final_price_dict = it.get("final_price") or {}
                price = price_value(price_dict.get("price") or final_price_dict.get("price"))
                if price <= 0:
                    continue

                old_price_dict = it.get("old_price") or {}
                old_val = price_value(old_price_dict.get("price"))
                old_price = old_val if old_val > price else 0

                link_dict = it.get("link") or {}
                raw_url = link_dict.get("web_url") or link_dict.get("href") or ""
                product_url = raw_url if raw_url.startswith("http") else urljoin(self.base_url, raw_url)

                pictures = it.get("pictures") or []
                image_url = ""
                if pictures:
                    image_url = (pictures[0].get("original") or pictures[0].get("web") or "").strip()

                item = {
                    "shop": self.SHOP_NAME,
                    "id": f"detmir_{pid}",
                    "sku": str(pid),
                    "title": title,
                    "category": category_name,
                    "url": product_url,
                    "image_url": image_url,
                    "price": price,
                    "old_price_on_site": old_price,
                    "city": "Алматы / Казахстан",
                }
                validated = validate_product_item(item, default_shop=self.SHOP_NAME)
                if validated:
                    results.append(validated)

            return results, total_products
        except Exception:
            return [], 0

    def _extract_dom_items(self, soup: BeautifulSoup, category_name: str) -> List[Dict[str, Any]]:
        """Резервный парсинг DOM карточек section[data-product-id]."""
        sections = soup.find_all("section", attrs={"data-product-id": True})
        results: List[Dict[str, Any]] = []

        for sec in sections:
            pid = sec.get("data-product-id")
            if not pid:
                continue

            a_tag = sec.find("a", href=lambda h: h and "/product/index/id/" in h)
            if not a_tag:
                continue
            href = a_tag.get("href", "")
            product_url = href if href.startswith("http") else urljoin(self.base_url, href)

            # Название товара
            title = ""
            for cand in sec.find_all("a", href=lambda h: h and "/product/index/id/" in h):
                t = cand.get_text(strip=True)
                if len(t) > 5 and not t.isdigit():
                    title = t
                    break
            if not title:
                img_cand = sec.find("img", alt=True)
                if img_cand:
                    title = img_cand.get("alt", "").strip()
            if not title:
                continue

            # Цены
            text_all = sec.get_text(separator=" ", strip=True)
            price_matches = re.findall(r"(\d[\d\s\u2009\u202f\xa0]*)\s*₸", text_all)
            prices = [int(re.sub(r"\D", "", p)) for p in price_matches if re.sub(r"\D", "", p)]
            if not prices:
                continue
            price = prices[0]
            if price <= 0:
                continue
            old_price = prices[1] if len(prices) > 1 and prices[1] > price else 0

            # Изображение
            img_src = ""
            img_tag = sec.find("img")
            if img_tag:
                img_src = (img_tag.get("src") or img_tag.get("data-src") or "").strip()
            if not img_src:
                source_tag = sec.find("source")
                if source_tag and source_tag.get("srcset"):
                    img_src = source_tag.get("srcset").split()[0].strip()

            item = {
                "shop": self.SHOP_NAME,
                "id": f"detmir_{pid}",
                "sku": str(pid),
                "title": title,
                "category": category_name,
                "url": product_url,
                "image_url": img_src,
                "price": price,
                "old_price_on_site": old_price,
                "city": "Алматы / Казахстан",
            }
            validated = validate_product_item(item, default_shop=self.SHOP_NAME)
            if validated:
                results.append(validated)

        return results

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
            raise UnconfirmedEnd(f"Детский мир HTTP {resp.status_code} на странице {page_num}")

        html = resp.text
        # 1. Попытка через window.appData
        products, total_products = self._extract_app_data_items(html, category_name)

        soup = BeautifulSoup(html, "html.parser")
        # 2. Если appData пуста, используем DOM
        if not products:
            products = self._extract_dom_items(soup, category_name)

        if not products:
            if page_num == 1:
                raise UnconfirmedEnd("Товары не найдены на первой странице")
            return ScanResult([], complete=True)

        # 3. Определение признака окончания каталога
        is_complete = False
        if total_products > 0:
            max_page = math.ceil(total_products / 36)
            if page_num >= max_page:
                is_complete = True
        else:
            # Определение по кнопкам пагинации
            buttons = soup.find_all(["button", "a"])
            page_numbers: List[int] = []
            for b in buttons:
                t = b.get_text(strip=True)
                if t.isdigit():
                    val = int(t)
                    if 1 < val < 1000:
                        page_numbers.append(val)
            if page_numbers:
                max_page = max(page_numbers)
                if page_num >= max_page:
                    is_complete = True
            elif len(products) < 18 and page_num > 1:
                is_complete = True

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск по каталогу «Детский мир» (detmir.kz)."""
        url = f"{self.base_url}/search/results/?qt={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            products, _ = self._extract_app_data_items(resp.text, "Поиск")
            if not products:
                soup = BeautifulSoup(resp.text, "html.parser")
                products = self._extract_dom_items(soup, "Поиск")
            return products[:limit]
        except Exception:
            return []
