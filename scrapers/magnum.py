"""Скрапер розничной сети «Магнум» (magnum.kz).

Платформа: Strapi REST API (каталог скидок и акций супермаркетов Magnum).
Эндпоинты:
- https://magnum.kz:1337/api/new-product?city={city}&cunt=500{&category=slug}
- https://magnum.kz:1337/api/new-product-catalog?city={city}&locale=ru
"""
import asyncio
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlsplit, parse_qs

from scrapers import http as requests
from scrapers.base import ScanResult, price_value, validate_product_item

CITY_SLUG_MAP = {
    "алматы": "almaty",
    "астана": "astana",
    "караганда": "karaganda",
    "шымкент": "shymkent",
    "кызылорда": "kyzylorda",
    "петропавловск": "petropavlovsk",
    "талдыкорган": "taldykorgan",
    "тараз": "taraz",
    "туркестан": "turkestan",
    "усть-каменогорск": "ust-kamenogorsk",
}


class MagnumScraper:
    SHOP_NAME = "Магнум"
    SHOP_EMOJI = "🔴"
    PAGE_DELAY_SECONDS = 0.3

    def __init__(self, city: str = "Алматы"):
        self.base_url = "https://magnum.kz"
        self.api_url = "https://magnum.kz:1337/api/new-product"
        self.city_display = city or "Алматы"
        norm_city = (city or "Алматы").strip().lower()
        self.city_slug = CITY_SLUG_MAP.get(norm_city, "almaty")
        self.session: Optional[requests.Session] = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://magnum.kz/catalog",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    async def scrape(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        # Извлекаем slug категории из URL (например, ?category=bakaleia)
        slug = None
        if category_url:
            parsed = urlsplit(category_url)
            qs = parse_qs(parsed.query)
            cat_vals = qs.get("category")
            if cat_vals and cat_vals[0]:
                slug = cat_vals[0].strip()

        api_url = f"{self.api_url}?city={self.city_slug}&cunt=500"
        if slug:
            api_url += f"&category={slug}"

        session = self._get_session()
        last_err = None

        for attempt in range(2):
            try:
                resp = session.get(api_url, headers=self.headers, timeout=20)
                if resp.status_code in (500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0)
                    continue
                if resp.status_code != 200:
                    return ScanResult(products, error=f"HTTP {resp.status_code}")
                data = resp.json()
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                return ScanResult(products, error=type(e).__name__)

        if not isinstance(data, list):
            return ScanResult(products, error="Invalid API payload format")

        for raw in data:
            if not isinstance(raw, dict):
                continue
            raw_id = raw.get("id")
            if not raw_id:
                continue

            title = (raw.get("name") or "").strip()
            price = price_value(raw.get("final_price"))
            old_price = price_value(raw.get("start_price"))

            img_path = raw.get("image") or ""
            if isinstance(img_path, str) and img_path.strip():
                img_path = img_path.strip()
                if img_path.startswith("http"):
                    image_url = img_path
                else:
                    image_url = f"https://magnum.kz:1337{img_path}"
            elif isinstance(img_path, list) and img_path and isinstance(img_path[0], dict):
                first_img = img_path[0].get("url") or ""
                image_url = f"https://magnum.kz:1337{first_img}" if first_img and not first_img.startswith("http") else first_img
            else:
                image_url = ""

            product_url = f"{self.base_url}/products/{raw_id}"

            discount_info = raw.get("discount_type")
            description = ""
            if isinstance(discount_info, dict):
                description = str(discount_info.get("conditions") or "").strip()

            item = {
                "shop": self.SHOP_NAME,
                "id": f"magnum_{raw_id}",
                "title": title,
                "category": category_name,
                "url": product_url,
                "image_url": image_url,
                "description": description,
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": self.city_display,
            }

            validated = validate_product_item(item, default_shop=self.SHOP_NAME)
            if validated:
                products.append(validated)

        is_complete = bool(products)
        err = None if is_complete else "Пустая выдача: полнота не подтверждена"
        return ScanResult(products, complete=is_complete, error=err)

    async def search_live(self, query: str, city: Optional[str] = None) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._search_live_sync, query, city)

    def _search_live_sync(self, query: str, city: Optional[str] = None) -> List[Dict[str, Any]]:
        city_to_use = city or self.city_display
        norm_city = city_to_use.strip().lower()
        slug = CITY_SLUG_MAP.get(norm_city, self.city_slug)
        api_url = f"{self.api_url}?city={slug}&cunt=500"

        session = self._get_session()
        try:
            resp = session.get(api_url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                return []
            data = resp.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        tokens = query.lower().split()
        matches: List[Dict[str, Any]] = []

        for raw in data:
            if not isinstance(raw, dict):
                continue
            title = (raw.get("name") or "").strip()
            if not all(tok in title.lower() for tok in tokens):
                continue

            raw_id = raw.get("id")
            if not raw_id:
                continue

            price = price_value(raw.get("final_price"))
            old_price = price_value(raw.get("start_price"))
            img_path = raw.get("image") or ""
            if isinstance(img_path, str) and img_path.strip():
                img_path = img_path.strip()
                image_url = img_path if img_path.startswith("http") else f"https://magnum.kz:1337{img_path}"
            else:
                image_url = ""

            item = {
                "shop": self.SHOP_NAME,
                "id": f"magnum_{raw_id}",
                "title": title,
                "category": "Каталог акций",
                "url": f"{self.base_url}/products/{raw_id}",
                "image_url": image_url,
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": city_to_use,
            }
            validated = validate_product_item(item, default_shop=self.SHOP_NAME)
            if validated:
                matches.append(validated)

        return matches

    def close(self) -> None:
        """Освобождает ресурсы сессии скрапера."""
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
