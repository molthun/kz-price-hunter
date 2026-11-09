"""Сканеры витрин продавцов на маркетплейсах (Kaspi, ForteMarket, Halyk Market).

Автоматически собирают все товары и цены с публичной витрины конкретного продавца
для привязки предложений к каталогу сервиса.
"""
from __future__ import annotations
import asyncio
import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from domain.image_quality import upgrade_image_url
from content_retention import source_free
from scrapers import http as requests
from scrapers.base import price_value


@dataclass
class ShowcaseOffer:
    marketplace: str
    merchant_id: str
    external_sku: str
    title: str
    price: float
    url: str
    old_price: Optional[float] = None
    image_url: Optional[str] = None
    city: str = "Алматы"
    in_stock: bool = True
    rating: Optional[float] = None
    reviews_count: Optional[int] = None
    category: Optional[str] = None
    raw_data: Dict[str, Any] = field(default_factory=dict)


def extract_marketplace_merchant_info(url_or_code: str) -> Tuple[Optional[str], Optional[str]]:
    """Извлекает маркетплейс и идентификатор мерчанта из ссылки или кода."""
    if not url_or_code:
        return None, None
    s = str(url_or_code).strip()

    # Kaspi
    m_kaspi = re.search(r'kaspi\.kz/shop/(?:info/merchant|m)/([^/?#]+)', s, re.IGNORECASE)
    if m_kaspi:
        return "kaspi", m_kaspi.group(1).strip()
    if s.lower().startswith("kaspi:"):
        return "kaspi", s.split(":", 1)[-1].strip()
    if s.isdigit() and 3 <= len(s) <= 12:
        return "kaspi", s

    # Halyk Market
    m_halyk = re.search(r'halykmarket\.kz/merchant/([^/?#]+)', s, re.IGNORECASE)
    if m_halyk:
        return "halyk", m_halyk.group(1).strip()
    if s.lower().startswith("halyk:"):
        return "halyk", s.split(":", 1)[-1].strip()

    # Forte Market
    m_forte = re.search(r'(?:market\.forte\.kz|forte\.kz)/merchant/([^/?#]+)', s, re.IGNORECASE)
    if m_forte:
        return "forte", m_forte.group(1).strip()
    if s.lower().startswith(("forte:", "fortemarket:")):
        return "forte", s.split(":", 1)[-1].strip()

    # Wildberries
    m_wb = re.search(r'wildberries\.(?:kz|ru)/seller/([^/?#]+)', s, re.IGNORECASE)
    if m_wb:
        return "wildberries", m_wb.group(1).strip()
    if s.lower().startswith(("wildberries:", "wb:")):
        return "wildberries", s.split(":", 1)[-1].strip()

    # Ozon
    m_ozon = re.search(r'ozon\.(?:kz|ru)/seller/([^/?#]+)', s, re.IGNORECASE)
    if m_ozon:
        return "ozon", m_ozon.group(1).strip()
    if s.lower().startswith("ozon:"):
        return "ozon", s.split(":", 1)[-1].strip()

    return None, None


