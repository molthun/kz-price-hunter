"""Скрапер сети электроники «Alser» (alser.kz).

Каталог отдаётся сервером целиком, но небольшой: сайт сам печатает «N товаров» в блоке
p.total-text, и это же число подтверждает API магазина. Счётчик читается, чтобы отличить
полностью собранную маленькую категорию от обхода, оборвавшегося на первой странице.

Без этого любая категория возвращалась limited: вторая страница карточек не содержит, адаптер
бросал UnconfirmedEnd, и базовый класс помечал обход неполным. По правилу P02 неполные обходы
не обучают норму источника, поэтому baseline у магазина не строился никогда.
"""
import re
from typing import Any, Dict, List, Optional
from scrapers import http as requests
from scrapers.base import slug_id, UnconfirmedEnd, PagedScraper, ScanResult, parse_price
from html_soup import make_soup
class AlserScraper(PagedScraper):
    SHOP_NAME = "Alser"
    SHOP_EMOJI = "🟡"

    def __init__(self):
        self.base_url = "https://alser.kz"
        # Увиденные карточки категории (разобранные и намеренно пропущенные) — по категории, чтобы
        # параллельный обход разных категорий одним экземпляром не путал их; их число сверяется
        # со счётчиком магазина (A01/A02)
        self._observed: Dict[str, set] = {}
        self._declared: Dict[str, int] = {}  # счётчик категории с первой страницы
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "ru-RU,ru;q=0.9",
        }

    def _fetch_page(self, category_name: str, category_url: str, page_num: int) -> List[Dict[str, Any]]:
        products: List[Dict[str, Any]] = []

        # Гарантируем город Астана в URL пути
        base_cat_url = category_url
        if "/astana/" not in base_cat_url and "alser.kz/c/" in base_cat_url:
            base_cat_url = base_cat_url.replace("alser.kz/c/", "alser.kz/astana/c/")

        url = base_cat_url
        if page_num > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}page={page_num}"

        try:
            r = requests.get(
                url,
                headers=self.headers,
                impersonate="chrome124",
                timeout=15
            )
            if r.status_code == 404 and page_num > 1:
                raise UnconfirmedEnd("HTTP 404 после последней доступной страницы")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")

            soup = make_soup(r.text)
            self._check_city(soup, r.text)
            cards = soup.select("article.product-card")
            if not cards:
                raise UnconfirmedEnd("Не найдены карточки: конец выдачи не подтвержден")

            seen_urls = set()
            unparsed = 0
            if page_num == 1:
                self._observed[category_url] = set()
            observed = self._observed.setdefault(category_url, set())

            for c in cards:
                # Ссылка
                link_el = c.find_parent("a") or c.select_one("a[href*='/p/']")
                if not link_el:
                    unparsed += 1  # структура карточки сменилась — ошибка разбора, не пропуск
                    continue
                rel_link = link_el.get("href", "")
                if not rel_link:
                    unparsed += 1
                    continue
                if rel_link in seen_urls:
                    continue
                seen_urls.add(rel_link)
                observed.add(rel_link)

                full_link = f"{self.base_url}{rel_link}" if rel_link.startswith("/") else rel_link

                # Название
                title_el = c.select_one(".product-card__title")
                title = title_el.text.strip() if title_el else ""
                if not title:
                    title = link_el.get("title", "") or link_el.text.strip()
                title = " ".join(title.split())
                if not title:
                    unparsed += 1
                    continue

                # Цены
                price_el = c.select_one(".info-container-product-price")
                if price_el is None:
                    unparsed += 1  # нет элемента цены — сменился селектор (A01)
                    continue
                current_price = parse_price(price_el.text)
                if current_price <= 0:
                    continue  # цена есть, но не числовая: намеренный пропуск

                old_price_el = c.select_one(".product-card__old-price")
                old_price = parse_price(old_price_el.text) if old_price_el else 0

                # Изображение
                img_el = c.select_one("img.object-contain, .carousel img, img")
                image_url = ""
                if img_el:
                    src = img_el.get("src") or img_el.get("data-src") or ""
                    if src:
                        image_url = src if src.startswith("http") else f"{self.base_url}{src}"

                # ID товара
                slug = rel_link.split("/p/")[-1].strip("/")
                # Раньше slug обрезался до 40 символов — длинные адреса с общим началом совпадали
                pid = slug_id("alser", slug)

                products.append({
                    "shop": self.SHOP_NAME,
                    "id": pid,
                    "title": title,
                    "category": category_name,
                    "url": full_link,
                    "image_url": image_url,
                    "price": current_price,
                    "old_price_on_site": old_price if old_price > current_price else 0,
                    "city": "Астана"
                })

            if not products and unparsed:
                raise ValueError("Карточки Alser найдены, но ни один товар не удалось разобрать")

        except UnconfirmedEnd:
            raise
        except Exception as e:
            print(f"[{self.SHOP_NAME}] Ошибка страницы {url}: {e}")
            raise

        return ScanResult(products,
                          complete=self._is_last_page(soup, category_url, page_num,
                                                      len(self._observed.get(category_url, ()))),
                          unparsed=unparsed)

    def _is_last_page(self, soup, category_url: str, page_num: int, observed_total: int) -> bool:
        """Конец — когда увидено столько уникальных карточек, сколько заявил счётчик магазина (A02).
        Число страниц из размера первой страницы не выводится: страницы бывают разного размера.
        Счётчик берётся со страницы, а если на ней его нет — с первой страницы категории.
        Пока счётчика нет вовсе — конец не подтверждён."""
        total = self._declared_total(soup)
        if page_num == 1:
            self._declared.pop(category_url, None)
        if total is not None and total > 0:
            self._declared[category_url] = total
        total = self._declared.get(category_url)
        return total is not None and observed_total >= total

    CITY_LABEL = "Астана"

    def _check_city(self, soup, html: str = "") -> None:
        """Выдача должна быть для Астаны: город берётся из состояния страницы, а не из заголовка.

        H1 — SEO-текст и пишет «в Алматы» и на астанинской выдаче. Город выбора — currentCity в
        состоянии Nuxt (его же показывает переключатель городов). Если его нет — подпись кнопки
        переключателя, совпадающая с названием города из списка городов страницы. Если не найдено
        ни то ни другое, город определяется адресом /astana/ (прежнее поведение). Другой город —
        ошибка: товары не подписываются Астаной (аудит 6.4.14).
        """
        city = self.selected_city(soup, html)
        if city and city != self.CITY_LABEL:
            raise RuntimeError(f"Alser отдал выдачу для города «{city}», а не «{self.CITY_LABEL}»")

    @staticmethod
    def selected_city(soup, html: str = "") -> Optional[str]:
        if html:
            ref = re.search(r"currentCity:([A-Za-z_$][\w$]*)", html)
            if ref:
                var = re.escape(ref.group(1))
                named = (re.search(r"(?<![\w$.])" + var + r'\.name="([^"]+)"', html)
                         or re.search(r"(?<![\w$.])" + var + r'=\{name:"([^"]+)"', html))
                if named:
                    return named.group(1).strip()
        cities = set(re.findall(r'name:"([^"]+)",keyword:"[a-z\-]+"', html)) | {"Астана", "Алматы"}
        for button in soup.select('button[name="main_button"][aria-label]'):
            label = " ".join((button.get("aria-label") or "").split())
            if label in cities:
                return label
        return None

    @staticmethod
    def _declared_total(soup) -> Optional[int]:
        """Число товаров по версии самого магазина: <p class="… total-text">10 товаров</p>."""
        counter = soup.select_one("p.total-text, .total-text")
        if not counter:
            return None
        match = re.search(r"(\d[\d\s\u00a0]*)", counter.get_text())
        if not match:
            return None
        return int(re.sub(r"[^0-9]", "", match.group(1)))
