"""Браузер на настоящих DTO SearchCore (SQLite :memory:, история через HistoryRepository); сеть перехвачена."""
import asyncio, json, sqlite3, sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests import test_support  # изолирует DATA_DIR до импорта продукта
from domain.models import CanonicalProduct, Seller, Channel, Offer, Condition, utc_now_iso
from domain.search_core import SearchCore
from repositories import ProductRepository, SellerRepository, ChannelRepository, OfferRepository, SearchRepository, init_schema_v2
from repositories.history_repo import HistoryRepository
from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent
conn = sqlite3.connect(':memory:'); init_schema_v2(conn)
pr, sr, cr, ore = ProductRepository(conn), SellerRepository(conn), ChannelRepository(conn), OfferRepository(conn)
for i in (1, 2):
    sr.save_or_update(Seller(id=f's{i}', name=f'Seller {i}', slug=f'seller{i}'))
    cr.save_or_update(Channel(id=f'c{i}', seller_id=f's{i}', channel_type='website', name=f'Web {i}'))
now = utc_now_iso()
for i in range(45):
    pid = f'lap{i:02d}'
    pr.save_or_update(CanonicalProduct(id=pid, canonical_key=pid, title=f'Laptop {pid}', brand='Acer', category='laptops' if i < 30 else 'smartphones'))
    ore.save_or_update(Offer(id=f'o{pid}', product_id=pid, seller_id=f's{i%2+1}', channel_id=f'c{i%2+1}', external_sku=pid,
        url=f'https://example.invalid/{pid}', price=1000+i, availability='in_stock', city='Алматы', observed_at=now))
pr.save_or_update(CanonicalProduct(id='known', canonical_key='known', title='Known conditions', category='food'))
ore.save_or_update(Offer(id='ok', product_id='known', seller_id='s1', channel_id='c1', external_sku='ok', url='https://example.invalid/ok',
    price=6000, old_price=8000, availability='in_stock', city='Алматы', delivery_type='courier', pickup_available=True, warranty='Гарантия производителя',
    observed_at=now, raw_payload={'price_basis': {'is_unit_price': True, 'quantity': 0.5, 'unit': 'kg'}}))
ore.save_or_update(Offer(id='ob', product_id='known', seller_id='s2', channel_id='c2', external_sku='ob', url='https://example.invalid/ob',
    price=5000, condition=Condition.OPEN_BOX, availability='in_stock', city='Алматы', observed_at=now))
hist = HistoryRepository(conn)
hist.record_point(offer_id='ok', price=8000, observed_at='2026-10-01T10:00:00+00:00')
hist.record_point(offer_id='ok', price=6000, observed_at=now)
hist.record_point(offer_id='ob', price=5000, observed_at=now)
core = SearchCore(SearchRepository(conn))

def search_json(q):
    one = lambda k: (q.get(k) or [None])[0]
    return core.search(one('q') or '', city=one('city'), category=one('cat'), seller_id=one('seller'), condition=one('condition'),
        min_price=float(one('min_price')) if one('min_price') else None, sort_by=one('sort') or 'relevance',
        limit=int(one('limit') or 20), page=int(one('page') or 1)).to_dict()

async def main():
    html = (ROOT/'web/templates/search_first.html').read_text()
    res = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel='chrome', headless=True)
        for name, w, h, scheme in (('desktop-light',1366,900,'light'),('desktop-dark',1366,900,'dark'),('mobile-light',390,844,'light'),('mobile-dark',390,844,'dark')):
            ctx = await b.new_context(viewport={'width':w,'height':h}, color_scheme=scheme, locale='ru-RU')
            page = await ctx.new_page(); errs = []
            page.on('pageerror', lambda e: errs.append(str(e)))
            async def handler(route):
                u = urlparse(route.request.url)
                if u.hostname != '127.0.0.1': return await route.abort()
                if u.path == '/api/v2/search': return await route.fulfill(json=search_json(parse_qs(u.query)))
                if u.path.startswith('/api/v2/products/'):
                    return await route.fulfill(json=core.get_canonical_product(u.path.rsplit('/',1)[1], city=(parse_qs(u.query).get('city') or [None])[0]))
                if u.path.startswith('/api/'): return await route.fulfill(json={})
                return await route.fulfill(content_type='text/html', body=html)
            await page.route('**/*', handler)
            await page.goto('http://127.0.0.1/search?q=Laptop&city=Алматы')
            await page.locator('.product-card').first.wait_for()
            r = {'count_1': await page.locator('#resultsCountSubtitle').inner_text()}
            await page.screenshot(path=str(OUT/f'search-{name}.png'), full_page=True)
            if w < 1024:
                await page.locator('.mobile-filter-btn').click()
                await page.screenshot(path=str(OUT/f'filters-{name}.png'))
            r['category_facets_ui'] = await page.locator('#filterCategoriesList').text_content()
            r['seller_facets_ui'] = await page.locator('#filterSellersList').text_content()
            await page.fill('#filterMinPrice', '1040')
            await page.locator('#filtersSidebar .btn-apply-filters').click()
            await page.wait_for_function("document.getElementById('resultsCountSubtitle').textContent.includes('5')")
            r['count_after_filter'] = await page.locator('#resultsCountSubtitle').inner_text()
            r['cards_after_filter'] = await page.locator('.product-card').count()
            r['error_visible'] = await page.locator('#resultsEmpty').is_visible()
            await page.goto('http://127.0.0.1/product/known?city=Алматы')
            await page.locator('.history-series').first.wait_for()
            r['offers'] = await page.locator('#offersTableBody').inner_text()
            r['history'] = await page.locator('#detailHistoryContent').inner_text()
            r['history_badge'] = await page.locator('#historyObservationsBadge').inner_text()
            await page.screenshot(path=str(OUT/f'detail-{name}.png'), full_page=True)
            r['page_errors'] = errs
            res[name] = r
            await ctx.close()
        await b.close()
    (OUT/'real-dto.json').write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(json.dumps(res['mobile-dark'], ensure_ascii=False, indent=1))
    print('errors', {k: v['page_errors'] for k, v in res.items()})
asyncio.run(main())
