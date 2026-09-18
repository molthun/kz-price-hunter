import re
import asyncio
from typing import List, Dict, Any
from curl_cffi import requests
from bs4 import BeautifulSoup
from scrapers.base import ScanResult, parse_price

class ShopKzScraper:
    SHOP_NAME = "Белый Ветер"
    SHOP_EMOJI = "🟦"

    def __init__(self):
        self.base_url = "https://shop.kz"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        self.cookies = {"BITRIX_SM_CITY": "astana"}
        self.yml_url = "https://shop.kz/bitrix/catalog_export/yandex.php"

    async def scrape(self, category_name: str, category_url: str, max_pages: int = 1) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._scrape_sync, category_name, category_url, max_pages)

    def scrape_yml(self, yml_url: str = None, in_stock_only: bool = True) -> List[Dict[str, Any]]:
        """Парсинг официальной YML-выгрузки shop.kz (16 000+ товаров в потоковом режиме)."""
        import xml.etree.ElementTree as ET
        import tempfile
        import os

        target_url = yml_url or self.yml_url
        products: List[Dict[str, Any]] = []

        tmp_path = None
        try:
            r = requests.get(
                target_url,
                headers=self.headers,
                impersonate="chrome124",
                timeout=90,
                stream=True
            )
            if r.status_code != 200:
                print(f"[{self.SHOP_NAME}] Ошибка скачивания YML: HTTP {r.status_code}")
                raise RuntimeError(f"HTTP {r.status_code}")

            with tempfile.NamedTemporaryFile(delete=False, suffix=".xml") as tmp:
                tmp_path = tmp.name
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    tmp.write(chunk)

            for event, elem in ET.iterparse(tmp_path, events=("end",)):
                if elem.tag == "offer":
                    pid = elem.get("id", "")
                    avail = elem.get("available") == "true"
                    if in_stock_only and not avail:
                        elem.clear()
                        continue

                    title = elem.findtext("name") or elem.findtext("model") or ""
                    url = elem.findtext("url") or ""
                    price_str = elem.findtext("price") or "0"
                    oldprice_str = elem.findtext("oldprice") or "0"
                    pic = elem.findtext("picture") or ""
                    cat = ""
                    for param in elem.findall("param"):
                        if param.get("name") == "category":
                            cat = param.text or ""
                            break

                    p_val = parse_price(price_str)
                    old_p_val = parse_price(oldprice_str)

                    if p_val > 0 and title:
                        products.append({
                            "shop": self.SHOP_NAME,
                            "id": f"shopkz_{pid}",
                            "title": title.strip(),
                            "category": cat or "Комплектующие и электроника",
                            "url": url.strip(),
                            "image_url": pic.strip(),
                            "price": p_val,
                            "old_price_on_site": old_p_val,
                            "city": "Астана"
                        })
                    elem.clear()

            print(f"[{self.SHOP_NAME}] ✅ Успешно выгружено {len(products)} товаров из официального YML фида!")
        except Exception as e:
            return ScanResult(products, error=type(e).__name__)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

        return ScanResult(products, complete=bool(products), error=None if products else "Пустая выгрузка")

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: int) -> List[Dict[str, Any]]:
        # Если передан URL YML выгрузки
        if "yandex.php" in category_url or "catalog_export" in category_url or "yml" in category_name.lower():
            return self.scrape_yml(category_url, in_stock_only=True)

        products: List[Dict[str, Any]] = []

        for page_num in range(1, max_pages + 1):
            url = category_url
            if page_num > 1:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}PAGEN_1={page_num}"

            try:
                r = requests.get(
                    url,
                    headers=self.headers,
                    cookies=self.cookies,
                    impersonate="chrome124",
                    timeout=15
                )
                if r.status_code != 200:
                    break

                soup = BeautifulSoup(r.text, "html.parser")
                cards = soup.select(".bx_catalog_item")
                if not cards:
                    break

                for c in cards:
                    # 1. ID товара
                    pid = c.get("data-product-id") or ""

                    # 2. Название и ссылка
                    title_el = c.select_one(".bx_catalog_item_title a")
                    if not title_el:
                        continue
                    title = title_el.text.strip()
                    rel_link = title_el.get("href", "")
                    if not pid:
                        match = re.search(r"/offer/([^/]+)/", rel_link)
                        pid = match.group(1) if match else title[:20]

                    full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                    # 3. Фото
                    img_el = c.select_one(".bx_catalog_item_images img, img")
                    image_url = ""
                    if img_el:
                        src = img_el.get("data-src") or img_el.get("src") or ""
                        if src and not src.endswith("1.gif"):
                            if src.startswith("//"):
                                image_url = f"https:{src}"
                            elif src.startswith("/"):
                                image_url = f"{self.base_url}{src}"
                            else:
                                image_url = src

                    # 4. Актуальная цена из .current_price
                    curr_price_el = c.select_one(".current_price span, .current_price")
                    if not curr_price_el:
                        continue
                    current_price = parse_price(curr_price_el.text)
                    if current_price <= 0:
                        continue

                    # 5. Старая цена из .old_price
                    old_price_el = c.select_one(".old_price span, .old_price")
                    old_price = 0
                    if old_price_el:
                        old_price = parse_price(old_price_el.text)

                    products.append({
                        "shop": self.SHOP_NAME,
                        "id": f"shopkz_{pid}",
                        "title": title,
                        "category": category_name,
                        "url": full_link,
                        "image_url": image_url,
                        "price": current_price,
                        "old_price_on_site": old_price if old_price > current_price else 0,
                        "city": "Астана"
                    })

            except Exception as e:
                print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
                break

        return products
