import re
import hashlib
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from scrapers.base import ScanResult, parse_price

class FourMobileScraper:
    SHOP_NAME = "4mobile"
    SHOP_EMOJI = "📱"

    def __init__(self):
        self.base_url = "https://4mobile.pages.dev"
        self.api_url = "https://4mobile.pages.dev/api/data"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://4mobile.pages.dev/",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    async def scrape(self, category_name: str, category_url: str, max_pages=None) -> List[Dict[str, Any]]:
        # У 4mobile один JSON со всем каталогом, постраничный обход не нужен
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def _scrape_sync(self, category_name: str, category_url: str, max_pages=None) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        try:
            r = requests.get(
                self.api_url,
                headers=self.headers,
                impersonate="chrome124",
                timeout=15
            )
            data = None
            if r.status_code == 200:
                try:
                    data = r.json()
                except Exception:
                    pass

            # Fallback к парсингу HTML, если /api/data недоступен
            if not data:
                r_html = requests.get(
                    self.base_url,
                    headers=self.headers,
                    impersonate="chrome124",
                    timeout=15
                )
                if r_html.status_code == 200:
                    import json
                    m = re.search(r"window\.MOBILE_DATA\s*=\s*(\{.*?\});\s*(?:window\.__BOOT|</script>)", r_html.text, re.DOTALL)
                    if m:
                        data = json.loads(m.group(1))

            if not data or not any(isinstance(data.get(k), list) for k in ("price", "wiwu")):
                raise ValueError("Каталог 4mobile недоступен или изменил формат")

            target_filter = category_name.lower().strip()

            # Собираем все категории из price и wiwu
            all_groups = []
            if "price" in data and isinstance(data["price"], list):
                all_groups.extend(data["price"])
            if "wiwu" in data and isinstance(data["wiwu"], list):
                all_groups.extend(data["wiwu"])

            for group in all_groups:
                cat_title = group.get("cat", "Гаджеты")
                items = group.get("items", [])

                # Если указана конкретная категория для парсинга, фильтруем
                if target_filter and not target_filter.startswith("4mobile: 🔥 все") and not target_filter.startswith("4mobile: все"):
                    clean_filter = target_filter.replace("4mobile:", "").strip().lower()
                    if clean_filter not in cat_title.lower() and cat_title.lower() not in clean_filter:
                        continue

                for item in items:
                    if not item or len(item) < 2:
                        continue
                    name = item[0].strip()
                    price_raw = item[1].strip()

                    price = parse_price(price_raw)
                    if price <= 0:
                        continue

                    pid_raw = f"4m_{cat_title}_{name}"
                    pid = hashlib.md5(pid_raw.encode("utf-8")).hexdigest()[:12]

                    # Проверяем, есть ли бренд в названии
                    full_title = name
                    if "wiwu" in cat_title.lower() and "wiwu" not in full_title.lower():
                        full_title = f"WiWU {full_title}"

                    products.append({
                        "shop": self.SHOP_NAME,
                        "id": f"4mobile_{pid}",
                        "title": full_title,
                        "category": cat_title,
                        "url": f"{self.base_url}/#catalog",
                        "image_url": "https://4mobile.pages.dev/favicon.ico",
                        "price": price,
                        "old_price_on_site": 0,
                        "city": "Астана"
                    })

        except Exception as e:
            return ScanResult(products, error=type(e).__name__)

        return ScanResult(products, complete=bool(products), error=None if products else "Пустой каталог")

    async def search_live(self, query: str) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._search_live_sync, query)

    def _search_live_sync(self, query: str) -> List[Dict[str, Any]]:
        all_prods = self._scrape_sync("4mobile: 🔥 Все товары", self.api_url, 1)
        tokens = query.lower().split()
        matches = []
        for p in all_prods:
            t = p["title"].lower()
            if all(tok in t for tok in tokens):
                matches.append(p)
        return matches
