"""Скрапер гипермаркета товаров для дома, ремонта и стройки «Комфорт» (komfort.kz).

Платформа: Bitrix Aspro.
Категории: электроинструменты, стройматериалы, отделка, сантехника, сад, товары для дома, мелкая бытовая техника.
Пагинация: ?PAGEN_1=N.
"""
from typing import List, Dict, Any
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item, UnconfirmedEnd


class KomfortScraper(PagedScraper):
    SHOP_NAME = "Комфорт"
    SHOP_EMOJI = "🛋"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://komfort.kz"
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
            url = f"{url}{separator}PAGEN_1={page_num}"

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
            cards = soup.select(".catalog-block-view__item")
            if not cards:
                # Источник не даёт признака конца каталога: пустая страница — «ограничен», а на
                # первой странице — видимая ошибка, а не тихий ноль (R-M01)
                raise UnconfirmedEnd("карточки не найдены")

            seen_ids = set()

            for c in cards:
                pid = c.get("data-id") or ""
                if pid and pid in seen_ids:
                    continue

                # Наименование и ссылка
                title_el = c.select_one(".item-title a, a.dark_link")
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                rel_url = title_el.get("href", "").strip()
                if not title or not rel_url:
                    continue

                product_url = urljoin(self.base_url, rel_url)

                # ID товара fallback
                if not pid:
                    parts = [p for p in rel_url.strip("/").split("/") if p.isdigit()]
                    pid = parts[-1] if parts else rel_url.strip("/").split("/")[-1]

                # Цена товара
                price = None
                price_el = c.select_one(".price[data-value], .cost .price[data-value]")
                if price_el and price_el.get("data-value"):
                    try:
                        price = int(float(price_el["data-value"]))
                    except (ValueError, TypeError):
                        pass

                if price is None or price <= 0:
                    val_wrapper = c.select_one(".price_value, .values_wrapper, .price")
                    if val_wrapper:
                        price = price_value(val_wrapper.get_text())

                if not price or price <= 0:
                    continue

                # Старая цена
                old_price = None
                old_el = c.select_one(".price_old, [data-value-old], .price.discount")
                if old_el:
                    if old_el.get("data-value-old"):
                        try:
                            old_price = int(float(old_el["data-value-old"]))
                        except (ValueError, TypeError):
                            pass
                    if not old_price:
                        old_price = price_value(old_el.get_text())
                if old_price and old_price <= price:
                    old_price = None

                # Изображение
                img_url = ""
                img_el = c.select_one(".thumb img, img")
                if img_el:
                    img_src = img_el.get("data-src") or img_el.get("src") or ""
                    if img_src and not img_src.startswith("data:"):
                        img_url = urljoin(self.base_url, img_src)

                item = {
                    "id": str(pid),
                    "shop": self.SHOP_NAME,
                    "title": title,
                    "price": price,
                    "old_price_on_site": old_price,
                    "url": product_url,
                    "image_url": img_url,
                    "category": category_name,
                    "city": "Алматы",
                }

                if validate_product_item(item):
                    seen_ids.add(pid)
                    products.append(item)

            return products

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
