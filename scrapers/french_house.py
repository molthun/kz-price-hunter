"""Адаптер парсера сети магазинов косметики и парфюмерии «Французский Дом» (french-house.kz).

Легендарная сеть селективной парфюмерии и косметики в Республике Казахстан,
работающая на рынке с 1992 года (официальный дистрибьютор мировых брендов:
Dior, Chanel, Guerlain, Yves Saint Laurent, Lancome, Givenchy, Kenzo, Versace и др.).
Каталог: SSR HTML с карточками div.goodCard.cardType1, идентификаторами data-id,
ценами .cardCost.new, скидками .cardCost.old и пагинацией ?PAGEN_1={page}.
Поиск в реальном времени: https://french-house.kz/search/?q={query}.
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
    pagination_last_page,
    parse_price,
    validate_product_item,
)


class FrenchHouseScraper(PagedScraper):
    SHOP_NAME = "Французский Дом"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, base_url: str = "https://french-house.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://french-house.kz/",
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
        params["PAGEN_1"] = [str(page)]
        new_query = urlencode(params, doseq=True)
        return parsed._replace(query=new_query).geturl()

    def _parse_card(self, card: Any, category_name: str) -> Optional[Dict[str, Any]]:
        # Ссылка на товар
        link_el = card.find("a", href=True)
        if not link_el:
            return None
        href = link_el["href"]
        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        # ID товара / SKU
        btn_el = card.select_one("button[data-id], .cardFavoriteStatus[id], [data-id]")
        prod_id = ""
        if btn_el:
            prod_id = btn_el.get("data-id") or btn_el.get("id") or ""
        if not prod_id:
            m = re.search(r"-(\d+)/?$", product_url)
            if m:
                prod_id = m.group(1)
            else:
                prod_id = re.sub(r"[^a-zA-Z0-9_-]", "", urlparse(product_url).path.strip("/").split("/")[-1])

        if not prod_id:
            return None

        # Название товара
        name_el = card.select_one(".name, .cardInfo span.name, .cardInfo h2, .cardInfo h3")
        if not name_el or not name_el.get_text(strip=True):
            for a_el in card.find_all("a"):
                if a_el.get_text(strip=True):
                    name_el = a_el
                    break
        desc_el = card.select_one(".cardInfo p")
        name_text = name_el.get_text(separator=" ", strip=True) if name_el else ""
        type_text = desc_el.get_text(separator=" ", strip=True) if desc_el else ""

        if type_text and type_text.lower() not in name_text.lower():
            title = f"{name_text} {type_text}".strip()
        else:
            title = name_text
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        # Цена товара
        cur_price_el = card.select_one(".cardCost.new, .cardCost")
        price = parse_price(cur_price_el.get_text(strip=True)) if cur_price_el else 0
        if not price:
            return None

        # Старая цена (до скидки)
        old_price_el = card.select_one(".cardCost.old")
        old_price = parse_price(old_price_el.get_text(strip=True)) if old_price_el else 0
        if old_price <= price:
            old_price = 0

        # Изображение
        img_el = card.select_one("img[src]")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or ""
            if raw_src and not raw_src.endswith(".svg"):
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        item = {
            "shop": self.SHOP_NAME,
            "id": f"frenchhouse_{prod_id}",
            "sku": str(prod_id),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "description": type_text,
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
            # 404 за последней страницей похож на конец каталога, но так же выглядит блокировка
            # или сбой CDN. Подтверждённый конец означает, что недосмотренные товары будут помечены
            # снятыми с продажи, поэтому обход честно остаётся неполным (то же решение, что для
            # Sulpak в 5.17.2).
            if page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
            raise UnconfirmedEnd("Категория не найдена (404 на первой странице)")

        if resp.status_code != 200:
            raise UnconfirmedEnd(f"French House HTTP {resp.status_code} на странице {page_num}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".goodCard, .cardType1")

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

        # Определение окончания каталога по номеру последней страницы
        last_page = pagination_last_page(resp.text, category_url, param="PAGEN_1")
        is_complete = False
        if last_page is not None and page_num >= last_page:
            is_complete = True
        elif last_page is None:
            is_complete = True

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20, max_results: Optional[int] = None) -> List[Dict[str, Any]]:
        """Живой поиск элитной парфюмерии и косметики через «Французский Дом»."""
        clean_query = query.strip()
        if not clean_query:
            return []
        effective_limit = max_results if max_results is not None else limit

        search_url = f"{self.base_url}/search/?q={quote_plus(clean_query)}"
        session = self._get_session()

        try:
            resp = session.get(search_url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
        except Exception:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".goodCard, .cardType1")

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