class KaspiShowcaseScanner:
    """Сканер витрины продавца на Kaspi Магазине."""
    API_URL = "https://kaspi.kz/yml/product-view/pl/results"

    def __init__(self, city_code: str = "750000000", city_name: str = "Алматы"):
        self.city_code = str(city_code)
        self.city_name = city_name
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "X-KS-City": self.city_code,
            "Referer": "https://kaspi.kz/",
        }

    def fetch_merchant_page(self, merchant_id: str, page: int = 1) -> Tuple[List[ShowcaseOffer], bool]:
        """Получает одну страницу каталога витрины мерчанта."""
        params = {
            "page": max(0, page - 1),
            "q": f":merchant:{merchant_id}",
            "text": "",
            "sort": "relevance",
            "qs": "",
            "ui": "d",
            "i": "-1",
            "c": self.city_code,
        }
        r = requests.get(self.API_URL, params=params, headers=self.headers, impersonate="chrome124", timeout=20)
        if r.status_code != 200:
            return [], True

        payload = r.json()
        data = payload.get("data") if isinstance(payload, dict) else payload
        cards = []
        if isinstance(data, list):
            cards = [c for c in data if isinstance(c, dict) and c.get("id")]
        elif isinstance(data, dict):
            cards = [c for c in (data.get("cards") or []) if isinstance(c, dict) and c.get("id")]

        offers = []
        for c in cards:
            title = (c.get("title") or "").strip()
            sale_price = price_value(c.get("unitSalePrice") or c.get("unitPrice"))
            if not title or sale_price <= 0:
                continue

            base_price = price_value(c.get("unitPrice"))
            old_p = base_price if base_price > sale_price else None

            # Изображение
            img_url = None
            imgs = c.get("previewImages") or []
            if imgs and isinstance(imgs[0], dict):
                img_url = imgs[0].get("large") or imgs[0].get("medium") or imgs[0].get("small")
            img_url = upgrade_image_url(img_url)

            # Ссылка
            link = c.get("shopLink") or ""
            if link and not link.startswith("http"):
                link = f"https://kaspi.kz{link}" if link.startswith("/") else f"https://kaspi.kz/{link}"
            if not link:
                link = f"https://kaspi.kz/shop/search/?text={urllib.parse.quote(title)}"

            # Категория
            cat_list = c.get("categoryRu") or c.get("category") or []
            cat_name = cat_list[-1] if cat_list else None

            offers.append(ShowcaseOffer(
                marketplace="kaspi",
                merchant_id=merchant_id,
                external_sku=str(c.get("id")),
                title=title,
                price=float(sale_price),
                old_price=float(old_p) if old_p else None,
                url=link,
                image_url=img_url,
                city=self.city_name,
                in_stock=bool(c.get("stock", 1) != 0),
                rating=float(c.get("rating", 0)) if c.get("rating") else None,
                reviews_count=int(c.get("reviewsQuantity", 0)) if c.get("reviewsQuantity") else None,
                category=cat_name,
                raw_data=c,
            ))

        is_last = (len(cards) < 12)
        return offers, is_last

    async def scan_merchant_showcase(self, merchant_id: str, max_pages: int = 50, delay_seconds: float = 0.5) -> List[ShowcaseOffer]:
        """Асинхронно обходит всю витрину мерчанта."""
        all_offers: List[ShowcaseOffer] = []
        seen_skus = set()

        for page in range(1, max_pages + 1):
            batch, is_last = await asyncio.to_thread(self.fetch_merchant_page, merchant_id, page)
            fresh = [o for o in batch if o.external_sku not in seen_skus]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen_skus.update(o.external_sku for o in fresh)

            if is_last or len(batch) < 12:
                break

            await asyncio.sleep(delay_seconds)

        return all_offers


class HalykShowcaseScanner:
    """Сканер витрины продавца на Halyk Market."""
    API_URL = "https://halykmarket.kz/search-api/products"

    def __init__(self, city_code: str = "-2", city_name: str = "Алматы"):
        self.city_code = city_code
        self.city_name = city_name
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://halykmarket.kz/",
        }

    def fetch_merchant_page(self, merchant_id: str, page: int = 1) -> Tuple[List[ShowcaseOffer], bool]:
        params = {
            "limit": 24,
            "page": page,
            "locations": self.city_code,
            "extended": "true",
            "merchants": merchant_id,
        }
        r = requests.get(self.API_URL, params=params, headers=self.headers, impersonate="chrome120", timeout=20)
        if r.status_code != 200:
            return [], True

        data = r.json()
        products = data.get("products") or []
        offers = []

        for p in products:
            if not isinstance(p, dict):
                continue
            name = (p.get("name") or "").strip()
            price_val = price_value(p.get("price"))
            if not name or price_val <= 0:
                continue

            sku = str(p.get("id") or p.get("sku") or "")
            old_p = price_value(p.get("old_price"))
            url = f"https://halykmarket.kz/product/{p.get('url_slug') or sku}"

            offers.append(ShowcaseOffer(
                marketplace="halyk",
                merchant_id=merchant_id,
                external_sku=sku,
                title=name,
                price=float(price_val),
                old_price=float(old_p) if old_p > price_val else None,
                url=url,
                image_url=upgrade_image_url(p.get("picture")),
                city=self.city_name,
                in_stock=True,
                raw_data=source_free(p),
            ))

        total = data.get("products_total") or 0
        is_last = (page * 24 >= total) or not products
        return offers, is_last

    async def scan_merchant_showcase(self, merchant_id: str, max_pages: int = 50, delay_seconds: float = 0.5) -> List[ShowcaseOffer]:
        all_offers = []
        seen = set()
        for page in range(1, max_pages + 1):
            batch, is_last = await asyncio.to_thread(self.fetch_merchant_page, merchant_id, page)
            fresh = [o for o in batch if o.external_sku not in seen]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen.update(o.external_sku for o in fresh)
            if is_last:
                break
            await asyncio.sleep(delay_seconds)
        return all_offers


