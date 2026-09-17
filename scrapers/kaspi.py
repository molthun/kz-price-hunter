import asyncio
import re
import urllib.parse
from typing import List, Dict, Any
from playwright.async_api import async_playwright

def parse_kaspi_price(price_str: str) -> int:
    if not price_str:
        return 0
    # Отсекаем часть с рассрочкой, если она присутствует
    main_part = re.split(r"рассроч|кредит", price_str, flags=re.IGNORECASE)[0]
    digits = re.sub(r"[^\d]", "", main_part)
    return int(digits) if digits else 0

class KaspiScraper:
    SHOP_NAME = "Kaspi Магазин"
    SHOP_EMOJI = "🔴"

    def __init__(self):
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.city_code = "710000000"  # Астана

    async def scrape(self, category_name: str, category_url: str, max_pages: int = 1) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent=self.user_agent,
                locale="ru-RU",
                viewport={"width": 1920, "height": 1080}
            )

            await context.add_cookies([
                {"name": "kaspi.storefront.cookie.city", "value": self.city_code, "domain": ".kaspi.kz", "path": "/"}
            ])

            page = await context.new_page()

            for page_num in range(1, max_pages + 1):
                url = category_url
                separator = "&" if "?" in url else "?"
                if f"c={self.city_code}" not in url:
                    url = f"{url}{separator}c={self.city_code}"
                if page_num > 1:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}page={page_num - 1}"

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    try:
                        await page.wait_for_selector(".item-card", timeout=10000)
                    except Exception:
                        pass

                    await asyncio.sleep(2.5)

                    raw_cards = await page.evaluate('''() => {
                        const cards = document.querySelectorAll('.item-card');
                        return Array.from(cards).map(c => {
                            const titleEl = c.querySelector('.item-card__name-link, .item-card__name, [class*="name"]');
                            const priceEl = c.querySelector('.item-card__prices-price, [class*="price"]');
                            const linkEl = c.querySelector('a.item-card__name-link, a[href*="/shop/p/"]');
                            const imgEl = c.querySelector('img.item-card__image, img');
                            return {
                                title: titleEl ? titleEl.innerText.trim() : '',
                                price_text: priceEl ? priceEl.innerText.trim() : '',
                                link: linkEl ? linkEl.href : '',
                                img: imgEl ? imgEl.src : ''
                            };
                        });
                    }''')

                    for c in raw_cards:
                        title = c.get("title", "")
                        link = c.get("link", "")
                        if not title or not link:
                            continue

                        price = parse_kaspi_price(c.get("price_text", ""))
                        if price <= 0:
                            continue

                        # Извлекаем ID из ссылки (например, ...-129172890/?...)
                        id_match = re.search(r"-(\d+)/", link)
                        pid = id_match.group(1) if id_match else link.split("/")[-2]

                        products.append({
                            "shop": self.SHOP_NAME,
                            "id": f"kaspi_{pid}",
                            "title": title,
                            "category": category_name,
                            "url": link,
                            "image_url": c.get("img", ""),
                            "price": price,
                            "old_price_on_site": 0,
                            "city": "Астана"
                        })

                except Exception as e:
                    print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                    break

            await browser.close()
        return products

    async def search(self, query: str, max_items: int = 15) -> List[Dict[str, Any]]:
        """Прямой поиск товаров в Kaspi по текстовому запросу."""
        encoded = urllib.parse.quote(query)
        search_url = f"https://kaspi.kz/shop/search/?text={encoded}&c={self.city_code}"
        return await self.scrape(f"Поиск: {query}", search_url, max_pages=1)
