import asyncio
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
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--disable-blink-features=AutomationControlled"]
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

        if hasattr(context, "add_init_script"):
            res = context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            if asyncio.iscoroutine(res):
                await res

        await context.add_cookies([
            {"name": "city_path", "value": "astana", "domain": ".dns-shop.kz", "path": "/"}
        ])

        page = await context.new_page()

        # Словарь для перехваченных AJAX цен и данных DNS: pid -> {"price": ..., "old_price": ...}
        intercepted_data: Dict[str, Dict[str, Any]] = {}

        async def _handle_response(resp):
            try:
                url = resp.url
                if any(k in url for k in ("/ajax-state/", "/microdata/", "/product/buy", "product-buy")):
                    ct = resp.headers.get("content-type", "")
                    if resp.status == 200 and "application/json" in ct:
                        body = await resp.json()
                        if isinstance(body, dict):
                            data_block = body.get("data") if isinstance(body.get("data"), dict) else body
                            states = data_block.get("states", []) if isinstance(data_block, dict) else []
                            for st in states:
                                if isinstance(st, dict) and "id" in st:
                                    code = str(st["id"])
                                    price_info = st.get("price") or {}
                                    cur = price_info.get("current") if isinstance(price_info, dict) else price_info
                                    prev = price_info.get("previous") if isinstance(price_info, dict) else 0
                                    intercepted_data[code] = {"price": cur, "old_price": prev}
            except Exception:
                pass

        if hasattr(page, "on"):
            page.on("response", _handle_response)

        for page_num in range(1, (max_pages or 2) + 1):
            url = category_url
            if page_num > 1:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}p={page_num}"

            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                if resp and resp.status == 403 and "Один момент" in (await page.title()):
                    # Проверка Cloudflare на бота: не обходим, фиксируем понятную причину
                    raise RuntimeError("DNS: доступ закрыт проверкой Cloudflare")
                if not resp or resp.status != 200:
                    raise RuntimeError(f"DNS: HTTP {resp.status if resp else 'нет ответа'}")

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
                    if price <= 0 and pid in intercepted_data:
                        price = parse_price(str(intercepted_data[pid].get("price") or 0))
                    if price <= 0:
                        continue

                    old_price_el = await el.query_selector(".product-buy__prev, .product-buy__price-sub, [class*='__prev']")
                    old_price = parse_price(await old_price_el.inner_text()) if old_price_el else 0
                    if old_price <= 0 and pid in intercepted_data:
                        old_price = parse_price(str(intercepted_data[pid].get("old_price") or 0))

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
                detail = str(e) if str(e).startswith("DNS:") else type(e).__name__
                error = f"Страница {page_num}: {detail}"
                break

        await context.close()
        return ScanResult(products, error=error, limited=not error)

    def close(self) -> None:
        """Освобождает ресурсы адаптера."""
        pass
