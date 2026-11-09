"""Ozon Казахстан (ozon.kz) scraper.

Поддерживает парсинг каталога и поисковой выдачи с ценами в тенге (KZT).
Офферы связываются с продавцами маркетплейса (seller, seller_id)
для интеграции в кросс-канальную матрицу цен.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from domain.image_quality import upgrade_image_url
from offer_identity import NATIONWIDE
from scrapers import http as requests
from scrapers.base import (
    PagedScraper, ScanResult, price_value, validate_product_item,
)
from scrapers.showcase_scanners import (
    extract_ozon_widget_items,
    extract_ozon_item_fields,
)


class OzonScraper(PagedScraper):
    SHOP_NAME = "Ozon KZ"
    SHOP_EMOJI = "🔵"
    PAGE_PARAM = "page"
    PAGE_DELAY_SECONDS = 0.5

    API_URL = "https://ozon.kz/api/composer-api.bx/page/json/v2"

    def __init__(self):
        self.base_url = "https://ozon.kz"
        self.cookies: Dict[str, str] = {}
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
            "Referer": "https://ozon.kz/",
            "Origin": "https://ozon.kz",
        }

    def _solve_challenge(self) -> bool:
        """Решает проверку Ozon через headless Chromium и извлекает cookies."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                try:
                    context = browser.new_context(
                        user_agent=self.headers["User-Agent"],
                        locale="ru-RU",
                        viewport={"width": 1920, "height": 1080},
                    )
                    context.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined})')
                    page = context.new_page()
                    page.goto("https://ozon.kz", timeout=25000)
                    page.wait_for_timeout(2500)
                    cookies = context.cookies()
                    if cookies:
                        for c in cookies:
                            self.cookies[c["name"]] = c["value"]
                        return True
                finally:
                    browser.close()
        except Exception:
            pass
        return False

    @staticmethod
    def extract_search_path(category_url: str, page: int) -> str:
        """Формирует относительный путь к поиску или категории Ozon с пагинацией."""
        parsed = urllib.parse.urlparse(category_url)
        path = parsed.path or "/search/"
        qs = urllib.parse.parse_qs(parsed.query)
        qs["page"] = [str(page)]
        new_query = urllib.parse.urlencode(qs, doseq=True)
        return f"{path}?{new_query}"

    def _fetch_page(self, category_name: str, category_url: str, page: int) -> List[Dict[str, Any]]:
        search_path = self.extract_search_path(category_url, page)
        resp = requests.get(self.API_URL, params={"url": search_path}, headers=self.headers, cookies=self.cookies or None, timeout=25)
        if (resp.status_code == 403 or (isinstance(getattr(resp, "text", None), str) and "challenge" in resp.text)) and not self.cookies:
            if self._solve_challenge():
                resp = requests.get(self.API_URL, params={"url": search_path}, headers=self.headers, cookies=self.cookies or None, timeout=25)
        if resp.status_code != 200:
            raise RuntimeError(f"Ozon API returned status {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as e:
            raise ValueError(f"Failed to decode Ozon JSON: {e}")

        return self.parse_payload(payload, category_name, category_url, page)

    @classmethod
    def parse_payload(cls, payload: Dict[str, Any], category_name: str, category_url: str, page: int) -> ScanResult:
        if not isinstance(payload, dict) or "widgetStates" not in payload or not isinstance(payload.get("widgetStates"), dict):
            from scrapers.base import UnconfirmedEnd
            raise UnconfirmedEnd("Неизвестная структура ответа Ozon: отсутствует widgetStates")

        widget_states = payload["widgetStates"]
        # Тот же разбор, что у витрины (R02): header-only, битый JSON и items не списком —
        # не подтверждённый конец, а неизвестная/повреждённая структура
        items_data, recognised, damaged = extract_ozon_widget_items(widget_states)
        if not recognised:
            from scrapers.base import UnconfirmedEnd
            raise UnconfirmedEnd(
                "Неизвестная структура ответа Ozon: нет товарного виджета со списком items"
                + (f" (повреждённых виджетов: {damaged})" if damaged else ""))

        items: List[Dict[str, Any]] = []
        unparsed = damaged  # карточки повреждённого виджета потеряны: обход не полный
        for it in items_data:
            if not isinstance(it, dict):
                unparsed += 1
                continue
            sku, title, price_raw, old_price_raw, img_url, seller_name, seller_id, link = extract_ozon_item_fields(it)
            price_val = price_value(price_raw)
            old_price_val = price_value(old_price_raw)
            if not sku or not title or price_val <= 0:
                unparsed += 1
                continue

            item = {
                "id": f"ozon_{sku}",
                "shop": cls.SHOP_NAME,
                "title": title,
                "category": category_name,
                "url": link,
                "image_url": upgrade_image_url(img_url),
                "price": price_val,
                "old_price_on_site": old_price_val if old_price_val > price_val else 0,
                "city": NATIONWIDE,
                "seller": seller_name or cls.SHOP_NAME,
                "seller_id": f"ozon:{seller_id}" if seller_id else None,
                "marketplace": "ozon",
                "external_sku": sku,
            }
            validated = validate_product_item(item, cls.SHOP_NAME)
            if validated:
                items.append(validated)
            else:
                unparsed += 1

        is_last = (len(items_data) < 12) or not items_data
        complete = is_last and (unparsed == 0)
        return ScanResult(items, complete=complete, unparsed=unparsed)
