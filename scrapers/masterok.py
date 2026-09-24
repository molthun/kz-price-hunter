"""Скрапер гипермаркета оборудования и инструментов «MasterOK» (masterok.kz).

Платформа: 1C-Bitrix HTML со Schema.org Product/Offer микроразметкой.
Селектор карточек: div.catalog-item-card.
Пагинация: ?PAGEN_1=N; последняя страница берётся из ссылок пагинатора, а при их отсутствии
выводится из счётчика «Товаров: N» в блоке div.count_items.

Зачем подтверждать конец каталога: без этого обход любой категории возвращался limited, даже когда
все товары собраны. По правилу P02 неполный обход не обучает норму источника, поэтому у магазина
никогда не строился baseline, а в мониторинге он навсегда оставался «собран не полностью» —
неотличимо от настоящего обрыва.
"""
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    UnconfirmedEnd,
    ScanResult,
    pagination_last_page,
    price_value,
    validate_product_item,
)


class MasterOkScraper(PagedScraper):
    SHOP_NAME = "MasterOK"
    SHOP_EMOJI = "🛠"
    # Сайт отвечает пустыми страницами, если листать быстро: при 0.4 с полный обход крупного
    # раздела начинал терять товары примерно с третьей страницы. Пауза важнее скорости —
    # оборванный обход всё равно пришлось бы повторять.
    PAGE_DELAY_SECONDS = 1.2

    def __init__(self):
        self.base_url = "https://masterok.kz"
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
            url = f"{url}{separator}PAGEN_1={page_num}"

        session = self._get_session()
        try:
            resp = session.get(url, headers=self.headers, timeout=20)
        except Exception as e:
            # Сбой сети — это ошибка страницы, а не пустая категория: пусть базовый класс
            # запишет причину, иначе обход выглядел бы просто «ничего не нашлось»
            raise RuntimeError(f"Не удалось загрузить {url}: {type(e).__name__}") from e
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".catalog-item-card, div[data-entity='item']")

        for card in cards:
            title_el = card.select_one("span[itemprop='name'], a.item-title, .item-title")
            if not title_el:
                continue
            title = title_el.text.strip()
            if not title:
                continue

            link_el = card.select_one("a.item-title, a[itemprop='url'], .item-image a")
            href = link_el.get("href") or "" if link_el else ""
            if not href:
                continue
            product_url = urljoin(self.base_url, href)

            # ID товара: id="bx_40480796_40732_52eccb44..." -> 40732
            card_id_attr = card.get("id") or ""
            m_id = re.search(r"bx_\d+_(\d+)_", card_id_attr)
            if m_id:
                item_id = m_id.group(1)
            else:
                m_slug = re.search(r"/catalog/[^/]+/([^/]+)/?", href)
                item_id = m_slug.group(1) if m_slug else str(abs(hash(product_url)) % 100000000)

            # Артикул / SKU
            article_el = card.select_one(".article")
            sku = item_id
            if article_el:
                m_art = re.search(r"[:\s]+(\S+)", article_el.text)
                if m_art:
                    sku = m_art.group(1).strip()

            # Текущая цена
            price = 0
            price_meta = card.select_one("meta[itemprop='price']")
            if price_meta and price_meta.get("content"):
                price = price_value(price_meta.get("content"))
            if price <= 0:
                price_span = card.select_one("span.catalog-item-price, .item-price")
                if price_span:
                    price = price_value(price_span.text)
            if price <= 0:
                continue

            # Старая цена
            old_price = 0
            old_price_el = card.select_one("span.catalog-item-price-old, .catalog-item-price-old")
            if old_price_el:
                old_price = price_value(old_price_el.text)
                if old_price <= price:
                    old_price = 0

            # Изображение
            img_url = ""
            img_meta = card.select_one("meta[itemprop='image']")
            if img_meta and img_meta.get("content"):
                src = img_meta.get("content")
                img_url = urljoin(self.base_url, src)
            if not img_url:
                img_el = card.select_one("img.item_img, .item-image img")
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        img_url = urljoin(self.base_url, src)

            # Описание
            desc_el = card.select_one("div[itemprop='description'], .item-desc")
            desc = desc_el.text.strip() if desc_el else ""

            item = {
                "id": str(item_id),
                "sku": str(sku),
                "shop": self.SHOP_NAME,
                "title": title,
                "category": category_name,
                "url": product_url,
                "image_url": img_url,
                "price": price,
                "old_price_on_site": old_price,
                "description": desc,
                "city": "Алматы",
            }
            if validate_product_item(item):
                products.append(item)

        if cards and not products:
            # Страница прочитана, но все её товары — «цена по запросу»: у masterok.kz такого
            # оборудования много. Это не пустая страница и не сбой, поэтому и не ошибка; но и
            # конца каталога мы не видели, так что обход честно остаётся неполным.
            raise UnconfirmedEnd("Страница без цен: конец каталога не подтверждён")

        return ScanResult(products, complete=self._is_last_page(soup, category_url, page_num,
                                                               len(cards)))

    def _is_last_page(self, soup, category_url: str, page_num: int, cards_on_page: int) -> bool:
        """Последняя ли это страница категории. Неизвестно — значит нет, а не «да»."""
        last = pagination_last_page(str(soup), category_url, param="PAGEN_1")
        if last:
            return page_num >= last
        # Пагинатора нет: либо каталог уместился на одной странице, либо разметка сменилась.
        # Счётчик магазина отличает одно от другого.
        total = self._declared_total(soup)
        if total is None:
            return False
        return page_num == 1 and cards_on_page >= total

    @staticmethod
    def _declared_total(soup) -> Optional[int]:
        """Число товаров, объявленное самим магазином: <div class="count_items">…<span>690</span>."""
        counter = soup.select_one("div.count_items span")
        if not counter:
            return None
        digits = re.sub(r"[^0-9]", "", counter.get_text())
        return int(digits) if digits else None

    def close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
