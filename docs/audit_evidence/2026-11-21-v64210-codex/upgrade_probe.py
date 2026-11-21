"""Offline Marwin upgrade probes; all DB writes go to temporary test_support DATA_DIR."""
from tests import test_support  # noqa: F401
import json
from unittest.mock import Mock
import database
from offer_identity import assign_offer_ids
from scrapers.marwin import MarwinScraper
from tests.scrapers.test_city_confirmation_r1a import _retired_after


def item(raw_id, price=9000):
    parsed, bad = MarwinScraper()._extract_item({'id': raw_id, 'sku': 'stable-business-sku',
        'name': 'Synthetic migration book', 'slug': 'synthetic-book',
        'price': {'current': price}, 'in_stock': True}, 'Synthetic')
    assert not bad
    assign_offer_ids([parsed])
    return parsed


database.init_db()
old_url = 'https://www.marwin.kz/books/'
new_url = 'https://www.marwin.kz/knigi-32627'
# IDs are illustrative; this is an explicit changed-ID scenario, not a verified live product mapping.
old = item(3495958, 10000)
new = item(221538, 9000)
database.save_or_update_product(old)
database.reconcile_source('marwin', old_url, [old], complete=True)
database.save_user_favorite(1, old)
database.save_or_update_product(new)
retired = database.reconcile_source('marwin', new_url, [new], complete=True)
with database.get_connection() as conn:
    rows = [dict(r) for r in conn.execute('SELECT id, current_price, first_seen_price, is_active FROM products ORDER BY id')]
    obs = [list(r) for r in conn.execute('SELECT product_id, count(*) FROM price_observations GROUP BY product_id ORDER BY product_id')]
    favorites = [r[0] for r in conn.execute('SELECT item_id FROM user_favorites')]
    sources = [list(r) for r in conn.execute('SELECT product_id, source_url, active FROM product_sources ORDER BY product_id')]
assert len(rows) == 2 and all(r['is_active'] for r in rows)
assert favorites == [old['id']]
assert new['id'] != old['id']
print('ID_AND_SOURCE_UPGRADE=' + json.dumps({'products':rows,'history':obs,'favorite_targets':favorites,
    'retired':retired,'source_links':sources}, ensure_ascii=False))

# Isolate source URL migration: even unchanged IDs of missing products remain active on old URLs.
missing = dict(old, id='marwin_missing@unknown')
database.save_or_update_product(missing)
database.reconcile_source('marwin', old_url, [old, missing], complete=True)
database.reconcile_source('marwin', new_url, [old], complete=True)
with database.get_connection() as conn:
    active = conn.execute('SELECT is_active FROM products WHERE id=?',(missing['id'],)).fetchone()[0]
assert active == 1
print('UNCHANGED_ID_OLD_SOURCE_STILL_ACTIVE=' + str(active))

# A malformed pagination.pages must never prove a complete result and retire absent products.
for pages in (True, 1.9):
    s = MarwinScraper()
    s._slug_to_id['knigi-32627'] = 33
    raw = {'id':101,'name':'Synthetic','slug':'synthetic-101','in_stock':True,'price':{'current':9990}}
    s.session = Mock(get=Mock(return_value=Mock(status_code=200,json=Mock(return_value={
        'items':[raw],'pagination':{'pages':pages,'page':1,'total':1000,'limit':20}}))))
    result = s._scrape_sync('Synthetic', new_url, max_pages=1)
    retired, active, assessment = _retired_after(result, ['marwin_101@unknown','marwin_102@unknown'], 'marwin', new_url)
    assert result.complete and retired == ['marwin_102@unknown']
    print('INVALID_PAGINATION=' + json.dumps({'pages':pages,'complete':result.complete,'unparsed':result.unparsed,
        'may_retire':assessment['may_retire'],'retired':retired}))