class ForteShowcaseScanner:
    """Сканер витрины продавца на Forte Market."""
    API_SEARCH_URL = "https://apigw.forte.kz/fm/v1/algolia/search/text"

    def __init__(self, city_name: str = "Алматы"):
        self.city_name = city_name
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": "https://market.forte.kz/",
            "Origin": "https://market.forte.kz",
        }

    def fetch_merchant_page(self, merchant_id: str, page: int = 1) -> Tuple[List[ShowcaseOffer], bool]:
        payload = {
            "query": "",
            "page": max(0, page - 1),
            "hitsPerPage": 24,
            "facetFilters": [f"MerchantID:{merchant_id}"]
        }
        r = requests.post(self.API_SEARCH_URL, json=payload, headers=self.headers, timeout=20)
        if r.status_code not in (200, 201):
            return [], True

        data = r.json()
        hits = data.get("hits") or []
        offers = []

        for hit in hits:
            obj_id = hit.get("objectID") or hit.get("ID")
            name = (hit.get("Name") or "").strip()
            price = price_value(hit.get("Price"))
            if not obj_id or not name or price <= 0:
                continue

            offers.append(ShowcaseOffer(
                marketplace="fortemarket",
                merchant_id=merchant_id,
                external_sku=str(obj_id),
                title=name,
                price=float(price),
                url=hit.get("URL") or f"https://market.forte.kz/items/{obj_id}",
                image_url=upgrade_image_url(hit.get("Picture")),
                city=self.city_name,
                in_stock=True,
                raw_data=hit,
            ))

        nb_pages = data.get("nbPages", 1)
        is_last = (page >= nb_pages) or not hits
        return offers, is_last

    async def scan_merchant_showcase(self, merchant_id: str, max_pages: int = 50, delay_seconds: float = 0.5) -> List[ShowcaseOffer]:
        all_offers = []
        seen = set()
        for page in range(1, max_pages + 1):
            batch, is_last = await asyncio.to_thread(self.fetch_merchant_page, merchant_id, page)
            fresh = [o for o in batch if o.external_sku not in seen]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen.update(o.external_sku for o in fresh)
            if is_last:
                break
            await asyncio.sleep(delay_seconds)
        return all_offers


def wb_image_url(wb_id: int) -> str:
    """Вычисляет канонический URL обложки товара на CDN Wildberries."""
    try:
        wb_num = int(wb_id)
        if wb_num <= 0:
            return ""
    except (ValueError, TypeError):
        return ""
    vol = wb_num // 100000
    part = wb_num // 1000
    if vol <= 143: bucket = 1
    elif vol <= 287: bucket = 2
    elif vol <= 431: bucket = 3
    elif vol <= 719: bucket = 4
    elif vol <= 1007: bucket = 5
    elif vol <= 1061: bucket = 6
    elif vol <= 1115: bucket = 7
    elif vol <= 1169: bucket = 8
    elif vol <= 1313: bucket = 9
    elif vol <= 1601: bucket = 10
    elif vol <= 1655: bucket = 11
    elif vol <= 1919: bucket = 12
    elif vol <= 2045: bucket = 13
    elif vol <= 2189: bucket = 14
    elif vol <= 2405: bucket = 15
    elif vol <= 2621: bucket = 16
    elif vol <= 2837: bucket = 17
    else: bucket = 18
    return f"https://basket-{bucket:02d}.wbbasket.ru/vol{vol}/part{part}/{wb_num}/images/big/1.webp"


