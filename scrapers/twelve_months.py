"""Скрапер гипермаркета стройматериалов и инструментов 12 Месяцев (12.kz).

Платформа: AdvantShop.
Категории: электроинструменты, ручной инструмент, сантехника, садовая техника, бытовая химия.
Пагинация: ?page=N. При выходе за диапазон страниц возвращается HTTP 404.
"""
import re
from typing import List, Dict, Any
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item, UnconfirmedEnd


class TwelveMonthsScraper(PagedScraper):
    SHOP_NAME = "12 Месяцев"
    SHOP_EMOJI = "🛠"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://12.kz"
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
            r = None
            for attempt in range(2):
                try:
                    r = session.get(url, headers=self.headers, timeout=20)
                    if r.status_code in (500, 502, 503, 504) and attempt == 0:
                        import time
                        time.sleep(1.0)
                        continue
                    break
                except Exception:
                    if attempt == 0:
                        import time
                        time.sleep(1.0)
                        continue
                    raise
            if r is None:
                raise RuntimeError(f"Не удалось получить ответ от {url}")
            if r.status_code == 404:
                # За последней страницей — 404; на первой странице это ошибка категории
                if page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней страницы")
                raise RuntimeError(f"HTTP 404 on {url}")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {url}")

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".products-view-item, .js-products-view-item")
            if not cards:
                # Источник не даёт признака конца каталога: пустая страница — «ограничен», а на
                # первой странице — видимая ошибка, а не тихий ноль (R-M01)
                raise UnconfirmedEnd("карточки не найдены")

            seen_ids = set()

            for c in cards:
                # Проверка наличия (если явно указано "нет в наличии" или "под заказ")
                card_text = c.get_text(separator=" ", strip=True).lower()
                if "нет в наличии" in card_text:
                    continue

                # ID товара
                pid = c.get("data-product-id") or c.get("data-offer-id") or ""

                # Наименование и ссылка
                name_el = c.select_one(".products-view-name a, a.products-view-name-link")
                if not name_el:
                    name_el = c.select_one("a[href*='/products/']")
                if not name_el:
                    continue

                title = name_el.get_text(strip=True)
                if not title:
                    continue

                rel_url = name_el.get("href", "")
                if not rel_url:
                    continue
                product_url = urljoin(self.base_url, rel_url)

                if not pid:
                    # Извлекаем артикул из URL (/products/0434357)
                    m = re.search(r"/products/([^/?#]+)", rel_url)
                    pid = m.group(1) if m else rel_url.strip("/").split("/")[-1]

                if pid in seen_ids:
                    continue

                # Цена товара (учитываем возможную акционную цену)
                price = None
                old_price = None

                price_new_el = c.select_one(".price-new .price-number")
                if price_new_el:
                    price = price_value(price_new_el.get_text())
                    old_price_el = c.select_one(".price-old .price-number")
                    if old_price_el:
                        old_price = price_value(old_price_el.get_text())
                else:
                    price_el = c.select_one(".price-number, .products-view-price .price, .price")
                    if price_el:
                        price = price_value(price_el.get_text())

                if not price or price <= 0:
                    continue

                if old_price and old_price <= price:
                    old_price = None

                # Изображение
                img_url = ""
                img_el = c.select_one("img.products-view-picture, figure.products-view-pictures img, img")
                if img_el:
                    raw_src = img_el.get("src") or img_el.get("data-src") or img_el.get("data-ng-src") or ""
                    if raw_src and not raw_src.startswith("data:"):
                        img_url = urljoin(self.base_url, raw_src)

                # Артикул
                art_m = re.search(r"Артикул:\s*([^\s,]+)", card_text, re.IGNORECASE)
                sku = art_m.group(1) if art_m else ""

                item = {
                    "id": str(pid),
                    "shop": self.SHOP_NAME,
                    "title": title,
                    "price": price,
                    "old_price_on_site": old_price,
                    "url": product_url,
                    "image_url": img_url,
                    "category": category_name,
                    "city": "Казахстан",
                    "sku": sku,
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
