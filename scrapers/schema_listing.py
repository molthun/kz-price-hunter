"""Общий парсер каталогов на Bitrix/Aspro с микроразметкой schema.org (ANTS, ITMag).

Каждая карточка — блок `itemtype="http://schema.org/Product"` с полями name, url, image
и offers (price, availability). Страницы листаются параметром `PAGEN_1`; конец выдачи
подтверждается отсутствием ссылки на следующую страницу.
"""
import re
import hashlib
from typing import Any, Dict, List
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests
from scrapers.base import PagedScraper, ScanResult, parse_price

class SchemaListingScraper(PagedScraper):
    SHOP_NAME = "Магазин"
    BASE_URL = ""
    ID_PREFIX = ""
    ID_PATTERN = r"(\d+)"   # как достать артикул из ссылки на товар
    CITY = "Алматы"
    PAGE_PARAM = "PAGEN_1"
    # Магазин сортирует выдачу «сначала в наличии»: первый отсутствующий товар означает,
    # что дальше идут только отсутствующие, и обход можно завершить
    IN_STOCK_FIRST = False
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self._session = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session(impersonate="chrome124")
            self._session.headers.update({"Accept-Language": "ru-RU,ru;q=0.9"})
        return self._session

    def _fetch_page(self, category_name: str, category_url: str, page_num: int):
        url = self.page_url(category_url, page_num)
        r = self._get_session().get(url, timeout=45)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return self.parse_page(r.text, category_name, page_num, page_url=url)

    @staticmethod
    def _prop(block, name: str) -> str:
        el = block.select_one(f'[itemprop="{name}"]')
        if not el:
            return ""
        return (el.get("content") or el.get("href") or el.get("src") or el.get_text(" ", strip=True) or "").strip()

    @classmethod
    def parse_page(cls, html: str, category_name: str, page_num: int, page_url: str = "") -> ScanResult:
        soup = BeautifulSoup(html, "html.parser")
        products: List[Dict[str, Any]] = []
        seen = set()
        out_of_stock = 0
        blocks = soup.select('[itemtype="http://schema.org/Product"], [itemtype="https://schema.org/Product"]')

        for block in blocks:
            url = cls._prop(block, "url")
            title = cls._prop(block, "name")
            price = parse_price(cls._prop(block, "price"))
            if not url or not title or price <= 0:
                continue
            # Товары не в наличии в выдаче остаются, но цена по ним не актуальна
            if "OutOfStock" in cls._prop(block, "availability"):
                out_of_stock += 1
                continue

            url = urljoin(cls.BASE_URL, url)
            match = re.search(cls.ID_PATTERN, url)
            if match:
                pid = match.group(1)
            else:
                # У части товаров (например, у ANTS) нет своей страницы: ссылка ведет на категорию.
                # Цена и наличие при этом настоящие — сохраняем со стабильным ID по названию
                pid = "u" + hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
                url = page_url or url
            if pid in seen:
                continue
            seen.add(pid)

            old_el = block.select_one(".price__old-val, .price__old, .price_old, [class*='old-price']")
            old_price = parse_price(old_el.get_text()) if old_el else 0

            products.append({
                "shop": cls.SHOP_NAME,
                "id": f"{cls.ID_PREFIX}_{pid}",
                "title": title,
                "category": category_name,
                "url": url,
                "image_url": urljoin(cls.BASE_URL, cls._prop(block, "image")) if cls._prop(block, "image") else "",
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": cls.CITY,
            })

        if cls.IN_STOCK_FIRST and out_of_stock:
            return ScanResult(products, complete=True)
        # Сравниваем номер страницы точно: подстрока «PAGEN_1=14» совпала бы и с «PAGEN_1=140»
        has_next = False
        for link in soup.select(f'a[href*="{cls.PAGE_PARAM}="]'):
            values = parse_qs(urlparse(link["href"]).query).get(cls.PAGE_PARAM, [])
            if any(v.isdigit() and int(v) > page_num for v in values):
                has_next = True
                break
        # Конец выдачи подтвержден, если страница с карточками загрузилась и ссылки дальше нет.
        # Последняя страница может содержать только служебный блок без цены (так бывает у ITMag)
        return ScanResult(products, complete=bool(products or blocks) and not has_next)
