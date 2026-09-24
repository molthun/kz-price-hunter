"""Скрапер гипермаркета товаров для строительства и ремонта «Лемана ПРО» (lemanapro.kz, ex-Leroy Merlin).

Платформа: SSR HTML + Next.js INITIAL_STATE.
Защита: ServicePipe Anti-Bot (WAF).
Селекторы: window.INITIAL_STATE["plp"] (JSON) с fallback на div[data-qa-product] (DOM).
При вызове с облачных/datacenter IP ServicePipe может отдавать JS-challenge (HTTP 200) —
в этом случае скрапер автоматически проходит проверку через Playwright, получает cookies
(spid/spsc) и продолжает быстрый сбор по HTTP.
Пагинация: ?page=N.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from scrapers import http as requests
from scrapers.base import (
    PagedScraper, price_value, validate_product_item,
    UnconfirmedEnd, ScanResult, pagination_last_page
)


class LemanaProScraper(PagedScraper):
    SHOP_NAME = "Лемана ПРО"
    SHOP_EMOJI = "🟢"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self):
        self.base_url = "https://lemanapro.kz"
        self.session: Optional[requests.Session] = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _get_session(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session(impersonate="chrome124")
        return self.session

    def _is_challenge(self, html_text: str) -> bool:
        """Проверяет, является ли ответ заглушкой/JS-челленджем ServicePipe или SmartCaptcha."""
        if not html_text:
            return True
        low = html_text.lower()
        return ("servicepipe.tech" in low or "js-challenge-loader" in low
                or "smartcaptcha" in low or "exhkqyad" in low)

    def _solve_challenge(self, url: str) -> Optional[str]:
        """Проходит ServicePipe challenge через Playwright Chromium headless.

        Возвращает HTML-контент страницы после прохождения челленджа и попутно
        сохраняет выданные cookies (spid, spsc) в текущую HTTP-сессию.
        """
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
                    page = context.new_page()
                    page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    # Даем JavaScript ServicePipe время пройти челлендж и получить cookies
                    page.wait_for_timeout(2500)
                    content = page.content()
                    cookies = context.cookies()
                    if cookies:
                        session = self._get_session()
                        for c in cookies:
                            session.cookies.set(c["name"], c["value"], domain=c.get("domain", "lemanapro.kz"))
                    return content
                finally:
                    browser.close()
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка обхода защиты через Playwright: {e}")
            return None

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = category_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}page={page_num}"

        session = self._get_session()
        try:
            r = session.get(url, headers=self.headers, timeout=20)
            if r.status_code == 404:
                if page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней страницы")
                raise RuntimeError(f"HTTP 404 on {url}")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} on {url}")

            html_text = r.text

            # Если вернулась заглушка ServicePipe (тихий возврат 200 без товаров)
            if self._is_challenge(html_text):
                print(f"[{self.SHOP_NAME}] Обнаружен ServicePipe challenge на {url}, запускаю Playwright...")
                pw_content = self._solve_challenge(url)
                if pw_content and not self._is_challenge(pw_content):
                    html_text = pw_content
                else:
                    # Повторная попытка HTTP с полученными cookies
                    r2 = session.get(url, headers=self.headers, timeout=20)
                    if r2.status_code == 200 and not self._is_challenge(r2.text):
                        html_text = r2.text

            return self._parse_html(html_text, category_name, category_url, page_num)

        except Exception as e:
            if isinstance(e, (RuntimeError, UnconfirmedEnd)):
                raise
            raise RuntimeError(f"Ошибка парсинга {url}: {e}") from e

    def _parse_html(self, html_text: str, category_name: str, category_url: str, page_num: int) -> ScanResult:
        products: List[Dict[str, Any]] = []
        seen_ids = set()
        total_count = None

        # 1. Сначала извлекаем структурированный JSON из window.INITIAL_STATE (надежно и независимо от CSS-верстки)
        m = re.search(r"window\.INITIAL_STATE\[\"plp\"\]\s*=\s*(\{.+?\});\s*window\.INITIAL_STATE", html_text, re.DOTALL)
        if not m:
            m = re.search(r"window\.INITIAL_STATE\[\"plp\"\]\s*=\s*(\{.+?\});", html_text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                plp_prods = data.get("plp", {}).get("plp", {}).get("products", {})
                total_count = plp_prods.get("productsCount")
                raw_items = plp_prods.get("productsData", [])
                for p in raw_items:
                    title = (p.get("displayedName") or "").strip()
                    rel_url = (p.get("productLink") or "").strip()
                    if not title or not rel_url:
                        continue
                    product_url = urljoin(self.base_url, rel_url)

                    sku_m = re.search(r"(\d{7,10})/?$", rel_url)
                    sku = sku_m.group(1) if sku_m else rel_url.strip("/").split("/")[-1]
                    if not sku or sku in seen_ids:
                        continue

                    price_info = p.get("price") or {}
                    price = price_value(price_info.get("main_price"))
                    if not price or price <= 0:
                        continue

                    old_price = price_value(price_info.get("previous_price"))
                    if not old_price or old_price <= price:
                        old_price = None

                    media = p.get("mediaMainPhoto") or {}
                    img_url = media.get("tablet") or media.get("mobile") or ""

                    item = {
                        "id": str(sku),
                        "shop": self.SHOP_NAME,
                        "title": title,
                        "price": price,
                        "old_price_on_site": old_price,
                        "url": product_url,
                        "image_url": img_url,
                        "category": category_name,
                        "city": "Казахстан",
                        "sku": str(sku),
                    }
                    if validate_product_item(item):
                        seen_ids.add(sku)
                        products.append(item)
            except Exception:
                pass

        # 2. Fallback: парсинг карточек из DOM (div[data-qa-product]), если JSON не найден
        if not products:
            soup = BeautifulSoup(html_text, "html.parser")
            cards = soup.select("div[data-qa-product]")
            for c in cards:
                link_el = c.select_one('a[data-qa="product-name"], a[href*="/product/"]')
                if not link_el:
                    continue
                rel_url = link_el.get("href", "").strip()
                if not rel_url:
                    continue
                product_url = urljoin(self.base_url, rel_url)

                art_el = c.select_one('[data-qa="product-article"]')
                sku = re.sub(r"[^\d]", "", art_el.get_text()) if art_el else ""
                if not sku:
                    sku_m = re.search(r"(\d{7,10})/?$", rel_url)
                    sku = sku_m.group(1) if sku_m else rel_url.strip("/").split("/")[-1]

                if not sku or sku in seen_ids:
                    continue

                title_el = c.select_one(".product-card-name-link, [data-qa=\"product-name\"] span")
                title = title_el.get_text(strip=True) if title_el else link_el.get_text(strip=True)
                if not title:
                    img_el = c.select_one("img")
                    if img_el and img_el.get("alt"):
                        title = img_el["alt"].strip()
                if not title:
                    continue

                price = 0
                p_el = c.select_one('[data-testid="price-block-price"]')
                if p_el and p_el.get("value"):
                    price = price_value(p_el["value"])
                if not price and p_el:
                    price = price_value(p_el.get_text())
                if not price:
                    for pel in c.select('[data-testid*="price"], [class*="price"]'):
                        pv = price_value(pel.get_text())
                        if pv > 0:
                            price = pv
                            break
                if not price or price <= 0:
                    continue

                old_price = None
                old_el = c.select_one('[data-testid="price-block-oldprice"]')
                if old_el:
                    old_price = price_value(old_el.get("value") if old_el.get("value") else old_el.get_text())
                if old_price and old_price <= price:
                    old_price = None

                img_url = ""
                img_el = c.select_one("img")
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src and not src.startswith("data:"):
                        img_url = urljoin(self.base_url, src)

                item = {
                    "id": str(sku),
                    "shop": self.SHOP_NAME,
                    "title": title,
                    "price": price,
                    "old_price_on_site": old_price,
                    "url": product_url,
                    "image_url": img_url,
                    "category": category_name,
                    "city": "Казахстан",
                    "sku": str(sku),
                }
                if validate_product_item(item):
                    seen_ids.add(sku)
                    products.append(item)

        if not products:
            raise UnconfirmedEnd("карточки не найдены")

        # Определение конца пагинации
        last_page = None
        if total_count is not None and total_count > 0:
            last_page = max(1, math.ceil(total_count / 30))
        else:
            last_page = pagination_last_page(html_text, category_url, "page")

        return ScanResult(products, complete=last_page is not None and page_num >= last_page)

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass
            self.session = None
