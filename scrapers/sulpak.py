import re
import hashlib
import time
from typing import List, Dict, Any, Iterable, Optional, Tuple
from urllib.parse import parse_qs, urlsplit, urlunsplit
from scrapers import http as requests
from scrapers.base import (
    UnconfirmedEnd,
    PagedScraper,
    parse_price,
    price_value,
    ScanResult,
    CityControl,
    merge_city_results,
    validate_product_item,
)
from html_soup import make_soup
class SulpakScraper(PagedScraper):
    SHOP_NAME = "Sulpak"
    SHOP_EMOJI = "🟢"
    # Подтверждено на живом sulpak.kz 19.11.2026: city_id=1 -> Алматы, city_id=2 -> Актобе, city_id=3 -> Астана
    CITY_IDS: Dict[str, str] = {
        "Алматы": "1",
        "Актобе": "2",
        "Астана": "3",
    }
    # На живом sulpak.kz /f/<cat> без суффикса города часто отдаёт Алматы (city_id=1)
    # даже с cookie city_id=3; канонический URL Астаны — /f/<cat>/astana.
    CITY_SLUGS: Dict[str, str] = {
        "Алматы": "",
        "Актобе": "aktobe",
        "Астана": "astana",
    }
    DEFAULT_CITIES: Tuple[str, ...] = ("Алматы", "Астана")

    def __init__(self, cities: Optional[Iterable[str]] = None):
        self.base_url = "https://www.sulpak.kz"
        self.cities: Tuple[str, ...] = tuple(cities) if cities is not None else self.DEFAULT_CITIES
        self._active_city: str = self.cities[0] if self.cities else "Алматы"
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
        self.cookies = {"city_id": self.CITY_IDS.get(self._active_city, "1"), "language_id": "3"}

    # Контрольная категория для JSON распродажи (город в ответе не возвращается):
    # живьём 19.11.2026 у Алматы и Астаны было по 13 собственных товаров из 44.
    CITY_CONTROL_URL = "https://www.sulpak.kz/sale/1"
    CITY_CONTROL_PAGES = 2

    def _city_control_snapshot(self, city: str) -> Dict[str, Any]:
        probe = SulpakScraper(cities=(city,))
        out: Dict[str, Any] = {}
        for page in range(1, self.CITY_CONTROL_PAGES + 1):
            for item in probe._fetch_page("контроль города", self.CITY_CONTROL_URL, page):
                out[item["id"]] = item["price"]
        return out

    @staticmethod
    def _is_out_of_stock(card: Any) -> bool:
        """Магазин сам пометил карточку отсутствующей (живьём: data-price="0" и «Нет в наличии»)."""
        return "нет в наличии" in card.get_text(" ", strip=True).lower()

    def _confirm_city(self, soup: Any, has_cards: bool) -> None:
        """R1-A01: HTML-страница каталога должна сама подтвердить город (атрибуты data-city-id).

        Проверка идёт до любого сигнала конца каталога: пустая страница управляет полнотой
        и снятием товаров. Чужой город — всегда ошибка разбора. Страница с карточками без
        маркера — ошибка разбора; пустая страница без маркера — неподтверждённый конец.
        JSON-выдача распродажи город не возвращает (открытый риск R1, docs/REGIONAL_PRICING.md §3).
        """
        expected = str(self.cookies.get("city_id") or "")
        seen = {str(e.get("data-city-id") or "").strip() for e in soup.select("[data-city-id]")}
        seen.discard("")
        if seen and seen != {expected}:
            raise ValueError(f"Sulpak отдал город {sorted(seen)} вместо запрошенного {expected}")
        if not seen:
            if has_cards:
                raise ValueError(f"Sulpak не подтвердил город «{self._active_city}» в ответе")
            raise UnconfirmedEnd(f"Sulpak не подтвердил город «{self._active_city}» на пустой странице")

    @classmethod
    def _city_url(cls, category_url: str, city: str) -> str:
        """Возвращает URL категории Sulpak с суффиксом города (/astana, /aktobe) для не-алматинских городов."""
        slug = cls.CITY_SLUGS.get(city, "")
        parts = urlsplit(category_url)
        path = parts.path.rstrip("/")
        if not path.startswith("/f/"):
            return category_url
        known_suffixes = {s for s in cls.CITY_SLUGS.values() if s} | {"almaty"}
        segs = path.split("/")
        if len(segs) >= 4 and segs[-1].lower() in known_suffixes:
            segs = segs[:-1]
        if slug:
            segs.append(slug)
        new_path = "/".join(segs)
        return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))

    @staticmethod
    def _parse_paginator(pag_soup: Any) -> Tuple[Optional[int], Optional[int]]:
        """Извлекает (data-currentpage, data-pagescount) из #paginator, игнорируя .swiper-pagination."""
        if not pag_soup:
            return None, None
        pag_el = pag_soup.select_one("#paginator, .pagination:not(.swiper-pagination)")
        if not pag_el:
            return None, None
        cur_raw = str(pag_el.get("data-currentpage") or "").strip()
        cnt_raw = str(pag_el.get("data-pagescount") or "").strip()
        current_page = int(cur_raw) if cur_raw.isdigit() else None
        pages_count = int(cnt_raw) if cnt_raw.isdigit() else None
        return current_page, pages_count

    @staticmethod
    def _is_redirected_to_prev_page(
        response: Any,
        category_url: str,
        page_num: int,
        *,
        require_explicit_page_param: bool = False,
    ) -> bool:
        """Проверяет, что сервер перенаправил запрос page_num на предыдущую страницу той же категории.

        На живом sulpak.kz data-pagescount часто завышен относительно числа реальных страниц
        (а на одностраничных категориях #paginator отсутствует); при выходе за последнюю
        страницу N сервер возвращает HTTP 301 на ?page=N (N == page_num - 1).
        Обычный повтор карточек без редиректа или сброс на первую страницу концом не считается.
        """
        if page_num <= 1:
            return False
        final_url = getattr(response, "url", None)
        if not isinstance(final_url, str) or not final_url.strip():
            return False
        final_parts = urlsplit(final_url.strip())
        cat_parts = urlsplit(category_url.strip())
        if final_parts.path.rstrip("/") != cat_parts.path.rstrip("/"):
            return False
        if cat_parts.hostname and final_parts.hostname and final_parts.hostname.lower() != cat_parts.hostname.lower():
            return False
        final_pages = parse_qs(final_parts.query).get("page")
        if page_num == 2 and not require_explicit_page_param:
            return not final_pages or final_pages[-1] == "1"
        return bool(final_pages) and final_pages[-1] == str(page_num - 1)

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> ScanResult:
        is_sale = "/sale" in category_url.lower()

        try:
            if is_sale:
                url = f"{self.base_url}/SaleLoadProducts/{page_num}/~/~/~/~/~/popularitydesc/tiles"
                headers = {**self.headers, "X-Requested-With": "XMLHttpRequest"}
                for attempt in range(2):
                    try:
                        r = requests.post(url, data={"page": page_num}, headers=headers,
                                          cookies=self.cookies, impersonate="chrome124", timeout=15)
                        break
                    except requests.exceptions.Timeout:
                        if attempt:
                            raise
                        time.sleep(1)

                # Сначала код ответа: пустое тело при 500 или 403 — это сбой, а не конец каталога.
                # Иначе неудачный обход выглядел бы успешно завершённым (правило P02).
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                if not r.text or not r.text.strip():
                    return ScanResult([], complete=True)

                try:
                    data = r.json()
                except Exception as e:
                    raise RuntimeError(f"Некорректный JSON от Sulpak Sale API: {e}")

                products_html = data.get("products", "")
                paginator_html = data.get("paginator", "")
                if not products_html or not products_html.strip():
                    return ScanResult([], complete=True)

                soup = make_soup(products_html)
                pag_soup = make_soup(paginator_html) if paginator_html else None
            else:
                request_base_url = self._city_url(category_url, self._active_city)
                url = request_base_url
                if page_num > 1:
                    separator = "&" if "?" in url else "?"
                    url = f"{url}{separator}page={page_num}"

                for attempt in range(2):
                    try:
                        r = requests.get(url, headers=self.headers, cookies=self.cookies,
                                         impersonate="chrome124", timeout=15)
                        break
                    except requests.exceptions.Timeout:
                        if attempt:
                            raise
                        time.sleep(1)

                # 404 после последней страницы похож на конец каталога, но так же выглядит и блокировка
                # или сбой CDN. Считать это подтверждённым концом нельзя: тогда оборванный обход
                # пометит все недосмотренные товары как снятые с продажи. Обход помечается неполным.
                if r.status_code == 404 and page_num > 1:
                    raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")

                soup = make_soup(r.text)
                pag_soup = soup

            cards = soup.select(".product__item, .goods-tiles")
            if not is_sale:
                self._confirm_city(soup, has_cards=bool(cards))
            if not cards:
                if page_num > 1:
                    return ScanResult([], complete=True)
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            current_page, pages_count = self._parse_paginator(pag_soup)
            if not is_sale and page_num > 1:
                paginated_redirect_end = (
                    current_page == page_num - 1
                    and pages_count is not None
                    and pages_count >= current_page
                    and self._is_redirected_to_prev_page(r, request_base_url, page_num)
                )
                single_page_redirect_end = (
                    page_num == 2
                    and current_page is None
                    and pages_count is None
                    and self._is_redirected_to_prev_page(
                        r, request_base_url, page_num, require_explicit_page_param=True
                    )
                )
                if paginated_redirect_end or single_page_redirect_end:
                    # Сервер подтвердил конец каталога редиректом ?page=N+1 -> ?page=N
                    # (проверка города уже пройдена выше по R1-A01).
                    return ScanResult([], complete=True)

            products: List[Dict[str, Any]] = []
            seen_codes = set()

            # SULPAK-A01: видимая карточка товара, не разобранная по технической причине (сломана
            # разметка цены/названия/ссылки), учитывается в unparsed и запрещает complete/снятие.
            # Не ошибки: рекламные вставки сетки (.product__item без кода, названия и ссылки)
            # и подтверждённое отсутствие («нет в наличии»).
            unparsed = 0
            for c in cards:
                pid = c.get("data-code") or c.get("data-product-id") or ""
                title = c.get("data-name") or ""
                data_price = c.get("data-price") or ""
                link_el = c.find("a", href=lambda h: h and ("/g/" in h or "/p/" in h))

                if not (pid or title or link_el):
                    continue  # рекламная вставка сетки, не товар

                if pid and pid in seen_codes:
                    continue
                if pid:
                    seen_codes.add(pid)

                if self._is_out_of_stock(c):
                    continue  # подтверждённое отсутствие товара, не ошибка разбора

                if not link_el:
                    unparsed += 1
                    continue

                rel_link = link_el.get("href", "")
                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                if not title:
                    title = link_el.text.strip()
                if not title:
                    unparsed += 1
                    continue

                img_el = c.find("img")
                image_url = ""
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                if data_price:
                    current_price = price_value(data_price)
                else:
                    price_el = c.select_one(".product__item-price, .price")
                    current_price = parse_price(price_el.text) if price_el else 0

                if current_price <= 0:
                    unparsed += 1
                    continue

                old_price_el = c.select_one(".product__item-price-old, .old-price")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                clean_path = rel_link.split("?")[0].split("#")[0]
                safe_id = pid if pid else (
                    re.search(r"[-_](\d+)$", clean_path).group(1)
                    if re.search(r"[-_](\d+)$", clean_path)
                    else hashlib.md5(clean_path.encode()).hexdigest()[:12]
                )

                item = {
                    "shop": self.SHOP_NAME,
                    "id": f"sulpak_{safe_id}",
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": current_price,
                    "old_price_on_site": old_price if old_price > current_price else 0,
                    "city": self._active_city,
                }
                if validate_product_item(item):
                    products.append(item)
                else:
                    unparsed += 1

            complete = pages_count is not None and page_num >= pages_count
            return ScanResult(products, complete=complete, unparsed=unparsed)

        except UnconfirmedEnd:
            raise
        except Exception:
            raise

    def _scrape_sync(self, category_name: str, category_url: str, max_pages: Optional[int] = None) -> ScanResult:
        """Обходит категорию по городам из self.cities; complete=True только если все города завершились полностью."""
        cities = self.cities or ("Алматы",)
        if len(cities) == 1:
            self._active_city = cities[0]
            self.cookies = {"city_id": self.CITY_IDS.get(self._active_city, "1"), "language_id": "3"}
            return super()._scrape_sync(category_name, category_url, max_pages=max_pages)

        results: List[Tuple[str, Any]] = []
        orig_city, orig_cookies = self._active_city, dict(self.cookies)
        try:
            is_sale = "/sale" in category_url.lower()
            for idx, city in enumerate(cities):
                if idx and is_sale:
                    # JSON распродажи город не возвращает — нужен контроль различия городов
                    ok, reason = CityControl.check(self.SHOP_NAME, cities[0], city, self._city_control_snapshot)
                    if not ok:
                        results.append((city, CityControl.skipped(reason)))
                        continue
                self._active_city = city
                self.cookies = {"city_id": self.CITY_IDS.get(city, "1"), "language_id": "3"}
                res = super()._scrape_sync(category_name, category_url, max_pages=max_pages)
                results.append((city, res))
        finally:
            self._active_city = orig_city
            self.cookies = orig_cookies
        return merge_city_results(results)
