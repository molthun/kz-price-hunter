"""Скрапер сети электроники «Alser» (alser.kz).

Каталог отдаётся сервером целиком, но небольшой: сайт сам печатает «N товаров» в блоке
p.total-text, и это же число подтверждает API магазина. Счётчик читается, чтобы отличить
полностью собранную маленькую категорию от обхода, оборвавшегося на первой странице.

Без этого любая категория возвращалась limited: вторая страница карточек не содержит, адаптер
бросал UnconfirmedEnd, и базовый класс помечал обход неполным. По правилу P02 неполные обходы
не обучают норму источника, поэтому baseline у магазина не строился никогда.
"""
import math
import re
from typing import Any, Dict, List, Optional
from scrapers import http as requests
from scrapers.base import slug_id, UnconfirmedEnd, PagedScraper, ScanResult, parse_price
from bs4 import BeautifulSoup


class AlserScraper(PagedScraper):
    SHOP_NAME = "Alser"
    SHOP_EMOJI = "🟡"

    def __init__(self):
        self.base_url = "https://alser.kz"
        # Объявленный магазином итог и размер первой страницы — по категории, чтобы параллельный
        # обход разных категорий одним экземпляром не путал их между собой
        self._expected_pages: Dict[str, int] = {}
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        # Гарантируем город Астана в URL пути
        base_cat_url = category_url
        if "/astana/" not in base_cat_url and "alser.kz/c/" in base_cat_url:
            base_cat_url = base_cat_url.replace("alser.kz/c/", "alser.kz/astana/c/")

        url = base_cat_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}page={page_num}"

        try:
            r = requests.get(
                url,
                headers=self.headers,
                impersonate="chrome124",
                timeout=15
            )
            if r.status_code == 404 and page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("article.product-card")
            if not cards:
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            seen_urls = set()

            for c in cards:
                # Ссылка
                link_el = c.find_parent("a") or c.select_one("a[href*='/p/']")
                if not link_el:
                    continue
                rel_link = link_el.get("href", "")
                if not rel_link or rel_link in seen_urls:
                    continue
                seen_urls.add(rel_link)

                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                # Название
                title_el = c.select_one(".product-card__title")
                title = title_el.text.strip() if title_el else ""
                if not title:
                    title = link_el.get("title", "") or link_el.text.strip()
                if not title:
                    continue

                # Цены
                price_el = c.select_one(".info-container-product-price")
                current_price = parse_price(price_el.text) if price_el else 0
                if current_price <= 0:
                    continue

                old_price_el = c.select_one(".product-card__old-price")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                # Изображение
                img_el = c.select_one("img.object-contain, .carousel img, img")
                image_url = ""
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                # ID товара
                slug = rel_link.split("/p/")[-1].strip("/")
                # Раньше slug обрезался до 40 символов — длинные адреса с общим началом совпадали
                pid = slug_id("alser", slug)

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": pid,
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": current_price,
                    "old_price_on_site": old_price if old_price > current_price else 0,
                    "city": "Астана"
                })

        except UnconfirmedEnd:
            raise
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
            raise

        return ScanResult(products,
                          complete=self._is_last_page(soup, category_url, page_num, len(cards)))

    def _is_last_page(self, soup, category_url: str, page_num: int, cards_on_page: int) -> bool:
        """Последняя ли это страница. Пока итог неизвестен — считаем, что нет."""
        if page_num == 1:
            total = self._declared_total(soup)
            if total is None or cards_on_page <= 0:
                self._expected_pages.pop(category_url, None)
                return False
            self._expected_pages[category_url] = math.ceil(total / cards_on_page)
        expected = self._expected_pages.get(category_url)
        return bool(expected) and page_num >= expected

    @staticmethod
    def _declared_total(soup) -> Optional[int]:
        """Число товаров по версии самого магазина: <p class="… total-text">10 товаров</p>."""
        counter = soup.select_one("p.total-text, .total-text")
        if not counter:
            return None
        match = re.search(r"(\d[\d\s\u00a0]*)", counter.get_text())
        if not match:
            return None
        return int(re.sub(r"[^0-9]", "", match.group(1)))
