"""Скрапер онлайн-супермаркета «Arbuz.kz» (arbuz.kz).

Платформа: SSR HTML на базе Vue/Nuxt.
Селектор карточек: article.product-card, article.product-item.
Пагинация: ?page=N (по 40 карточек на страницу; пустая страница при завершении).
"""
import re
import hashlib
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item, UnconfirmedEnd


class ArbuzScraper(PagedScraper):
    SHOP_NAME = "Arbuz"
    SHOP_EMOJI = "🍉"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://arbuz.kz"
        self.session: Optional[requests.Session] = None
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
        # Ошибки HTTP и сети — ошибка обхода, а не пустая страница (раньше маскировались)
        resp = session.get(url, headers=self.headers, timeout=20)
        if resp.status_code == 404 and page_num > 1:
            raise UnconfirmedEnd("HTTP 404 после последней страницы")
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} on {url}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("article.product-card, article.product-item")
        if not cards:
            # Признака конца каталога в HTML нет: «ограничен», на первой странице — ошибка (R-M01)
            raise UnconfirmedEnd("карточки не найдены")

        for card in cards:
            title_el = card.select_one(".product-card__title")
            if not title_el:
                continue
            title = title_el.text.strip()
            if not title:
                continue

            href = title_el.get("href") or ""
            if not href:
                link_el = card.select_one("a.product-card__link, a[href*='/catalog/item/']")
                if link_el:
                    href = link_el.get("href") or ""

            product_url = urljoin(self.base_url, href)

            # Извлечение ID из URL: /item/351031-... -> 351031
            m = re.search(r"/item/(\d+)", href)
            if m:
                item_id = m.group(1)
            else:
                # Fallback: детерминированный стабильный идентификатор из URL (P00/P01)
                item_id = hashlib.sha256(product_url.encode("utf-8")).hexdigest()[:12]

            # Текущая цена
            price_el = card.select_one(".product-card__price b, .product-card__price strong")
            if not price_el:
                price_el = card.select_one(".product-card__price")
            price = price_value(price_el.text) if price_el else 0
            if price <= 0:
                continue

            # Старая цена (скидка)
            old_price = 0
            old_price_el = card.select_one(
                ".product-card__price s, .product-card__price-previous, "
                ".product-card__price del, .product-card__old-price"
            )
            if old_price_el:
                old_price = price_value(old_price_el.text)
                if old_price <= price:
                    old_price = 0

            # Фотография
            img_url = ""
            img_el = card.select_one("img.product-card__img, .product-card__img, img")
            if img_el:
                v_lazy = img_el.get("v-lazy") or ""
                clean_lazy = v_lazy.strip("'\"")
                if clean_lazy.startswith("http"):
                    img_url = clean_lazy.replace("%w", "360").replace("%h", "360")
                else:
                    src = img_el.get("data-src") or img_el.get("src") or ""
                    if src and not src.endswith("placeholder.svg"):
                        img_url = urljoin(self.base_url, src)

            item = {
                "id": str(item_id),
                "sku": str(item_id),
                "shop": self.SHOP_NAME,
                "title": title,
                "category": category_name,
                "url": product_url,
                "image_url": img_url,
                "price": price,
                "old_price_on_site": old_price,
                "city": "Алматы",
            }
            if validate_product_item(item):
                products.append(item)

        return products

    def close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
