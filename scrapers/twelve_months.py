"""Скрапер гипермаркета стройматериалов и инструментов «12 Месяцев» (12.kz).

Платформа: Bitrix Aspro Premier (с обратной совместимостью для legacy AdvantShop фикстур).
Региональность (docs/REGIONAL_PRICING.md): `multi_city_price` — цены и наличие зависят от города
(например, один и тот же товар может стоить 27 300 ₸ в Алматы и 35 000 ₸ в Астане).
Переключение города: cookie `current_region` (`471567` = Алматы, `471565` = Астана и др.),
подтверждение фактического города в ответе через `.regions__name`.
Пагинация: `?PAGEN_1=N`.
"""
import asyncio
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

from html_soup import make_soup
from scrapers import http as requests
from scrapers.base import (
    PagedScraper,
    ScanResult,
    UnconfirmedEnd,
    pagination_last_page,
    price_value,
    validate_product_item,
)


class TwelveMonthsScraper(PagedScraper):
    SHOP_NAME = "12 Месяцев"
    SHOP_EMOJI = "🛠"
    PAGE_DELAY_SECONDS = 0.8
    RETRY_STATUS_CODES = (500, 502, 503, 504, 520, 522, 524)
    REQUEST_TIMEOUT_SECONDS = 30
    RETRY_DELAY_SECONDS = 2.0

    # ID регионов Bitrix Aspro Premier (подтверждено на живом 12.kz, 19.11.2026)
    REGION_IDS: Dict[str, str] = {
        "Астана": "471565",
        "Караганда": "471566",
        "Алматы": "471567",
        "Усть-Каменогорск": "471568",
        "Павлодар": "471569",
        "Костанай": "471570",
        "Семей": "471571",
    }
    DEFAULT_CITIES: Tuple[str, ...] = ("Алматы", "Астана")

    def __init__(self, cities: Optional[Iterable[str]] = None):
        self.base_url = "https://12.kz"
        self.session: Optional[requests.Session] = None
        self.cities: Tuple[str, ...] = tuple(cities) if cities is not None else self.DEFAULT_CITIES
        self._active_city: str = self.cities[0] if self.cities else "Алматы"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _page_url(self, category_url: str, page_num: int) -> str:
        if page_num <= 1:
            return category_url
        sep = "&" if "?" in category_url else "?"
        return f"{category_url}{sep}PAGEN_1={page_num}"

    @staticmethod
    def _extract_active_region(soup: Any) -> Optional[str]:
        el = soup.select_one(".regions__name")
        if not el:
            return None
        name = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
        return name or None

    def _parse_aspro_card(self, card: Any, category_name: str, city: str) -> Optional[Dict[str, Any]]:
        card_text = card.get_text(separator=" ", strip=True).lower()
        if "нет в наличии" in card_text:
            return None

        data_item: Dict[str, Any] = {}
        data_el = card.select_one("[data-item]")
        if data_el and data_el.get("data-item"):
            try:
                parsed = json.loads(data_el["data-item"])
                if isinstance(parsed, dict):
                    data_item = parsed
            except (ValueError, TypeError):
                pass

        pid = str(data_item.get("ID") or card.get("data-id") or "").strip()

        title = str(data_item.get("NAME") or "").strip()
        if not title:
            ru_el = card.select_one(".js-gt-name-ru")
            if ru_el:
                title = ru_el.get_text(" ", strip=True)
        link_el = card.select_one(".catalog-block__info-title a, a.dark_link, a[href*='/catalog/']")
        if not title and link_el:
            title = (link_el.get("title") or link_el.get_text(" ", strip=True)).strip()
        if not title:
            return None

        rel_url = str(data_item.get("DETAIL_PAGE_URL") or (link_el.get("href") if link_el else "") or "").strip()
        if not rel_url:
            return None
        product_url = urljoin(self.base_url, rel_url)

        if not pid:
            m = re.search(r"-(\d+)/?$", rel_url)
            pid = m.group(1) if m else rel_url.strip("/").split("/")[-1]
        if not pid:
            return None

        price = None
        old_price = None
        new_el = card.select_one(".price__new-val")
        if new_el:
            price = price_value(new_el.get_text())
        if not price:
            fallback_p = card.select_one(".price, [class*='price']")
            if fallback_p:
                price = price_value(fallback_p.get_text())
        if not price or price <= 0:
            return None

        old_el = card.select_one(".price__old-val")
        if old_el:
            old_price = price_value(old_el.get_text())
            if old_price and old_price <= price:
                old_price = None

        img_url = ""
        img_el = card.select_one("img.js-replace-img, img[data-siter-catalog-image], img")
        if img_el:
            raw_src = img_el.get("data-src") or img_el.get("data-siter-src") or img_el.get("src") or ""
            if raw_src and not raw_src.startswith("data:"):
                img_url = urljoin(self.base_url, raw_src)

        art_m = re.search(r"(?:Артикул|Арт\.?):?\s*([A-Za-z0-9_-]+)", card_text, re.IGNORECASE)
        sku = art_m.group(1) if art_m else ""
        if not sku:
            m_art = re.search(r"-(\d{5,})/?$", rel_url)
            sku = m_art.group(1) if m_art else ""

        item = {
            "id": pid,
            "shop": self.SHOP_NAME,
            "title": re.sub(r"\s+", " ", title).strip(),
            "price": price,
            "old_price_on_site": old_price,
            "url": product_url,
            "image_url": img_url,
            "category": category_name,
            "city": city,
            "sku": sku,
        }
        return item if validate_product_item(item) else None

    def _parse_legacy_advantshop_card(self, card: Any, category_name: str, city: str) -> Optional[Dict[str, Any]]:
        """Обратная совместимость со старой разметкой AdvantShop (используется в контрактных фикстурах)."""
        card_text = card.get_text(separator=" ", strip=True).lower()
        if "нет в наличии" in card_text:
            return None

        pid = card.get("data-product-id") or card.get("data-offer-id") or ""
        name_el = card.select_one(".products-view-name a, a.products-view-name-link") or card.select_one("a[href*='/products/']")
        if not name_el:
            return None
        title = name_el.get_text(strip=True)
        rel_url = name_el.get("href", "")
        if not title or not rel_url:
            return None
        product_url = urljoin(self.base_url, rel_url)

        if not pid:
            m = re.search(r"/products/([^/?#]+)", rel_url)
            pid = m.group(1) if m else rel_url.strip("/").split("/")[-1]

        price = None
        old_price = None
        price_new_el = card.select_one(".price-new .price-number")
        if price_new_el:
            price = price_value(price_new_el.get_text())
            old_price_el = card.select_one(".price-old .price-number")
            if old_price_el:
                old_price = price_value(old_price_el.get_text())
        else:
            price_el = card.select_one(".price-number, .products-view-price .price, .price")
            if price_el:
                price = price_value(price_el.get_text())
        if not price or price <= 0:
            return None
        if old_price and old_price <= price:
            old_price = None

        img_url = ""
        img_el = card.select_one("img.products-view-picture, figure.products-view-pictures img, img")
        if img_el:
            raw_src = img_el.get("src") or img_el.get("data-src") or img_el.get("data-ng-src") or ""
            if raw_src and not raw_src.startswith("data:"):
                img_url = urljoin(self.base_url, raw_src)

        art_m = re.search(r"Артикул:\s*([^\s,]+)", card_text, re.IGNORECASE)
        sku = art_m.group(1) if art_m else ""

        item = {
            "id": str(pid),
            "shop": self.SHOP_NAME,
            "title": title,
            "price": price,
            "old_price_on_site": old_price,
            "url": product_url,
            "image_url": img_url,
            "category": category_name,
            "city": city,
            "sku": sku,
        }
        return item if validate_product_item(item) else None

    def _fetch_page(self, category_name: str, category_url: str, page_num: int,
                    city: Optional[str] = None) -> ScanResult:
        target_city = city or self._active_city
        region_id = self.REGION_IDS.get(target_city)
        url = self._page_url(category_url, page_num)
        session = self._get_session()
        req_kwargs: Dict[str, Any] = {"headers": self.headers, "timeout": self.REQUEST_TIMEOUT_SECONDS}
        if region_id:
            req_kwargs["cookies"] = {"current_region": region_id}

        try:
            r = None
            for attempt in range(2):
                try:
                    r = session.get(url, **req_kwargs)
                    if r.status_code in self.RETRY_STATUS_CODES and attempt == 0:
                        time.sleep(self.RETRY_DELAY_SECONDS)
                        continue
                    break
                except requests.HostCooldown:
                    raise
                except Exception:
                    if attempt == 0:
                        time.sleep(self.RETRY_DELAY_SECONDS)
                        continue
                    raise
            if r is None:
                raise RuntimeError(f"Не удалось получить ответ от {url}")
            if r.status_code == 404:
                if page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней страницы")
                raise RuntimeError(f"HTTP 404 on {url}")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {url}")

            soup = make_soup(r.text)
            active_region = self._extract_active_region(soup)
            if active_region and target_city and active_region.lower() != target_city.lower():
                raise RuntimeError(
                    f"12 Месяцев отдал регион «{active_region}» вместо запрошенного «{target_city}»"
                )

            aspro_cards = soup.select(".catalog-block__item")
            legacy_cards = [] if aspro_cards else soup.select(".products-view-item, .js-products-view-item")
            if not aspro_cards and not legacy_cards:
                raise UnconfirmedEnd("карточки не найдены")

            products: List[Dict[str, Any]] = []
            seen_ids = set()
            if aspro_cards:
                for c in aspro_cards:
                    item = self._parse_aspro_card(c, category_name, target_city)
                    if item and item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        products.append(item)
                last_page = pagination_last_page(r.text, category_url, param="PAGEN_1")
                is_complete = (last_page is None) or (page_num >= last_page)
                return ScanResult(products, complete=is_complete)

            for c in legacy_cards:
                item = self._parse_legacy_advantshop_card(c, category_name, target_city)
                if item and item["id"] not in seen_ids:
                    seen_ids.add(item["id"])
                    products.append(item)
            return ScanResult(products, complete=False)

        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Ошибка парсинга {url}: {e}") from e

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> ScanResult:
        """Обходит категорию по всем городам из self.cities, возвращая объединённый ScanResult.

        Общий флаг complete=True выставляется только если ВСЕ города завершились полностью:
        при частичном сбое одного города reconcile_source не снимет с продажи предложения остальных городов.
        """
        cities = self.cities or ("Алматы",)
        all_items: List[Dict[str, Any]] = []
        all_complete = True
        errors: List[str] = []

        for city in cities:
            self._active_city = city
            # Сбрасываем сессию между городами, чтобы cookie PHPSESSID/current_region не смешивались
            self.close()
            res = super()._scrape_sync(category_name, category_url, max_pages=max_pages)
            all_items.extend(res)
            if not getattr(res, "complete", False):
                all_complete = False
            err = getattr(res, "error", None)
            if err:
                errors.append(f"{city}: {err}")

        return ScanResult(all_items, complete=all_complete and not errors, error="; ".join(errors) if errors else None)

    async def scrape(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> ScanResult:
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
