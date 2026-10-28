"""Эврика (evrika.com): SSR-каталог на Next.js.

Раньше сайт отдавал JSON с HTML-плитками через `back.evrika.com`, но после перехода
витрины на Next.js поддомен `back.evrika.com` перенаправляет (301) на `evrika.com`
и возвращает полноценную HTML-страницу, из-за чего `r.json()` падал с `JSONDecodeError`.

Актуальная разметка карточек — блоки `.productCard-ui`, общее число товаров категории —
в `[class*="quantityOfProducts"]`, ссылки на страницы — в `ul.pagination`.
Если карточки в разметке есть, но ни одна не разобралась (сменились внутренние селекторы),
выбрасывается `ValueError`, чтобы ошибка парсера не маскировалась под пустую выдачу
или подтверждённый конец каталога.
"""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlsplit

from html_soup import make_soup
from scrapers import http as requests
from scrapers.base import PagedScraper, ScanResult, UnconfirmedEnd, parse_price


class EvrikaScraper(PagedScraper):
    SHOP_NAME = "Эврика"
    SHOP_EMOJI = "🔷"
    PAGE_PARAM = "page"

    API_HOST = "https://back.evrika.com"
    CITY_SLUG = "nur-sultan-astana"

    def __init__(self):
        self.base_url = "https://evrika.com"
        # id всех карточек категории, которые магазин показал (разобранных и намеренно пропущенных) —
        # их число сверяется со счётчиком категории (A02)
        self._observed: Dict[str, set] = {}
        self._declared: Dict[str, int] = {}  # счётчик категории с первой страницы
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _page_url(self, category_url: str, page: int) -> str:
        path = urlparse(category_url).path if category_url.startswith("http") else category_url
        if f"/catalog/{self.CITY_SLUG}/" not in path:
            path = path.replace("/catalog/", f"/catalog/{self.CITY_SLUG}/", 1)
        path = "/" + path.strip("/")
        if page <= 1:
            return f"{self.base_url}{path}"
        return f"{self.base_url}{path}?{self.PAGE_PARAM}={page}"

    # Совместимость со старым именем метода
    _api_url = _page_url

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        url = self._page_url(category_url, page_num)
        r = requests.get(url, headers=self.headers, impersonate="chrome124", timeout=30)
        if r.status_code == 404 and page_num > 1:
            raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")

        soup = make_soup(r.text or "")
        cards = soup.select(".productCard-ui")
        if not cards:
            raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

        products: List[Dict[str, Any]] = []
        seen_pids = set()
        unparsed = 0
        if page_num == 1:
            self._observed[category_url] = set()
        observed = self._observed.setdefault(category_url, set())

        for tile in cards:
            title_link = tile.select_one('a[class*="titleOfProduct"][href*="/p"], a[class*="titleOfProduct"]')
            link = title_link or tile.select_one('a[href*="/p"]')
            if not link:
                unparsed += 1  # структура карточки сменилась — это ошибка разбора, не пропуск
                continue

            href = (link.get("href") or "").strip()
            pid_match = re.search(r"/p(\d+)(?:[/?#]|$)", href)
            if not pid_match:
                unparsed += 1
                continue
            pid = pid_match.group(1)
            if pid in seen_pids:
                continue
            observed.add(pid)

            img = tile.find("img")
            raw_title = ""
            if title_link is not None:
                raw_title = title_link.get("title") or title_link.get_text(" ", strip=True)
            if not raw_title:
                raw_title = link.get("title") or link.get_text(" ", strip=True)
            if not raw_title and img is not None:
                raw_title = img.get("alt") or img.get("title") or ""
            title = " ".join(str(raw_title).split())
            if not title:
                unparsed += 1
                continue

            price_el = tile.select_one('[class*="currentPrice"]')
            if price_el is None:
                unparsed += 1  # нет элемента цены — сменился селектор (A01)
                continue
            price = parse_price(price_el.get_text(" ", strip=True))
            if price <= 0:
                continue  # цена есть, но не числовая («нет в наличии»): намеренный пропуск

            old_price_el = tile.select_one('[class*="costContainer"] [class*="oldPrice"]')
            old_price = parse_price(old_price_el.get_text(" ", strip=True)) if old_price_el else 0

            image_url = ""
            if img is not None:
                for attr in ("data-original-src", "data-original", "data-src", "data-lazy", "src"):
                    src = (img.get(attr) or "").strip()
                    if src and "empty.gif" not in src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"
                        break

            if href.startswith("http"):
                full_url = href.replace(self.API_HOST, self.base_url)
            elif href.startswith("/"):
                full_url = f"{self.base_url}{href}"
            else:
                full_url = f"{self.base_url}/{href}"

            seen_pids.add(pid)
            products.append({
                "shop": self.SHOP_NAME,
                "id": f"evrika_{pid}",
                "title": title,
                "category": category_name,
                "url": full_url,
                "image_url": image_url,
                "price": price,
                "old_price_on_site": old_price if old_price > price else 0,
                "city": "Астана",
            })

        if not products and unparsed:
            raise ValueError("Карточки Эврика найдены, но ни один товар не удалось разобрать")

        return ScanResult(
            products,
            complete=self._is_last_page(soup, category_url, page_num, len(observed)),
            unparsed=unparsed,
        )

    def _is_last_page(self, soup, category_url: str, page_num: int, observed_total: int) -> bool:
        """Последняя ли страница (A02). Наибольшая видимая ссылка пагинатора доказательством не считается:
        пагинатор бывает «оконным» и показывает лишь ближайшие страницы.

        * Есть счётчик категории (на странице или запомненный с первой) — конец, когда увидено столько
          уникальных карточек, сколько заявлено.
          Ссылка вперёд при этом не нужна; её отсутствие при недоборе — противоречие, не конец.
        * Счётчика нет — конец, только если на ЭТОЙ странице нет ссылки на следующую.
        """
        total = self._declared_total(soup)
        if page_num == 1:
            self._declared.pop(category_url, None)
        if total is not None and total > 0:
            self._declared[category_url] = total
        total = self._declared.get(category_url)
        if total:
            return observed_total >= total
        forward = self._paginator_last_page(soup, category_url)
        return forward is not None and forward <= page_num

    @staticmethod
    def _declared_total(soup) -> Optional[int]:
        """Число товаров в шапке категории: <span class="…__quantityOfProducts">32 товара</span>."""
        counter = soup.select_one('[class*="quantityOfProducts"]')
        if not counter:
            return None
        match = re.search(r"(\d[\d\s\u00a0]*)", counter.get_text(" ", strip=True))
        if not match:
            return None
        return int(re.sub(r"[^0-9]", "", match.group(1)))

    def _paginator_last_page(self, soup, category_url: str) -> Optional[int]:
        """Наибольший номер страницы, на который ведёт пагинатор ЭТОЙ страницы (страница 1 — без параметра)."""
        page_1_url = self._page_url(category_url, 1)
        expected_path = urlsplit(page_1_url).path.rstrip("/")
        pages: List[int] = []
        for a in soup.select("ul.pagination a[href]"):
            link = urlsplit(urljoin(page_1_url, a.get("href", "")))
            if link.path.rstrip("/") != expected_path:
                continue
            pages.extend(int(v) for v in parse_qs(link.query).get(self.PAGE_PARAM, []) if v.isdigit())
        if not soup.select("ul.pagination"):
            return None
        return max(pages) if pages else 1
