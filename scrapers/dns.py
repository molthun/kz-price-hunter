import asyncio
import re
from typing import List, Dict, Any
from playwright.async_api import async_playwright
from scrapers.base import ScanResult, parse_price

class DNSScraper:
    SHOP_NAME = "DNS Казахстан"
    SHOP_EMOJI = "🟧"

    def __init__(self):
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    async def scrape(self, category_name: str, category_url: str, max_pages: int = 1) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            try:
                return await self._scrape_with_browser(browser, category_name, category_url, max_pages, products)
            finally:
                # Браузер закрывается и при ошибке/отмене задачи, а не только в конце обхода (M09)
                await browser.close()

    async def _scrape_with_browser(self, browser, category_name, category_url, max_pages, products):
        error = None
        context = await browser.new_context(
            user_agent=self.user_agent,
            locale="ru-RU",
            viewport={"width": 1920, "height": 1080}
        )

        await context.add_cookies([
            {"name": "city_path", "value": "astana", "domain": ".dns-shop.kz", "path": "/"}
        ])

        page = await context.new_page()

        for page_num in range(1, (max_pages or 2) + 1):
            url = category_url
            if page_num > 1:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}p={page_num}"

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if not resp or resp.status != 200:
                    raise RuntimeError("DNS: HTTP error")

                try:
                    await page.wait_for_selector(".catalog-product", timeout=12000)
                except Exception:
                    raise RuntimeError("DNS: карточки не найдены")

                await asyncio.sleep(2)
                product_elements = await page.query_selector_all(".catalog-product")

                for el in product_elements:
                    pid = await el.get_attribute("data-code") or await el.get_attribute("data-product")
                    if not pid:
                        continue

                    name_el = await el.query_selector(".catalog-product__name")
                    if not name_el:
                        continue
                    title = (await name_el.inner_text()).strip()
                    rel_link = await name_el.get_attribute("href") or ""
                    full_link = f"https://www.dns-shop.kz{rel_link}" if rel_link.startswith("/") else rel_link

                    img_el = await el.query_selector("img")
                    image_url = ""
                    if img_el:
                        image_url = await img_el.get_attribute("data-src") or await img_el.get_attribute("src") or ""

                    price_active_el = await el.query_selector(".product-buy__price_active, .product-buy__price-current, [class*='price_active']")
                    if price_active_el:
                        price_text = await price_active_el.inner_text()
                    else:
                        price_el = await el.query_selector(".product-buy__price")
                        price_text = await price_el.inner_text() if price_el else ""

                    price = parse_price(price_text)
                    if price <= 0:
                        continue

                    old_price_el = await el.query_selector(".product-buy__prev, .product-buy__price-sub, [class*='__prev']")
                    old_price = parse_price(await old_price_el.inner_text()) if old_price_el else 0

                    products.append({
                        "shop": self.SHOP_NAME,
                        "id": f"dns_{pid}",
                        "title": title,
                        "category": category_name,
                        "url": full_link,
                        "image_url": image_url,
                        "price": price,
                        "old_price_on_site": old_price if old_price > price else 0,
                        "city": "Астана"
                    })

                await asyncio.sleep(1.5)
            except Exception as e:
                error = f"Страница {page_num}: {type(e).__name__}"
                break

        await context.close()
        return ScanResult(products, error=error, limited=not error)

    def close(self) -> None:
        """Освобождает ресурсы адаптера."""
        pass
