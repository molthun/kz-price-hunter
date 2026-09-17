import re
import hashlib
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from scrapers.base import UnconfirmedEnd, PagedScraper, parse_price
from bs4 import BeautifulSoup

class ForcecomScraper(PagedScraper):
    SHOP_NAME = "Forcecom"
    SHOP_EMOJI = "⚡️"

    def __init__(self):
        self.base_url = "https://forcecom.kz"
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

        url = category_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}PAGEN_1={page_num}"

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
            cards = soup.select(".catalog-table__inner, .catalog-block__inner, .catalog-item")
            if not cards:
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            seen_pids = set()

            for c in cards:
                # 1. Ссылка и название
                link_el = c.find("a", href=re.compile(r"/model/\d+/"))
                if not link_el:
                    continue

                rel_link = link_el.get("href", "")
                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                # Название: текст ссылки, микроразметка товара или заголовок карточки
                title = link_el.text.strip()
                if not title:
                    for sel in ('[itemprop="name"]', ".catalog-block__info-title a", ".catalog-table__info-title a",
                                ".catalog-item__title", ".font_14 a", "a[title]"):
                        el = c.select_one(sel)
                        if el:
                            title = (el.get("content") or el.get("title") or el.text).strip()
                            if title:
                                break
                if not title:
                    img = c.find("img")
                    title = (img.get("alt") or "").strip() if img else ""
                if not title:
                    continue

                # ID товара
                clean_path = rel_link.split("?")[0].split("#")[0]
                pid_match = re.search(r"/model/(\d+)/", clean_path)
                pid = pid_match.group(1) if pid_match else hashlib.md5(clean_path.encode()).hexdigest()[:12]
                if pid in seen_pids:
                    continue
                seen_pids.add(pid)

                # 2. Картинка
                img_el = c.find("img")
                image_url = ""
                if img_el:
                    src = img_el.get("data-src") or img_el.get("src") or ""
                    if src:
                        if src.startswith("//"):
                            image_url = f"https:{src}"
                        elif src.startswith("/"):
                            image_url = f"{self.base_url}{src}"
                        else:
                            image_url = src

                # 3. Актуальная цена
                new_price_el = c.select_one(".price__new-val, .price__new, .price_value")
                if new_price_el:
                    current_price = parse_price(new_price_el.text)
                else:
                    price_box = c.select_one(".price")
                    current_price = parse_price(price_box.text) if price_box else 0

                if current_price <= 0:
                    continue

                # 4. Старая цена
                old_price_el = c.select_one(".price__old-val, .price__old")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": f"fc_{pid}",
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": current_price,
                    "old_price_on_site": old_price if old_price > current_price else 0,
                    "city": "Астана / Казахстан"
                })

        except UnconfirmedEnd:
            raise
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
            raise

        return products
