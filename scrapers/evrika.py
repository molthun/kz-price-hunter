import re
import json
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from bs4 import BeautifulSoup

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0

class EvrikaScraper:
    SHOP_NAME = "Эврика"
    SHOP_EMOJI = "🔷"

    def __init__(self):
        self.base_url = "https://evrika.com"
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

        # Гарантируем город Астана (nur-sultan-astana) в URL
        target_url = category_url
        if "catalog/nur-sultan-astana/" not in target_url and "evrika.com/catalog/" in target_url:
            target_url = target_url.replace("evrika.com/catalog/", "evrika.com/catalog/nur-sultan-astana/")

        for page_num in range(1, max_pages + 1):
            url = target_url
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

                # Способ 1: Парсинг из структурированного React Query State в __NEXT_DATA__
                nxt = soup.select_one("script#__NEXT_DATA__")
                extracted_from_json = False
                if nxt and nxt.string:
                    try:
                        data = json.loads(nxt.string)
                        queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
                        for q in queries:
                            qk = q.get("queryKey", [])
                            if qk and qk[0] == "products":
                                raw_list = q.get("state", {}).get("data", {}).get("data", [])
                                for item in raw_list:
                                    pid = str(item.get("id") or "")
                                    name = item.get("name", "").strip()
                                    cost = item.get("cost") or 0
                                    old_cost = item.get("old_cost") or 0
                                    slug = item.get("slug", "")
                                    images = item.get("images") or []
                                    image_url = images[0] if images else ""

                                    if not pid or not name or cost <= 0:
                                        continue

                                    full_url = f"{self.base_url}/catalog/{slug}/p{pid}" if slug else f"{self.base_url}/catalog/p{pid}"

                                    products.append({
                                        "shop": self.SHOP_NAME,
                                        "id": f"evrika_{pid}",
                                        "title": name,
                                        "category": category_name,
                                        "url": full_url,
                                        "image_url": image_url,
                                        "price": int(cost),
                                        "old_price_on_site": int(old_cost) if old_cost > cost else 0,
                                        "city": "Астана"
                                    })
                                extracted_from_json = True
                                break
                    except Exception:
                        pass

                # Способ 2 (fallback): Парсинг из HTML карточек
                if not extracted_from_json:
                    cards = soup.select(".productCard-ui")
                    for c in cards:
                        title_el = c.select_one("[class*='titleOfProduct']")
                        if not title_el:
                            continue
                        title = title_el.text.strip()
                        rel_link = title_el.get("href", "")
                        if not title or not rel_link:
                            continue

                        full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                        price_el = c.select_one("[class*='currentPrice']")
                        price = parse_price(price_el.text) if price_el else 0
                        if price <= 0:
                            continue

                        old_price_el = c.select_one("[class*='oldPrice__']")
                        old_price = parse_price(old_price_el.text) if old_price_el else 0

                        img_el = c.select_one("img")
                        image_url = ""
                        if img_el:
                            src = img_el.get("src") or img_el.get("data-original-src") or ""
                            if src:
                                image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                        pid_match = re.search(r"/p(\d+)", rel_link)
                        pid = pid_match.group(1) if pid_match else rel_link.split("/")[-1]

                        products.append({
                            "shop": self.SHOP_NAME,
                            "id": f"evrika_{pid}",
                            "title": title,
                            "category": category_name,
                            "url": full_link,
                            "image_url": image_url,
                            "price": price,
                            "old_price_on_site": old_price if old_price > price else 0,
                            "city": "Астана"
                        })

            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                break

        return products
