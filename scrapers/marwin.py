"""Скрапер сети магазинов книг, игрушек, гейминга и подарков «Меломан / MARWIN» (marwin.kz).

Платформа (с 21.11.2026): Next.js App Router + публичный JSON REST API `/v1/catalog/*`.
- Разрешение slug категории -> category_id: `GET /v1/catalog/categories/slug/{slug}`
  (с фоллбэком на редирект для старых URL вида `/books/`).
- Товары категории: `GET /v1/catalog/products?category_id={id}&page={N}&limit=20&in_stock=true&locale=ru`.
- Конец каталога подтверждается по `pagination.pages` (`page >= pages` при `unparsed == 0`).
- Живой поиск: `GET /v1/catalog/search?q={query}&limit={limit}&locale=ru`.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    price_value,
    validate_product_item,
)


_LOCALE_PREFIXES = {"ru", "en", "kk", "kz"}


def extract_category_slug(category_url: str) -> str:
    """Извлекает slug категории из URL вида `https://www.marwin.kz/knigi-32627` или `/en/knigi-32627`."""
    path = urlsplit(str(category_url or "")).path.strip("/")
    if not path:
        return ""
    parts = [p for p in path.split("/") if p]
    if parts and parts[0].lower() in _LOCALE_PREFIXES:
        parts = parts[1:]
    return parts[-1] if parts else ""


class MarwinScraper(PagedScraper):
    SHOP_NAME = "Меломан"
    SHOP_EMOJI = "📚"
    PAGE_PARAM = "page"
    PAGE_LIMIT = 20
    PAGE_DELAY_SECONDS = 0.35

    def __init__(self):
        self.base_url = "https://www.marwin.kz"
        self.session: Optional[requests.Session] = None
        self._slug_to_id: Dict[str, int] = {}
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://www.marwin.kz/",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def close(self):
        self._slug_to_id.clear()
        super().close()

    def _get_with_retry(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: int = 20):
        session = self._get_session()
        resp = None
        for attempt in range(2):
            try:
                resp = session.get(url, params=params, headers=self.headers, timeout=timeout)
                if resp.status_code in (500, 502, 503, 504) and attempt == 0:
                    time.sleep(1.0)
                    continue
                break
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                raise
        if resp is None:
            raise RuntimeError(f"Не удалось получить ответ от {url}")
        return resp

    def _resolve_category_id(self, category_url: str) -> int:
        slug = extract_category_slug(category_url)
        if not slug:
            raise RuntimeError(f"Не удалось извлечь slug категории из {category_url}")
        if slug in self._slug_to_id:
            return self._slug_to_id[slug]

        api_url = f"{self.base_url}/v1/catalog/categories/slug/{slug}"
        resp = self._get_with_retry(api_url, params={"locale": "ru"}, timeout=15)

        if resp.status_code == 404:
            # Фоллбэк для старых URL (например /books/ -> редирект на /knigi-32627)
            redir = self._get_with_retry(category_url, timeout=15)
            final_slug = extract_category_slug(getattr(redir, "url", "") or "")
            if final_slug and final_slug != slug:
                if final_slug in self._slug_to_id:
                    self._slug_to_id[slug] = self._slug_to_id[final_slug]
                    return self._slug_to_id[slug]
                resp = self._get_with_retry(
                    f"{self.base_url}/v1/catalog/categories/slug/{final_slug}",
                    params={"locale": "ru"},
                    timeout=15,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    cat_id = int(data["id"])
                    self._slug_to_id[final_slug] = cat_id
                    self._slug_to_id[slug] = cat_id
                    return cat_id
            raise RuntimeError(f"HTTP 404 категория не найдена: {category_url}")

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} при получении категории {slug}")

        try:
            data = resp.json()
            cat_id = int(data["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Некорректный JSON категории {slug}: {exc}") from exc

        self._slug_to_id[slug] = cat_id
        return cat_id

    def _extract_item(self, raw: Any, category_name: str) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Разбирает объект товара из `/v1/catalog/*`.

        Возвращает `(item, is_unparsed)`:
        - товары с `in_stock is False` пропускаются без ошибки (`(None, False)`);
        - сломанные записи возвращают `(None, True)`, чтобы заблокировать ошибочное снятие.
        """
        if not isinstance(raw, dict):
            return None, True

        if raw.get("in_stock") is False:
            return None, False

        raw_id = raw.get("id")
        raw_sku = raw.get("sku")
        pid = str(raw_id if raw_id is not None and str(raw_id).strip() else (raw_sku or "")).strip()
        sku = str(raw_sku if raw_sku is not None and str(raw_sku).strip() else pid).strip()
        title = str(raw.get("name") or "").strip()
        slug = str(raw.get("slug") or "").strip()

        if not pid or not title or not slug:
            return None, True

        price_block = raw.get("price")
        if isinstance(price_block, dict):
            price = price_value(price_block.get("current"))
            old_price = price_value(price_block.get("old"))
        else:
            price = price_value(price_block)
            old_price = 0

        if price <= 0:
            return None, True

        if slug.startswith("http://") or slug.startswith("https://"):
            product_url = slug
        else:
            product_url = urljoin(f"{self.base_url}/product/", slug.lstrip("/"))

        image_url = str(raw.get("image") or "").strip()
        if not image_url:
            images = raw.get("images")
            if isinstance(images, list) and images:
                image_url = str(images[0] or "").strip()

        item = {
            "shop": self.SHOP_NAME,
            "id": f"marwin_{pid}",
            "sku": sku,
            "title": title,
            "category": category_name,
            "url": product_url,
            "image_url": image_url,
            "price": price,
            "old_price_on_site": old_price if old_price > price else 0,
            "city": "Алматы / Казахстан",
        }
        validated = validate_product_item(item, default_shop=self.SHOP_NAME)
        if not validated:
            return None, True
        return validated, False

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> ScanResult:
        cat_id = self._resolve_category_id(category_url)
        resp = self._get_with_retry(
            f"{self.base_url}/v1/catalog/products",
            params={
                "category_id": cat_id,
                "page": page_num,
                "limit": self.PAGE_LIMIT,
                "in_stock": "true",
                "locale": "ru",
            },
            timeout=20,
        )

        if resp.status_code == 404:
            if page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней страницы")
            raise RuntimeError(f"HTTP 404 on {category_url}")

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} on {category_url}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Некорректный JSON выдачи {category_url}: {exc}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError(f"Неожиданная структура ответа API {category_url}")

        raw_items = payload["items"]
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        try:
            total_pages = int(pagination.get("pages") or 0)
        except (ValueError, TypeError):
            total_pages = 0

        if not raw_items:
            if page_num == 1:
                raise RuntimeError(f"Карточки товаров не найдены на первой странице {category_url}")
            if total_pages > 0 and page_num > total_pages:
                return ScanResult([], complete=True)
            raise UnconfirmedEnd("карточки не найдены на следующей странице")

        products: List[Dict[str, Any]] = []
        unparsed = 0
        for raw in raw_items:
            parsed, is_bad = self._extract_item(raw, category_name)
            if is_bad:
                unparsed += 1
            elif parsed is not None:
                products.append(parsed)

        # PagedScraper._scrape_sync сам запрещает complete=True при unparsed > 0;
        # здесь complete отражает факт достижения последней страницы, чтобы не делать лишний запрос.
        is_complete = bool(total_pages > 0 and page_num >= total_pages)
        return ScanResult(products, complete=is_complete, unparsed=unparsed)

    def search_live(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Живой поиск товаров через публичный JSON API `/v1/catalog/search`."""
        q = (query or "").strip()
        if not q:
            return []
        try:
            resp = self._get_with_retry(
                f"{self.base_url}/v1/catalog/search",
                params={"q": q, "limit": max(1, int(limit or 20)), "locale": "ru"},
                timeout=12,
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            raw_items = payload.get("items") if isinstance(payload, dict) else []
            if not isinstance(raw_items, list):
                return []
            results: List[Dict[str, Any]] = []
            for raw in raw_items:
                parsed, _ = self._extract_item(raw, "Поиск")
                if parsed:
                    results.append(parsed)
                    if len(results) >= limit:
                        break
            return results
        except Exception:
            return []
