"""Forte Market (market.forte.kz) scraper via Algolia search gateway."""
import asyncio
import urllib.parse
from offer_identity import NATIONWIDE
from typing import Any, Dict, List, Optional, Tuple
from scrapers import http as requests
from scrapers.base import PagedScraper, ScanResult, price_value

API_SEARCH_URL = "https://apigw.forte.kz/fm/v1/algolia/search/text"
PAGE_SIZE = 20

# Маппинг городов Казахстана на префиксы кодов локаций в Forte Market (поле Locations.Location.ID)
CITY_LOCATION_MAP = {
    "Астана": ["KZ-AST", "710000000"],
    "Алматы": ["KZ-ALA", "750000000"],
    "Шымкент": ["KZ-SHY", "591010000", "611010000"],
    "Караганда": ["KZ-KAR", "351010000"],
    "Актобе": ["KZ-AKT", "151010000"],
    "Павлодар": ["KZ-PAV", "551010000"],
    "Усть-Каменогорск": ["KZ-VOS", "631010000"],
    "Семей": ["KZ-SEM", "632810000"],
    "Костанай": ["KZ-KUS", "391010000"],
    "Атырау": ["KZ-ATY", "231010000"],
    "Актау": ["KZ-MAN", "471010000"],
    "Тараз": ["KZ-ZHA", "311010000"],
}


def resolve_offer_for_city(hit: Dict[str, Any], city: str = "Астана") -> Tuple[int, str]:
    """Цена и подтверждённое место продажи: цена города, иначе общая по Казахстану.

    Если у товара нет цены для запрошенного города, берётся общая цена (KZ или поле Price)
    с меткой «Казахстан», а не с названием запрошенного города.
    """
    locs = (hit.get("Locations") or {}).get("Location")
    if isinstance(locs, list) and city:
        city_prefixes = CITY_LOCATION_MAP.get(city) or [city]
        for loc in locs:
            lid = str(loc.get("ID") or "")
            for prefix in city_prefixes:
                if prefix in lid:
                    p = price_value(loc.get("Price"))
                    if p > 0:
                        return p, city

        # Fallback: общая цена по KZ
        for loc in locs:
            if loc.get("ID") == "KZ":
                p = price_value(loc.get("Price"))
                if p > 0:
                    return p, NATIONWIDE

    # Главное поле Price — тоже общая цена без привязки к городу
    return price_value(hit.get("Price")), NATIONWIDE


def resolve_price_for_city(hit: Dict[str, Any], city: str = "Астана") -> int:
    """Извлекает цену для указанного города из массива Locations.Location или базового Price."""
    return resolve_offer_for_city(hit, city)[0]


def build_description_from_params(hit: Dict[str, Any]) -> str:
    """Формирует текстовое описание из параметров товара (ParamMap или Param), исключая служебные поля."""
    param_map = hit.get("ParamMap")
    ignored_keys = {"мерчант", "merchant", "seller", "id", "uid", "код"}
    if isinstance(param_map, dict) and param_map:
        lines = []
        for k, v in param_map.items():
            clean_k = k.replace("_", " ").strip()
            if clean_k.lower() in ignored_keys:
                continue
            lines.append(f"• {clean_k}: {v}")
        if lines:
            return "\n".join(lines)

    params = hit.get("Param")
    if isinstance(params, list) and params:
        lines = []
        for p in params:
            if isinstance(p, dict) and p.get("Name") and p.get("Value"):
                name = str(p["Name"]).strip()
                if name.lower() in ignored_keys:
                    continue
                lines.append(f"• {name}: {p['Value']}")
        if lines:
            return "\n".join(lines)

    return ""


class ForteMarketScraper(PagedScraper):
    SHOP_NAME = "Forte Market"
    PAGE_DELAY_SECONDS = 0.4

    def __init__(self, city: str = "Астана"):
        super().__init__()
        self.city = city
        self._session = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Referer": "https://market.forte.kz/",
                "Origin": "https://market.forte.kz",
            })
        return self._session

    def _fetch_page(self, category_name: str, category_url: str, page: int) -> ScanResult:
        """Получает страницу каталога Forte Market через фасетный запрос к Algolia."""
        algolia_page = max(0, page - 1)
        session = self._get_session()

        payload: Dict[str, Any] = {
            "query": "",
            "page": algolia_page,
            "hitsPerPage": PAGE_SIZE,
        }

        # Извлекаем facet из URL или используем URL как facet/query
        parsed = urllib.parse.urlparse(category_url)
        query_params = urllib.parse.parse_qs(parsed.query)

        if "facet" in query_params:
            facet_val = query_params["facet"][0]
            payload["facetFilters"] = [facet_val]
        elif "query" in query_params:
            payload["query"] = query_params["query"][0]
        elif category_url.startswith("CategoryMap."):
            payload["facetFilters"] = [category_url]
        elif "/items" not in category_url and not category_url.startswith("http"):
            payload["query"] = category_url

        resp = session.post(API_SEARCH_URL, json=payload, timeout=15)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Forte Market API returned HTTP {resp.status_code}")

        data = resp.json()
        return self.parse_response(data, category_name, page, city=self.city)

    @classmethod
    def parse_response(
        cls, data: Dict[str, Any], category_name: str, page: int, city: str = "Астана"
    ) -> ScanResult:
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise ValueError("Неожиданная структура ответа Forte Market")
        hits = data["hits"]
        nb_hits = data.get("nbHits")

        products = []
        for hit in hits:
            obj_id = hit.get("objectID") or hit.get("ID")
            name = (hit.get("Name") or "").strip()
            price, offer_city = resolve_offer_for_city(hit, city)

            if not obj_id or not name or price <= 0:
                continue

            url = hit.get("URL") or f"https://market.forte.kz/items/{obj_id}"
            image = hit.get("Picture") or ""
            desc = build_description_from_params(hit)

            product = {
                "id": f"forte_{obj_id}",
                "shop": cls.SHOP_NAME,
                "title": name,
                "category": category_name,
                "url": url,
                "image_url": image,
                "price": price,
                "old_price_on_site": 0,
                "city": offer_city,
            }
            if desc:
                product["description"] = desc

            products.append(product)

        # Конец подтверждает только валидный nbHits (Algolia); без него — partial
        total_known = isinstance(nb_hits, int) and not isinstance(nb_hits, bool) and nb_hits >= 0
        complete = total_known and page * PAGE_SIZE >= nb_hits
        return ScanResult(products, complete=complete)

    async def search_live(self, query: str, city: str = "Астана") -> List[Dict[str, Any]]:
        """Асинхронный быстрый live-поиск в Forte Market."""
        return await asyncio.to_thread(self._search_live_sync, query, city)

    def _search_live_sync(self, query: str, city: str = "Астана") -> List[Dict[str, Any]]:
        """Синхронный запрос к Algolia с поисковой строкой query."""
        session = self._get_session()
        payload = {
            "query": query,
            "page": 0,
            "hitsPerPage": 15,
        }
        try:
            resp = session.post(API_SEARCH_URL, json=payload, timeout=10)
            if resp.status_code not in (200, 201):
                return []
            data = resp.json()
            scan_res = self.parse_response(data, "Электроника", 1, city=city)
            return list(scan_res)
        except Exception as e:
            print(f"[ForteMarket] Ошибка live-поиска '{query}': {e}")
            return []
