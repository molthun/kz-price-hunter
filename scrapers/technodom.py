import re
import hashlib
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

                    # Извлекаем все карточки за один вызов внутри браузера (быстро и без рассинхронизации DOM)
                    raw_cards = await page.evaluate(r"""() => {
                        const results = [];
                        const seen = new Set();
                        
                        const cardElements = document.querySelectorAll('[data-testid="product-card"], [class*="ProductCardV_card"], [class*="product-card"]');
                        
                        for (const el of cardElements) {
                            const a = el.closest('a') || el.querySelector('a[href*="/p/"]');
                            if (!a) continue;
                            
                            const rawHref = a.getAttribute('href') || a.href || '';
                            if (!rawHref || !rawHref.includes('/p/')) continue;
                            
                            // Канонизируем ссылку: отсекаем query-параметры (?recommended_by=... и т.д.) и хэши
                            const cleanHref = rawHref.split('?')[0].split('#')[0].replace(/\/+$/, '');
                            if (seen.has(cleanHref)) continue;
                            seen.add(cleanHref);
                            
                            // Название товара
                            let title = '';
                            const titleP = el.querySelector('p[class*="title__"], [class*="ProductCardV_title__"], [class*="ProductCard_title"]');
                            if (titleP && titleP.innerText.trim().length > 5) {
                                title = titleP.innerText.trim();
                            } else {
                                const ps = el.querySelectorAll('p');
                                for (const p of ps) {
                                    const txt = p.innerText.trim();
                                    if (txt.length > 15 && !txt.includes('₸') && !txt.includes('бонусов') && !txt.includes('мес') && !txt.includes('В корзину') && !txt.includes('Самовывоз')) {
                                        title = txt;
                                        break;
                                    }
                                }
                            }
                            
                            if (!title) {
                                const slug = cleanHref.split('/').pop() || '';
                                title = slug.replace(/-\d+$/, '').replace(/-/g, ' ');
                            }
                            
                            // Картинка товара
                            const img = el.querySelector('img');
                            const image_url = img ? (img.getAttribute('src') || img.src || '') : '';
                            
                            // Текущая цена
                            let current_price = 0;
                            const priceEl = el.querySelector('p[class*="ProductCardPrices_price__"], [class*="price__oCsLy"]') || 
                                            el.querySelector('[class*="ProductCardPrices_price"]:not([class*="pricesInfo"])');
                            if (priceEl) {
                                const digits = priceEl.innerText.replace(/[^\d]/g, '');
                                if (digits) current_price = parseInt(digits, 10);
                            }
                            
                            // Старая цена (зачёркнутая)
                            let old_price = 0;
                            const oldPriceEl = el.querySelector('p[class*="ProductCardPrices_oldPrice__"], [class*="oldPrice__"]') ||
                                               el.querySelector('[class*="ProductCardPrices_oldPrice"]');
                            if (oldPriceEl) {
                                const digits = oldPriceEl.innerText.replace(/[^\d]/g, '');
                                if (digits) old_price = parseInt(digits, 10);
                            }
                            
                            // Фолбэк на случай изменения структуры стилей
                            if (!current_price) {
                                const priceNodes = Array.from(el.querySelectorAll('p, span')).filter(node => 
                                    node.children.length === 0 && node.innerText && node.innerText.includes('₸') && !node.innerText.includes('мес') && !node.innerText.includes('бонус')
                                );
                                if (priceNodes.length > 0) {
                                    const p0 = parseInt(priceNodes[0].innerText.replace(/[^\d]/g, ''), 10);
                                    if (p0 >= 1000) current_price = p0;
                                }
                                if (priceNodes.length > 1) {
                                    const p1 = parseInt(priceNodes[1].innerText.replace(/[^\d]/g, ''), 10);
                                    if (p1 >= 1000 && p1 > current_price) old_price = p1;
                                }
                            }
                            
                            results.push({
                                cleanHref,
                                title,
                                image_url,
                                current_price,
                                old_price
                            });
                        }
                        return results;
                    }""")

                    for item in raw_cards:
                        clean_path = item["cleanHref"]
                        current_price = item["current_price"]
                        old_price = item["old_price"]
                        title = item["title"]
                        image_url = item["image_url"]

                        if not clean_path or current_price <= 0 or not title:
                            continue

                        # Извлечение чистого числового ID артикула Technodom
                        pid_match = re.search(r"[-_=](\d+)(?:[a-zA-Z]*)$", clean_path)
                        if pid_match:
                            pid = pid_match.group(1)
                        else:
                            skus = re.findall(r"\d{5,7}", clean_path)
                            if skus:
                                pid = skus[-1]
                            else:
                                pid = hashlib.md5(clean_path.encode()).hexdigest()[:12]

                        full_link = f"{self.base_url}{clean_path}" if clean_path.startswith("/") else clean_path

                        products.append({
                            "shop": self.SHOP_NAME,
                            "id": f"td_{pid}",
                            "title": title,
                            "category": category_name,
                            "url": full_link,
                            "image_url": image_url,
                            "price": current_price,
                            "old_price_on_site": old_price if old_price > current_price else 0,
                            "city": "Астана"
                        })

                    await asyncio.sleep(1.0)

                except Exception as e:
                    print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                    break

            await browser.close()
        return products