class WildberriesShowcaseScanner:
    """Сканер витрины продавца на Wildberries (wildberries.kz)."""
    API_URL = "https://catalog.wb.ru/sellers/v4/catalog"

    def __init__(self, city_name: str = "Алматы"):
        self.city_name = city_name
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": "https://www.wildberries.kz/",
            "Origin": "https://www.wildberries.kz",
        }

    def fetch_merchant_page(self, merchant_id: str, page: int = 1) -> Tuple[List[ShowcaseOffer], bool]:
        params = {
            "curr": "kzt",
            "dest": "-1257786",  # Казахстан / Алматы склад
            "supplier": merchant_id,
            "page": page,
            "sort": "popular",
        }
        r = requests.get(self.API_URL, params=params, headers=self.headers, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Wildberries API error: HTTP {r.status_code}")

        try:
            payload = r.json()
        except Exception as e:
            raise RuntimeError(f"Failed to decode Wildberries JSON: {e}")

        data = payload.get("data") if isinstance(payload, dict) else payload
        products = data.get("products") or [] if isinstance(data, dict) else []
        offers = []

        from content_retention import source_free

        for p in products:
            if not isinstance(p, dict):
                continue
            wb_id = p.get("id")
            name = (p.get("name") or "").strip()
            brand = (p.get("brand") or "").strip()
            title = f"{brand} {name}".strip() if brand and not name.lower().startswith(brand.lower()) else name
            if not wb_id or not title:
                continue

            # Цены в API WB отдаются в тиынах/копейках
            sale_price = 0
            base_price = 0
            sizes = p.get("sizes") or []
            if sizes and isinstance(sizes[0], dict) and isinstance(sizes[0].get("price"), dict):
                pr_obj = sizes[0]["price"]
                sale_price = price_value(pr_obj.get("total", 0) / 100)
                base_price = price_value(pr_obj.get("basic", 0) / 100)
            if sale_price <= 0:
                sale_price = price_value(p.get("salePriceU", 0) / 100)
                base_price = price_value(p.get("priceU", 0) / 100)

            if sale_price <= 0:
                continue

            old_p = float(base_price) if base_price > sale_price else None
            offers.append(ShowcaseOffer(
                marketplace="wildberries",
                merchant_id=str(merchant_id),
                external_sku=str(wb_id),
                title=title,
                price=float(sale_price),
                old_price=old_p,
                url=f"https://www.wildberries.kz/catalog/{wb_id}/detail.aspx",
                image_url=wb_image_url(wb_id),
                city=self.city_name,
                in_stock=True,
                rating=float(p.get("reviewRating", 0)) if p.get("reviewRating") else None,
                reviews_count=int(p.get("feedbacks", 0)) if p.get("feedbacks") else None,
                raw_data=source_free(p),
            ))

        is_last = (len(products) < 20) or not products
        return offers, is_last

    async def scan_merchant_showcase(self, merchant_id: str, max_pages: int = 50, delay_seconds: float = 0.5) -> List[ShowcaseOffer]:
        all_offers = []
        seen = set()
        for page in range(1, max_pages + 1):
            batch, is_last = await asyncio.to_thread(self.fetch_merchant_page, merchant_id, page)
            fresh = [o for o in batch if o.external_sku not in seen]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen.update(o.external_sku for o in fresh)
            if is_last:
                break
            await asyncio.sleep(delay_seconds)
        return all_offers


class OzonShowcaseScanner:
    """Сканер витрины продавца на Ozon (ozon.kz)."""
    API_URL = "https://api.ozon.kz/composer-api.bx/page/json/v2"

    def __init__(self, city_name: str = "Алматы"):
        self.city_name = city_name
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://ozon.kz/",
        }

    def fetch_merchant_page(self, merchant_id: str, page: int = 1) -> Tuple[List[ShowcaseOffer], bool]:
        url_param = f"/seller/{merchant_id}/?page={page}"
        r = requests.get(self.API_URL, params={"url": url_param}, headers=self.headers, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Ozon API error: HTTP {r.status_code}")

        try:
            payload = r.json()
        except Exception as e:
            raise RuntimeError(f"Failed to decode Ozon JSON: {e}")

        from content_retention import source_free

        widget_states = payload.get("widgetStates") or {}
        items = []
        for k, v in widget_states.items():
            if "searchResults" in k or "megaPaginator" in k or "catalog" in k:
                try:
                    state_obj = json.loads(v) if isinstance(v, str) else v
                    sub_items = state_obj.get("items") or []
                    if sub_items:
                        items.extend(sub_items)
                except Exception:
                    continue

        offers = []
        for it in items:
            if not isinstance(it, dict):
                continue
            sku = str(it.get("id") or it.get("sku") or "")
            title = (it.get("cellTrackingInfo", {}).get("title") or it.get("title") or "").strip()
            price_raw = it.get("cellTrackingInfo", {}).get("price") or it.get("price")
            price = price_value(price_raw)
            if not sku or not title or price <= 0:
                continue

            link = it.get("action", {}).get("link") or f"/product/{sku}"
            if not link.startswith("http"):
                link = f"https://ozon.kz{link}"

            img = it.get("tileImage", {}).get("image", {}).get("link") or it.get("image")
            offers.append(ShowcaseOffer(
                marketplace="ozon",
                merchant_id=str(merchant_id),
                external_sku=sku,
                title=title,
                price=float(price),
                url=link,
                image_url=upgrade_image_url(img),
                city=self.city_name,
                in_stock=True,
                raw_data=source_free(it),
            ))

        is_last = (len(items) < 12) or not items
        return offers, is_last

    async def scan_merchant_showcase(self, merchant_id: str, max_pages: int = 50, delay_seconds: float = 0.5) -> List[ShowcaseOffer]:
        all_offers = []
        seen = set()
        for page in range(1, max_pages + 1):
            batch, is_last = await asyncio.to_thread(self.fetch_merchant_page, merchant_id, page)
            fresh = [o for o in batch if o.external_sku not in seen]
            if not fresh:
                break
            all_offers.extend(fresh)
            seen.update(o.external_sku for o in fresh)
            if is_last:
                break
            await asyncio.sleep(delay_seconds)
        return all_offers
