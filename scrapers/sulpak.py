import re
import hashlib
import time
from typing import List, Dict, Any, Optional
from scrapers import http as requests
from scrapers.base import (
    UnconfirmedEnd,
    PagedScraper,
    parse_price,
    price_value,
    ScanResult,
    validate_product_item,
)
from bs4 import BeautifulSoup


class SulpakScraper(PagedScraper):
    SHOP_NAME = "Sulpak"
    SHOP_EMOJI = "🟢"

    def __init__(self):
        self.base_url = "https://www.sulpak.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        # city_id=1 — Астана в Sulpak
        self.cookies = {"city_id": "1", "language_id": "3"}

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> ScanResult:
        is_sale = "/sale" in category_url.lower()

        try:
            if is_sale:
                url = f"{self.base_url}/SaleLoadProducts/{page_num}/~/~/~/~/~/popularitydesc/tiles"
                headers = {**self.headers, "X-Requested-With": "XMLHttpRequest"}
                for attempt in range(2):
                    try:
                        r = requests.post(url, data={"page": page_num}, headers=headers,
                                          cookies=self.cookies, impersonate="chrome124", timeout=15)
                        break
                    except requests.exceptions.Timeout:
                        if attempt:
                            raise
                        time.sleep(1)

                # Сначала код ответа: пустое тело при 500 или 403 — это сбой, а не конец каталога.
                # Иначе неудачный обход выглядел бы успешно завершённым (правило P02).
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                if not r.text or not r.text.strip():
                    return ScanResult([], complete=True)

                try:
                    data = r.json()
                except Exception as e:
                    raise RuntimeError(f"Некорректный JSON от Sulpak Sale API: {e}")

                products_html = data.get("products", "")
                paginator_html = data.get("paginator", "")
                if not products_html or not products_html.strip():
                    return ScanResult([], complete=True)

                soup = BeautifulSoup(products_html, "html.parser")
                pag_soup = BeautifulSoup(paginator_html, "html.parser") if paginator_html else None
            else:
                url = category_url
                if page_num > 1:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}page={page_num}"

                for attempt in range(2):
                    try:
                        r = requests.get(url, headers=self.headers, cookies=self.cookies,
                                         impersonate="chrome124", timeout=15)
                        break
                    except requests.exceptions.Timeout:
                        if attempt:
                            raise
                        time.sleep(1)

                # 404 после последней страницы похож на конец каталога, но так же выглядит и блокировка
                # или сбой CDN. Считать это подтверждённым концом нельзя: тогда оборванный обход
                # пометит все недосмотренные товары как снятые с продажи. Обход помечается неполным.
                if r.status_code == 404 and page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")

                soup = BeautifulSoup(r.text, "html.parser")
                pag_soup = soup

            cards = soup.select(".product__item, .goods-tiles")
            if not cards:
                if page_num > 1:
                    return ScanResult([], complete=True)
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            products: List[Dict[str, Any]] = []
            seen_codes = set()

            for c in cards:
                pid = c.get("data-code") or c.get("data-product-id") or ""
                title = c.get("data-name") or ""
                data_price = c.get("data-price") or ""

                if pid and pid in seen_codes:
                    continue
                if pid:
                    seen_codes.add(pid)

                link_el = c.find("a", href=lambda h: h and ("/g/" in h or "/p/" in h))
                if not link_el:
                    continue

                rel_link = link_el.get("href", "")
                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                if not title:
                    title = link_el.text.strip()
                if not title:
                    continue

                img_el = c.find("img")
                image_url = ""
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                if data_price:
                    current_price = price_value(data_price)
                else:
                    price_el = c.select_one(".product__item-price, .price")
                    current_price = parse_price(price_el.text) if price_el else 0

                if current_price <= 0:
                    continue

                old_price_el = c.select_one(".product__item-price-old, .old-price")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                clean_path = rel_link.split("?")[0].split("#")[0]
                safe_id = pid if pid else (
                    re.search(r"[-_](\d+)$", clean_path).group(1)
                    if re.search(r"[-_](\d+)$", clean_path)
                    else hashlib.md5(clean_path.encode()).hexdigest()[:12]
                )

                item = {
                    "shop": self.SHOP_NAME,
                    "id": f"sulpak_{safe_id}",
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": current_price,
                    "old_price_on_site": old_price if old_price > current_price else 0,
                    "city": "Астана",
                }
                if validate_product_item(item):
                    products.append(item)

            pages_count = None
            if pag_soup:
                pag_el = pag_soup.select_one("#paginator, .pagination")
                if pag_el:
                    cnt = pag_el.get("data-pagescount")
                    if cnt and cnt.isdigit():
                        pages_count = int(cnt)

            complete = pages_count is not None and page_num >= pages_count
            return ScanResult(products, complete=complete)

        except UnconfirmedEnd:
            raise
        except Exception:
            raise
