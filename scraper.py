import asyncio
import re
from typing import List, Dict, Any
from playwright.async_api import async_playwright
from config import BASE_URL, CITY_NAME

def parse_price(price_str: str) -> int:
    """Очищает строку цены (например, '149 150 ₸') и переводит в целое число."""
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else 0

class DNSScraper:
    def __init__(self):
        self.user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )

    async def scrape_category(self, category_name: str, category_url: str, max_pages: int = 2) -> List[Dict[str, Any]]:
        """
        Сканирует страницы категории DNS для города Астана.
        Возвращает список словарей с информацией о товарах.
        """
        products: List[Dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=self.user_agent,
                locale="ru-RU",
                viewport={"width": 1920, "height": 1080}
            )

            # Устанавливаем cookie города Астана
            await context.add_cookies([
                {
                    "name": "city_path",
                    "value": "astana",
                    "domain": ".dns-shop.kz",
                    "path": "/"
                }
            ])

            page = await context.new_page()

            for page_num in range(1, max_pages + 1):
                url = category_url
                if page_num > 1:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}p={page_num}"

                print(f"[{category_name}] Загрузка стр. {page_num}: {url}")
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    if not resp or resp.status != 200:
                        print(f"  Внимание: статус ответа {resp.status if resp else 'None'}")
                        break

                    # Ожидаем появления карточек товаров
                    try:
                        await page.wait_for_selector(".catalog-product", timeout=12000)
                    except Exception:
                        print("  Карточки товаров не найдены на странице.")
                        break

                    # Даем скриптам DNS время подгрузить динамические цены
                    await asyncio.sleep(2)

                    # Извлекаем карточки со страницы
                    product_elements = await page.query_selector_all(".catalog-product")
                    print(f"  Найдено карточек: {len(product_elements)}")

                    for el in product_elements:
                        # 1. ID товара
                        pid = await el.get_attribute("data-code")
                        if not pid:
                            pid = await el.get_attribute("data-product")
                        if not pid:
                            continue

                        # 2. Название и ссылка
                        name_el = await el.query_selector(".catalog-product__name")
                        if not name_el:
                            continue
                        title = (await name_el.inner_text()).strip()
                        rel_link = await name_el.get_attribute("href") or ""
                        full_link = f"{BASE_URL}{rel_link}" if rel_link.startswith("/") else rel_link

                        # 3. Картинка
                        img_el = await el.query_selector("img")
                        image_url = ""
                        if img_el:
                            image_url = await img_el.get_attribute("data-src") or await img_el.get_attribute("src") or ""

                        # 4. Цена
                        price_el = await el.query_selector(".product-buy__price")
                        if not price_el:
                            continue
                        price_raw = await price_el.inner_text()
                        price = parse_price(price_raw)
                        if price <= 0:
                            continue

                        products.append({
                            "id": pid,
                            "title": title,
                            "category": category_name,
                            "url": full_link,
                            "image_url": image_url,
                            "price": price,
                            "city": CITY_NAME
                        })

                    # Пауза перед следующей страницей во избежание WAF rate-limit
                    await asyncio.sleep(2)

                except Exception as e:
                    print(f"  Ошибка при обработке страницы {url}: {e}")
                    break

            await browser.close()

        return products
