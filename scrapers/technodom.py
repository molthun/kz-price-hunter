import re
import asyncio
from typing import List, Dict, Any
from playwright.async_api import async_playwright

def parse_price(price_str: str) -> int:
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0

class TechnodomScraper:
    SHOP_NAME = "Технодом"
    SHOP_EMOJI = "🔴"

    def __init__(self):
        self.base_url = "https://www.technodom.kz"
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    async def scrape(self, category_name: str, category_url: str, max_pages: int = 1) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.user_agent,
                locale="ru-RU",
                viewport={"width": 1920, "height": 1080}
            )

            # Куки для города Астана в Технодоме
            await context.add_cookies([
                {"name": "city", "value": "astana", "domain": ".technodom.kz", "path": "/"},
                {"name": "city_id", "value": "1", "domain": ".technodom.kz", "path": "/"}
            ])

            page = await context.new_page()

            for page_num in range(1, max_pages + 1):
                url = category_url
                if page_num > 1:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}page={page_num}"

                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    if not resp or resp.status != 200:
                        break

                    await asyncio.sleep(2.5)
                    links = await page.query_selector_all("a[href*=\"/p/\"]")
                    if not links:
                        break

                    seen_urls = set()

                    for l in links:
                        rel_link = await l.get_attribute("href") or ""
                        if not rel_link or rel_link in seen_urls:
                            continue
                        seen_urls.add(rel_link)

                        full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                        # Получаем карточку (li или родитель)
                        card = await l.evaluate_handle("el => el.closest('li') || el.parentElement")
                        card_text = await card.inner_text()

                        # Название товара
                        title = ""
                        p_titles = await card.query_selector_all("p, a")
                        for pt in p_titles:
                            t_text = (await pt.inner_text()).strip()
                            if len(t_text) > 15 and not any(k in t_text.lower() for k in ["корзину", "бонусов", "доставим", "самовывоз"]):
                                title = t_text
                                break
                        if not title:
                            # Извлекаем из ссылки
                            slug = rel_link.split("/")[-1]
                            title = slug.replace("-", " ").title()

                        # Картинка
                        img_el = await card.query_selector("img")
                        image_url = ""
                        if img_el:
                            image_url = await img_el.get_attribute("src") or ""

                        # Поиск цен в тексте карточки
                        # Цены идут как '299 990 ₸', а рассрочка как 'x 24 мес'
                        price_matches = re.findall(r"(\d[\d\s]+)\s*(?:₸|тг)", card_text)
                        valid_prices = []
                        for pm in price_matches:
                            val = parse_price(pm)
                            # Отсекаем мелкие платежи рассрочки (< 25 000 ₸) и бонусы
                            if val >= 25_000:
                                valid_prices.append(val)

                        if not valid_prices:
                            continue

                        # Текущая цена — наименьшая из основных цен
                        current_price = valid_prices[0]
                        old_price = valid_prices[1] if len(valid_prices) > 1 and valid_prices[1] > current_price else 0

                        # ID из ссылки
                        pid_match = re.search(r"-(\d+)$", rel_link)
                        pid = pid_match.group(1) if pid_match else rel_link[-15:]

                        products.append({
                            "shop": self.SHOP_NAME,
                            "id": f"td_{pid}",
                            "title": title,
                            "category": category_name,
                            "url": full_link,
                            "image_url": image_url,
                            "price": current_price,
                            "old_price_on_site": old_price,
                            "city": "Астана"
                        })

                    await asyncio.sleep(1.5)

                except Exception as e:
                    print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                    break

            await browser.close()
        return products
