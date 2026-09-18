import re
import uuid
import asyncio
from typing import List, Dict, Any
from scrapers import http as requests
from scrapers.base import ScanResult, PagedScraper, parse_price, price_value

class MechtaScraper(PagedScraper):
    SHOP_NAME = "Мечта"
    SHOP_EMOJI = "🟣"

    def __init__(self):
        self.base_url = "https://www.mechta.kz"
        self.api_url = "https://www.mechta.kz/api/v3/catalog/products"
        self.device_id = str(uuid.uuid4())
        self.session = None

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
            try:
                self.session.get(self.base_url, timeout=10)
            except Exception:
                pass
        return self.session

    def _extract_slug(self, category_url: str) -> str:
        """Извлекает slug категории из переданного URL или возвращает сам slug."""
        if category_url.startswith("http"):
            match = re.search(r"/(?:section|category)/([^/?#]+)", category_url)
            if match:
                return match.group(1)
            parts = [p for p in category_url.split("/") if p and not p.startswith("http")]
            if parts:
                return parts[-1]
        return category_url

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []
        slug = self._extract_slug(category_url)
        session = self._get_session()

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.base_url}/section/{slug}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Mechta-Device-Id": self.device_id,
            "x-city-code": "astana",
            "Accept-Language": "ru",
        }

        params = {
            "slug": slug,
            "page": page_num,
            "pageSize": 24,
            "orderBy": "sort",
            "direction": "desc"
        }

        try:
            r = session.get(self.api_url, headers=headers, params=params, timeout=15)
            if r.status_code == 204:
                return ScanResult(complete=True)  # категория закончилась: API отдает 204 после последней страницы
            if r.status_code != 200:
                if r.status_code in (403, 422):
                    self.session = None
                    session = self._get_session()
                    r = session.get(self.api_url, headers=headers, params=params, timeout=15)
                if r.status_code == 204:
                    return ScanResult(complete=True)
                if r.status_code != 200:
                    print(f"[{self.SHOP_NAME}] Ошибка HTTP {r.status_code} для категории {slug}")
                    raise RuntimeError(f"HTTP {r.status_code}")

            data = r.json()
            raw_items = data["products"]
            if not raw_items:
                return ScanResult(complete=True)

            for item in raw_items:
                pid = item.get("id") or str(item.get("code", ""))
                name = item.get("name", "").strip()
                item_slug = item.get("slug", "")
                if not name or not pid:
                    continue

                # Цены
                prices = item.get("prices") or {}
                final_price = price_value(prices.get("finalPrice"))
                base_price = price_value(prices.get("basePrice"))

                if final_price <= 0:
                    continue

                # Ссылка
                product_url = f"{self.base_url}/product/{item_slug}/" if item_slug else f"{self.base_url}/product/{pid}/"

                # Изображение
                images = item.get("images") or []
                image_url = images[0] if images else ""

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": f"mechta_{pid}",
                    "title": name,
                    "category": category_name,
                    "url": product_url,
                    "image_url": image_url,
                    "price": final_price,
                    "old_price_on_site": base_price if base_price > final_price else 0,
                    "city": "Астана"
                })

        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка при парсинге {slug} (стр. {page_num}): {e}")
            raise

        return products
