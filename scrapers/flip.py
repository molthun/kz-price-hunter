"""Public Flip catalog; no inferred city or installment prices."""
from urllib.parse import urljoin, urlparse, parse_qs
from bs4 import BeautifulSoup
from scrapers import http as requests
from scrapers.base import PagedScraper, ScanResult, parse_price


class FlipScraper(PagedScraper):
    SHOP_NAME = 'Flip.kz'
    PAGE_DELAY_SECONDS = 1.0

    def _fetch_page(self, category_name, category_url, page):
        response = requests.get(self.page_url(category_url, page), timeout=30)
        if response.status_code != 200:
            raise RuntimeError(f'HTTP {response.status_code}')
        return self.parse_page(response.text, category_name, category_url, page)

    @classmethod
    def parse_page(cls, html, category_name, category_url, page):
        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select('.new-product a.product')
        if not cards:
            raise ValueError('Карточки Flip не найдены: полнота не подтверждена')
        products = []
        for card in cards:
            data = card.select_one('.product-data[data-available="1"]')
            if not data:
                continue
            title = data.select_one('.title')
            price = data.select_one('.price > span:not(.old)')
            href = urljoin('https://www.flip.kz', card.get('href', ''))
            pid = parse_qs(urlparse(href).query).get('prod', [''])[0]
            if not title or not price or not pid.isdigit():
                continue
            amount = parse_price(price.get_text())
            if amount <= 0:
                continue
            old = data.select_one('.price > span.old')
            image = card.select_one('img.image')
            details = data.select_one('.description')
            name = title.get_text(' ', strip=True)
            if details:
                name += ' ' + details.get_text(' ', strip=True)
            products.append(dict(
                id=f'flip_{pid}', shop=cls.SHOP_NAME, title=name, category=category_name,
                url=href, image_url=urljoin(href, image.get('src', '')) if image else '',
                price=amount, old_price_on_site=parse_price(old.get_text()) if old else 0,
                city='Регион не подтверждён',
            ))
        subsection = parse_qs(urlparse(category_url).query).get('subsection')
        has_next = False
        for link in soup.select('a[href]'):
            query = parse_qs(urlparse(link['href']).query)
            if query.get('subsection') == subsection and query.get('page', [''])[0].isdigit():
                if int(query['page'][0]) > page:
                    has_next = True
        return ScanResult(products, complete=bool(products) and not has_next)
