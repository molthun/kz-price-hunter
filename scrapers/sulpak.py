import re
import hashlib
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from scrapers.base import UnconfirmedEnd, PagedScraper, parse_price
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

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        url = category_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}page={page_num}"

        try:
            r = requests.get(
                url,
                headers=self.headers,
                cookies=self.cookies,
                impersonate="chrome124",
                timeout=15
            )
            if r.status_code == 404 and page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".product__item, .goods-tiles")
            if not cards:
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            seen_codes = set()

            for c in cards:
                # ID и цена из data-атрибутов
                pid = c.get("data-code") or c.get("data-product-id") or ""
                title = c.get("data-name") or ""
                data_price = c.get("data-price") or ""

                if pid and pid in seen_codes:
                    continue
                if pid:
                    seen_codes.add(pid)

                # Ссылка
                link_el = c.find("a", href=lambda h: h and ("/g/" in h or "/p/" in h))
                if not link_el:
                    continue

                rel_link = link_el.get("href", "")
                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                if not title:
                    title = link_el.text.strip()
                if not title:
                    continue

                # Картинка
                img_el = c.find("img")
                image_url = ""
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                # Текущая цена
                if data_price:
                    try:
                        current_price = int(float(data_price))
                    except ValueError:
                        current_price = 0
                else:
                    price_el = c.select_one(".product__item-price, .price")
                    current_price = parse_price(price_el.text) if price_el else 0

                if current_price <= 0:
                    continue

                # Старая цена
                old_price_el = c.select_one(".product__item-price-old, .old-price")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                # ID товара
                clean_path = rel_link.split("?")[0].split("#")[0]
                safe_id = pid if pid else (re.search(r"[-_](\d+)$", clean_path).group(1) if re.search(r"[-_](\d+)$", clean_path) else hashlib.md5(clean_path.encode()).hexdigest()[:12])

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": f"sulpak_{safe_id}",
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

        return products
