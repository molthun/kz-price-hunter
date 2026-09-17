import re
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from bs4 import BeautifulSoup

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0

class AlserScraper:
    SHOP_NAME = "Alser"
    SHOP_EMOJI = "🟡"

    def __init__(self):
        self.base_url = "https://alser.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    async def scrape(self, category_name: str, category_url: str, max_pages: int = 1) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        # Гарантируем город Астана в URL пути
        base_cat_url = category_url
        if "/astana/" not in base_cat_url and "alser.kz/c/" in base_cat_url:
            base_cat_url = base_cat_url.replace("alser.kz/c/", "alser.kz/astana/c/")

        for page_num in range(1, max_pages + 1):
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
                if r.status_code != 200:
                    break

                soup = BeautifulSoup(r.text, "html.parser")
                cards = soup.select("article.product-card")
                if not cards:
                    break

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
                    pid = f"alser_{slug[:40]}"

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

            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                break

        return products
