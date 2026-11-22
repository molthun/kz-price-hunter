"""Адаптер парсера сети магазинов обуви, одежды и аксессуаров «KIMEX» (kimex.kz).

Старейшая и крупнейшая в Казахстане мультибрендовая сеть обуви и одежды европейских
брендов, работающая на рынке с 1994 года.
Широкий ассортимент мужской и женской обуви (Rieker, Tamaris, Caprice, IMAC, Bugatti,
Marco Tozzi, Abricot и др.), одежды и аксессуаров.
Каталог: SSR HTML с карточками a.card[data-entity="item"], идентификаторами data-id,
актуальными ценами .price-current и пагинацией /page-{page}/.
Поиск в реальном времени: https://kimex.kz/search/?q={query}.
"""
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus, urljoin

from domain.sizes import normalize_available_sizes
from html_soup import make_soup
from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    parse_price,
    validate_product_item,
)


class KimexScraper(PagedScraper):
    SHOP_NAME = "KIMEX"
    PAGE_DELAY_SECONDS = 1.0
    RETRY_STATUS_CODES = (500, 502, 503, 504, 520, 522, 524)
    REQUEST_TIMEOUT_SECONDS = 30
    RETRY_DELAY_SECONDS = 2.0

    def __init__(self, base_url: str = "https://kimex.kz"):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,kk;q=0.8,en;q=0.7",
            "Referer": "https://kimex.kz/",
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
        return f"{clean}/page-{page}/"

    _HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
    _JS_PUSH_RE = re.compile(r"SDVVJCCatalogItemJSData\.push\((\{.*?\})\);", re.DOTALL)
    _PRODUCT_ID_RE = re.compile(r"'PRODUCT'\s*:\s*\{\s*'ID'\s*:\s*'(\d+)'")
    _OFFER_SPLIT_RE = re.compile(r"(?=\{'ID':'\d+','NAME':')")
    _OFFER_NAME_RE = re.compile(r"'NAME'\s*:\s*'([^']+)'")
    _OFFER_CAN_BUY_RE = re.compile(r"'CAN_BUY'\s*:\s*(true|false)")
    _OFFER_MAX_QTY_RE = re.compile(r"'MAX_QUANTITY'\s*:\s*'([^']*)'")
    _TRAILING_SIZE_RE = re.compile(r"\(([^()]{1,16})\)\s*$")

    @classmethod
    def _extract_sizes_map(cls, html: str) -> Dict[str, List[str]]:
        """Извлекает доступные размеры товаров из встроенных блоков SDVVJCCatalogItemJSData."""
        if not html or "SDVVJCCatalogItemJSData" not in html:
            return {}
        sizes_map: Dict[str, List[str]] = {}
        for block in cls._JS_PUSH_RE.findall(html):
            pid_match = cls._PRODUCT_ID_RE.search(block)
            if not pid_match:
                continue
            pid = pid_match.group(1)
            offers_idx = block.find("'OFFERS'")
            if offers_idx == -1:
                continue
            offers_chunk = block[offers_idx:]
            raw_sizes: List[str] = []
            sized_offers = 0
            for offer_blk in cls._OFFER_SPLIT_RE.split(offers_chunk)[1:]:
                nm = cls._OFFER_NAME_RE.search(offer_blk)
                sm = cls._TRAILING_SIZE_RE.search(nm.group(1)) if nm else None
                if not sm or not normalize_available_sizes([sm.group(1)]):
                    continue
                sized_offers += 1  # размерный вариант распознан — доступен он или нет
                cb = cls._OFFER_CAN_BUY_RE.search(offer_blk)
                if not cb or cb.group(1) != "true":
                    continue
                mq = cls._OFFER_MAX_QTY_RE.search(offer_blk)
                if mq and mq.group(1).strip() in ("0", "0.0"):
                    continue
                raw_sizes.append(sm.group(1))
            # SZ-04: варианты разобраны — список известен, в том числе пустой (всё раскуплено)
            if sized_offers:
                sizes_map[pid] = normalize_available_sizes(raw_sizes)
        return sizes_map

    def _parse_card(
        self,
        card: Any,
        category_name: str,
        sizes_map: Optional[Dict[str, List[str]]] = None,
    ) -> Optional[Dict[str, Any]]:
        # ID товара / SKU
        prod_id = card.get("data-id") or ""
        href = card.get("href") or ""
        if not href:
            return None
        product_url = href if href.startswith("http") else urljoin(self.base_url, href)

        if not prod_id:
            m = re.search(r"_(\d+)/?$", product_url)
            if m:
                prod_id = m.group(1)
            else:
                m2 = re.search(r"-([a-zA-Z0-9_-]+)/?$", product_url)
                prod_id = m2.group(1) if m2 else ""

        if not prod_id:
            return None

        # Заголовок
        title_el = card.select_one(".card__title, .card__name, [class*='title']")
        title = title_el.get_text(separator=" ", strip=True) if title_el else ""
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            return None

        # Цена товара
        price = 0
        cur_price_el = card.select_one(".price-current, .card__price")
        if cur_price_el:
            price = parse_price(cur_price_el.get_text(strip=True))
        if not price:
            cost_el = card.select_one("[class*='price']")
            if cost_el:
                price = parse_price(cost_el.get_text(strip=True))
        if not price:
            return None

        # Старая цена (если есть скидка; пропускаем скрытые Bitrix-заглушки display: none)
        old_price = 0
        for old_price_el in card.select(".card__price--old, .price--old"):
            style_attr = str(old_price_el.get("style") or "")
            if self._HIDDEN_STYLE_RE.search(style_attr):
                continue
            candidate = parse_price(old_price_el.get_text(strip=True))
            if candidate > price:
                old_price = candidate
                break

        # Изображение товара
        img_el = card.select_one("img")
        img_src = ""
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-src") or ""
            if raw_src:
                img_src = raw_src if raw_src.startswith("http") else urljoin(self.base_url, raw_src)

        item = {
            "shop": self.SHOP_NAME,
            "id": f"kimex_{prod_id}",
            "sku": str(prod_id),
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": img_src,
            "price": price,
            "old_price_on_site": old_price,
            "in_stock": True,
            "city": "Алматы / Казахстан",
        }
        sizes = (sizes_map or {}).get(str(prod_id))
        if sizes is not None:
            item["available_sizes"] = list(sizes)  # [] — размеры разобраны, доступных нет
        else:
            item["sizes_unknown"] = True
        return validate_product_item(item, default_shop=self.SHOP_NAME)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> ScanResult:
        url = self.page_url(category_url, page_num)
        session = self._get_session()

        resp = None
        for attempt in range(2):
            try:
                resp = session.get(url, headers=self.headers, timeout=self.REQUEST_TIMEOUT_SECONDS)
                if resp.status_code in self.RETRY_STATUS_CODES and attempt == 0:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                break
            except requests.HostCooldown:
                raise
            except Exception:
                if attempt == 0:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                raise

        if resp is None:
            raise UnconfirmedEnd(f"Не удалось получить ответ для страницы {page_num}")

        if resp.status_code == 404:
            if page_num > 1:
                return ScanResult([], complete=True)
            raise UnconfirmedEnd("Категория не найдена (404 на первой странице)")

        if resp.status_code != 200:
            raise UnconfirmedEnd(f"KIMEX HTTP {resp.status_code} на странице {page_num}")

        soup = make_soup(resp.text)
        sizes_map = self._extract_sizes_map(resp.text)
        # Основные карточки каталога находятся в .cataloge__cards
        cards = soup.select(".cataloge__cards a.card[data-entity='item']")
        if not cards:
            cards = [c for c in soup.select("a.card[data-entity='item']") if "swiper-slide" not in [p.get("class", [""])[0] for p in c.parents]]

        products: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        for card in cards:
            validated = self._parse_card(card, category_name, sizes_map=sizes_map)
            if validated and validated["url"] not in seen_urls:
                seen_urls.add(validated["url"])
                products.append(validated)

        if not products:
            if page_num == 1:
                raise UnconfirmedEnd("Товары не найдены на первой странице")
            return ScanResult([], complete=True)

        # Определение окончания каталога: наличие кнопки js-load-more
        has_next_btn = bool(soup.select_one(".btn.js-load-more"))
        is_complete = not has_next_btn

        if not is_complete and len(products) < 16 and page_num > 1:
            is_complete = True

        return ScanResult(products, complete=is_complete)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск по каталогу KIMEX (kimex.kz)."""
        url = f"{self.base_url}/search/?q={quote_plus(query)}"
        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=12)
            if resp.status_code != 200:
                return []
            soup = make_soup(resp.text)
            sizes_map = self._extract_sizes_map(resp.text)
            cards = soup.select(".cataloge__cards a.card[data-entity='item']")
            if not cards:
                cards = [c for c in soup.select("a.card[data-entity='item']") if "swiper-slide" not in [p.get("class", [""])[0] for p in c.parents]]

            results: List[Dict[str, Any]] = []
            seen_urls: Set[str] = set()

            for card in cards:
                validated = self._parse_card(card, "Поиск", sizes_map=sizes_map)
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
