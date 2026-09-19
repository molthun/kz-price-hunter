"""Скрапер сети магазинов и производителя товаров для дома Zeta (zeta.kz).

Платформа: Next.js + прямой REST API бэкенда (back.zeta.kz).
Категории: товары для дома, бытовой пластик, емкости, обувницы, вешалки, садовый инвентарь.
Пагинация: limit=40, page=N через JSON API.
"""
from typing import List, Dict, Any
from urllib.parse import urljoin

from scrapers import http as requests
from scrapers.base import PagedScraper, price_value, validate_product_item, ScanResult


class ZetaScraper(PagedScraper):
    SHOP_NAME = "Zeta"
    SHOP_EMOJI = "🪑"
    PAGE_DELAY_SECONDS = 0.3
    PAGE_SIZE = 40

    def __init__(self):
        self.api_base = "https://back.zeta.kz"
        self.site_base = "https://zeta.kz"
        self.session = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        # Извлекаем ID категории (поддерживаем как сырой hex ID, так и полный URL)
        cat_id = category_url.strip("/").split("/")[-1]
        if "categories[]=" in category_url:
            import urllib.parse
            parsed = urllib.parse.urlparse(category_url)
            qs = urllib.parse.parse_qs(parsed.query)
            cat_id = qs.get("categories[]", [cat_id])[0]

        api_url = (
            f"{self.api_base}/good/list?"
            f"categories[]={cat_id}&limit={self.PAGE_SIZE}&page={page_num}"
            f"&isCount=true&language=RU"
        )

        session = self._get_session()
        try:
            r = session.get(api_url, headers=self.headers, timeout=15)
            if r.status_code == 404:
                return []
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {api_url}")

            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get("results"), list):
                raise RuntimeError("Неожиданная структура ответа Zeta")
            results = data["results"]
            # isCount=true: resultCount — общее число товаров категории, доказательство конца (R-M01)
            total = data.get("resultCount")
            total_known = isinstance(total, int) and not isinstance(total, bool) and total >= 0
            if not results:
                return ScanResult([], complete=total_known and (page_num - 1) * self.PAGE_SIZE >= total)

            seen_ids = set()

            for item_data in results:
                if not isinstance(item_data, dict):
                    continue

                # Доступность товара
                if not item_data.get("available", True):
                    continue

                pid = str(item_data.get("id") or "")
                if not pid or pid in seen_ids:
                    continue

                # Наименование (может быть локализованным объектом {'ru': ..., 'kz': ...})
                name_field = item_data.get("name")
                if isinstance(name_field, dict):
                    title = name_field.get("ru") or name_field.get("kz") or name_field.get("en") or ""
                elif isinstance(name_field, str):
                    title = name_field
                else:
                    title = ""

                title = str(title).strip()
                if not title:
                    continue

                # Цена
                price = price_value(item_data.get("price"))
                if not price or price <= 0:
                    continue

                old_price = price_value(item_data.get("oldPrice"))
                if old_price and old_price <= price:
                    old_price = None

                # Ссылка на товар
                slug = item_data.get("slug") or ""
                full_slug_list = item_data.get("fullSlug")
                if isinstance(full_slug_list, list) and full_slug_list:
                    rel_path = f"catalog/{full_slug_list[0]}"
                elif slug:
                    rel_path = f"catalog/{slug}"
                else:
                    rel_path = f"products/{pid}"

                product_url = urljoin(self.site_base, rel_path)

                # Изображение
                img_url = ""
                images = item_data.get("images")
                if isinstance(images, list) and images:
                    img_raw = images[0]
                    if isinstance(img_raw, str) and img_raw.strip():
                        img_url = urljoin(self.site_base, img_raw.strip())

                # Артикул
                sku = str(item_data.get("article") or "").strip()

                item = {
                    "id": pid,
                    "shop": self.SHOP_NAME,
                    "title": title,
                    "price": price,
                    "old_price_on_site": old_price,
                    "url": product_url,
                    "image_url": img_url,
                    "category": category_name,
                    "city": "Казахстан",
                    "sku": sku,
                }

                if validate_product_item(item):
                    seen_ids.add(pid)
                    products.append(item)

            return ScanResult(products, complete=total_known and page_num * self.PAGE_SIZE >= total)

        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Ошибка парсинга Zeta {api_url}: {e}") from e

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
