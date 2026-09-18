"""ТехноGRAD (tgrad.kz): бытовая техника и электроника со склада в Алматы.

Сайт на Bitrix. Категории лежат в корне (`/stiralnye-mashiny/`), страницы — `/page-N/`.
У каждой карточки есть кнопка корзины с JSON в `data-ga` (артикул, название, цена, бренд),
поэтому цена и идентификатор берутся из него, а ссылка, фото и старая цена — из разметки.
После последней страницы сайт отвечает редиректом 302 на первую; другой адрес редиректа
(вход, блокировка, другой раздел) концом каталога не считается.
"""
import json
from typing import Any, Dict, List
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from scrapers import http as requests
from scrapers.base import PagedScraper, ScanResult, parse_price

BASE_URL = "https://tgrad.kz"


def same_page(a: str, b: str) -> bool:
    """Один адрес страницы: сравниваются схема, хост, порт (443/80 по умолчанию) и путь."""
    def key(url):
        parts = urlsplit(url)
        port = parts.port or {"https": 443, "http": 80}.get(parts.scheme)
        return parts.scheme, (parts.hostname or "").lower(), port, parts.path.rstrip("/"), parts.query
    return key(a) == key(b)

class TgradScraper(PagedScraper):
    SHOP_NAME = "Tgrad"
    SHOP_EMOJI = "🟪"
    PAGE_DELAY_SECONDS = 0.5

    def __init__(self):
        self._session = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session(impersonate="chrome124")
            self._session.headers.update({"Accept-Language": "ru-RU,ru;q=0.9"})
        return self._session

    def page_url(self, category_url: str, page: int) -> str:
        base = category_url if category_url.endswith("/") else category_url + "/"
        return base if page <= 1 else f"{base}page-{page}/"

    def _fetch_page(self, category_name: str, category_url: str, page_num: int):
        url = self.page_url(category_url, page_num)
        # Редирект после последней страницы — признак конца, поэтому редиректы не выполняем
        r = self._get_session().get(url, timeout=45, allow_redirects=False)
        if r.status_code in (301, 302) and page_num > 1:
            # Конец выдачи — только редирект на первую страницу этой же категории
            target = urljoin(url, r.headers.get("Location") or "")
            if r.headers.get("Location") and same_page(target, self.page_url(category_url, 1)):
                return ScanResult([], complete=True)
            raise RuntimeError(f"Неожиданный редирект HTTP {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        return self.parse_page(r.text, category_name, page_num)

    @classmethod
    def parse_page(cls, html: str, category_name: str, page_num: int) -> ScanResult:
        soup = BeautifulSoup(html, "html.parser")
        products: List[Dict[str, Any]] = []
        seen = set()

        for button in soup.select("button.catalog-item-button[data-ga]"):
            # Блоки «часто покупают» в меню повторяют чужие товары — пропускаем их
            if button.find_parent(class_=["menu-right__product", "catalog__desktop-menu"]):
                continue
            try:
                info = json.loads(button["data-ga"])[0]
            except (ValueError, KeyError, IndexError, TypeError):
                continue

            pid = str(info.get("id") or "").strip()
            title = (info.get("name") or "").strip()
            price = parse_price(str(info.get("price") or ""))
            if not pid or not title or price <= 0 or pid in seen:
                continue
            seen.add(pid)

            card = button.find_parent(class_="product__block") or button.parent
            link = card.select_one("a.product__name[href], a.product__block_price[href]") if card else None
            old_el = card.select_one(".old__price") if card else None
            img = card.select_one(".product__img img") if card else None
            old_price = parse_price(old_el.get_text()) if old_el else 0

            products.append({
                "shop": cls.SHOP_NAME,
                "id": f"tgrad_{pid}",
                "title": title,
                "category": category_name,
                "url": urljoin(BASE_URL, link["href"]) if link else BASE_URL,
                "image_url": urljoin(BASE_URL, img.get("src", "")) if img and img.get("src") else "",
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": "Алматы",
            })

        # Конец выдачи подтверждается отсутствием ссылки на следующую страницу
        has_next = bool(soup.select_one(f'a[href$="/page-{page_num + 1}/"]'))
        return ScanResult(products, complete=bool(products) and not has_next)
