import re
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from scrapers.base import UnconfirmedEnd, PagedScraper, parse_price
from bs4 import BeautifulSoup

class MoonScraper(PagedScraper):
    SHOP_NAME = "Moon.kz"
    SHOP_EMOJI = "🚀"

    def __init__(self):
        self.base_url = "https://moon.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    # У Moon (Bitrix/Aspro) страницы листаются через PAGEN_1
    PAGE_PARAM = "PAGEN_1"

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        url = category_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}PAGEN_1={page_num}"

        try:
            r = requests.get(
                url,
                headers=self.headers,
                impersonate="chrome124",
                verify=False,
                timeout=15
            )
            if r.status_code == 404 and page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")

            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select(".catalog_item_wrapp, .item_block")
            if not cards:
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            seen_ids = set()

            for c in cards:
                pid = c.get("data-id") or ""
                if pid and pid in seen_ids:
                    continue

                title_el = c.select_one(".item-title a, a.dark_link")
                if not title_el:
                    continue
                title = title_el.text.strip()
                rel_link = title_el.get("href", "")
                if not title or not rel_link:
                    continue

                if not pid:
                    pid = rel_link.strip("/").split("/")[-1]
                seen_ids.add(pid)

                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                # Цены
                price_el = c.select_one(".price_value, .values_wrapper, .price")
                price = parse_price(price_el.text) if price_el else 0
                if price <= 0:
                    continue

                old_price_el = c.select_one(".price--old, .price_old, strike")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                # Изображение
                img_el = c.select_one(".image_wrapper_block img, img")
                image_url = ""
                if img_el:
                    src = img_el.get("data-src") or img_el.get("src") or ""
                    if src and not src.endswith("empty.png"):
                        if src.startswith("//"):
                            image_url = f"https:{src}"
                        elif src.startswith("/"):
                            image_url = f"{self.base_url}{src}"
                        else:
                            image_url = src

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": f"moon_{pid}",
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": price,
                    "old_price_on_site": old_price if old_price > price else 0,
                    "city": "Астана"
                })

        except UnconfirmedEnd:
            raise
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
            raise

        return products
