"""Сканеры витрин продавцов на маркетплейсах (Kaspi, ForteMarket, Halyk Market).

Автоматически собирают все товары и цены с публичной витрины конкретного продавца
для привязки предложений к каталогу сервиса.
"""
from __future__ import annotations
import asyncio
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from domain.image_quality import upgrade_image_url
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
                raw_data=p,
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
