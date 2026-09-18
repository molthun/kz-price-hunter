"""Halyk Market catalog scraper via internal search-api/products JSON endpoint."""
import re
from urllib.parse import urlparse
from scrapers import http as requests
from scrapers.base import PagedScraper, ScanResult, price_value

# Known category IDs on Halyk Market
CATEGORY_ID_MAP = {
    'smartfony': '20001',
    'telefoni': '10001',
    'noutbuki': '20088',
    'noutbuki-i-aksessuary': '32769',
    'televizori': '20029',
    'televizory': '20029',
    'naushniki': '20009',
    'naushniki-i-garnitury': '20009',
    'plansheti': '20087',
    'smart-chasi': '20002',
    'smart-chasi-i-brasleti': '20002',
    'igrovie-pristavki': '21076',
    'monitori': '20037',
    'roboty-pylesosy': '98776',
    'pilesosi': '20167',
    'holodilniki': '20181',
}

SHOP_ID = "693ff081028570920fd8a6b971eb5e"
DEFAULT_LOCATION = "-2"  # Almaty
PAGE_SIZE = 24


class HalykScraper(PagedScraper):
    SHOP_NAME = 'Halyk Market'
    PAGE_DELAY_SECONDS = 0.5

    def __init__(self):
        super().__init__()
        self._session = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Referer': 'https://halykmarket.kz/',
            })
            # Инициализация начальной сессии и cookies магазина
            try:
                self._session.get('https://halykmarket.kz/', impersonate='chrome120', timeout=15)
            except Exception:
                pass
        return self._session

    def resolve_category_id(self, category_url: str) -> str:
        """Resolve category numeric ID from URL slug or category mapping."""
        path = urlparse(category_url).path.strip('/')
        slug = path.split('/')[-1] if path else ''
        
        # If slug is already numeric
        if slug.isdigit():
            return slug
            
        # Check known map
        if slug in CATEGORY_ID_MAP:
            return CATEGORY_ID_MAP[slug]

        # Try to resolve dynamically from category page
        try:
            session = self._get_session()
            r = session.get(category_url, impersonate='chrome120', timeout=15)
            if r.status_code == 200:
                # Pattern: categories\u002F(\d+)\u002F{slug}
                m = re.findall(rf'categories\\u002F(\d+)\\u002F[^\"]*?{slug}', r.text)
                if m:
                    CATEGORY_ID_MAP[slug] = m[0]
                    return m[0]
                m2 = re.findall(rf'\"(\d+)\",\"[^\"]+\",\"[^\"]*\",\"\\u002F{slug}\"', r.text)
                if m2:
                    CATEGORY_ID_MAP[slug] = m2[0]
                    return m2[0]
        except Exception:
            pass

        return slug

    def _fetch_page(self, category_name: str, category_url: str, page: int):
        cat_id = self.resolve_category_id(category_url)
        session = self._get_session()
        
        api_url = "https://halykmarket.kz/search-api/products"
        params = {
            'shop_id': SHOP_ID,
            'limit': PAGE_SIZE,
            'page': page,
            'locations': DEFAULT_LOCATION,
            'categories': cat_id,
            'sort_by': 'popular',
            'sort_dir': 'desc',
            'extended': 'true',
        }
        
        resp = session.get(api_url, params=params, impersonate='chrome120', timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"Halyk Market search-api returned HTTP {resp.status_code}")
            
        return self.parse_response(resp.json(), category_name, page)

    @classmethod
    def parse_response(cls, data: dict, category_name: str, page: int) -> ScanResult:
        if not isinstance(data, dict) or not isinstance(data.get('products'), list):
            raise ValueError("Неожиданная структура ответа Halyk Market")
        raw_products = data['products']
        total_products = data.get('products_total')
        
        products = []
        for p in raw_products:
            pid = p.get('id')
            name = (p.get('name') or '').strip()
            price = price_value(p.get('price'))
            if not pid or not name or price <= 0:
                continue
                
            old_price = price_value(p.get('oldprice'))
            url_path = p.get('url') or ''
            if url_path.startswith('http'):
                full_url = url_path
            elif url_path.startswith('/'):
                full_url = f"https://halykmarket.kz/category{url_path}"
            else:
                full_url = f"https://halykmarket.kz/category/{url_path}"
                
            image = p.get('picture') or p.get('image') or ''
            
            products.append({
                'id': f'halyk_{pid}',
                'shop': cls.SHOP_NAME,
                'title': name,
                'category': category_name,
                'url': full_url,
                'image_url': image,
                'price': price,
                'old_price_on_site': old_price if old_price > price else 0,
                'city': 'Алматы',
            })

        # Конец подтверждает только валидный products_total: без него короткая/пустая
        # страница может быть сбоем или сменой схемы, а не концом каталога
        total_known = isinstance(total_products, int) and not isinstance(total_products, bool) and total_products >= 0
        complete = total_known and page * PAGE_SIZE >= total_products
        return ScanResult(products, complete=complete)
