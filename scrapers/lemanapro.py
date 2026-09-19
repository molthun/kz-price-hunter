"""Скрапер гипермаркета товаров для строительства и ремонта «Лемана ПРО» (lemanapro.kz, ex-Leroy Merlin).

Платформа: SSR HTML.
Селектор карточек: div[data-qa-product].
Пагинация: ?page=N.
"""
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item, UnconfirmedEnd, ScanResult, pagination_last_page


class LemanaProScraper(PagedScraper):
    SHOP_NAME = "Лемана ПРО"
    SHOP_EMOJI = "🟢"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://lemanapro.kz"
        self.session = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        url = category_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}page={page_num}"

        session = self._get_session()
        try:
            r = session.get(url, headers=self.headers, timeout=20)
            if r.status_code == 404:
                # За последней страницей — 404; на первой странице это ошибка категории
                if page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней страницы")
                raise RuntimeError(f"HTTP 404 on {url}")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {url}")

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("div[data-qa-product]")
            if not cards:
                raise UnconfirmedEnd("карточки не найдены")
            # Конец каталога подтверждает номер последней страницы в пагинации (R-M01)
            last_page = pagination_last_page(r.text, category_url, "page")

            seen_ids = set()

            for c in cards:
                # Артикул / SKU
                sku = ""
                art_el = c.select_one('[data-qa="product-article"]')
                if art_el:
                    sku = re.sub(r"[^\d]", "", art_el.get_text())

                # Ссылка на товар
                link_el = c.select_one('a[data-qa="product-name"], a[href*="/product/"]')
                if not link_el:
                    continue
                rel_url = link_el.get("href", "").strip()
                if not rel_url:
                    continue
                product_url = urljoin(self.base_url, rel_url)

                if not sku:
                    sku_m = re.search(r"(\d{7,10})/?$", rel_url)
                    if sku_m:
                        sku = sku_m.group(1)
                    else:
                        sku = rel_url.strip("/").split("/")[-1]

                if sku in seen_ids:
                    continue

                # Наименование товара
                title_el = c.select_one(".product-card-name-link, [data-qa=\"product-name\"] span")
                title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
                if not title:
                    img_el = c.select_one("img")
                    if img_el and img_el.get("alt"):
                        title = img_el["alt"].strip()

                if not title:
                    continue

                # Текущая цена
                price = 0
                p_el = c.select_one('[data-testid="price-block-price"]')
                if p_el and p_el.get("value"):
                    price = price_value(p_el["value"])
                if not price and p_el:
                    price = price_value(p_el.get_text())
                if not price:
                    for pel in c.select('[data-testid*="price"], [class*="price"]'):
                        pv = price_value(pel.get_text())
                        if pv > 0:
                            price = pv
                            break

                if not price or price <= 0:
                    continue

                # Старая цена со скидкой
                old_price = None
                old_el = c.select_one('[data-testid="price-block-oldprice"]')
                if old_el:
                    if old_el.get("value"):
                        old_price = price_value(old_el["value"])
                    if not old_price:
                        old_price = price_value(old_el.get_text())
                if old_price and old_price <= price:
                    old_price = None

                # Изображение
                img_url = ""
                img_el = c.select_one("img")
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        img_url = urljoin(self.base_url, src)

                item = {
                    "id": str(sku),
                    "shop": self.SHOP_NAME,
                    "title": title,
                    "price": price,
                    "old_price_on_site": old_price,
                    "url": product_url,
                    "image_url": img_url,
                    "category": category_name,
                    "city": "Казахстан",
                    "sku": str(sku),
                }

                if validate_product_item(item):
                    seen_ids.add(sku)
                    products.append(item)

            return ScanResult(products, complete=last_page is not None and page_num >= last_page)

        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Ошибка парсинга {url}: {e}") from e

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
