"""Скрапер гипермаркета оборудования и инструментов «MasterOK» (masterok.kz).

Платформа: 1C-Bitrix HTML со Schema.org Product/Offer микроразметкой.
Селектор карточек: div.catalog-item-card.
Пагинация: ?PAGEN_1=N.
"""
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item


class MasterOkScraper(PagedScraper):
    SHOP_NAME = "MasterOK"
    SHOP_EMOJI = "🛠"
    PAGE_DELAY_SECONDS = 0.4

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
            if resp.status_code != 200:
                return []
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка загрузки {url}: {e}")
            return []

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

        return products

    def close(self):
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
