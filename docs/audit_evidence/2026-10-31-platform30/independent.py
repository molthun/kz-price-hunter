"""Read-only code audit probes; every database is synthetic and temporary.
Run from the immutable source snapshot with its root on PYTHONPATH.
"""
import dataclasses
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from domain.models import Seller, Channel, CanonicalProduct, Offer
from domain.provenance import build_seller_provenance
from domain.search_core import SearchCore
from domain.scale_bench import get_current_scale_status, ScaleCheckpoint, evaluate_storage_decision
from repositories import init_schema_v2, ProductRepository, SellerRepository, ChannelRepository, OfferRepository, SearchRepository, HistoryRepository, DiscoveryRepository
from domain.discovery import SellerCandidate, CandidateStatus

result = {}
conn = sqlite3.connect(':memory:')
init_schema_v2(conn)
pr, sr, cr, offers = ProductRepository(conn), SellerRepository(conn), ChannelRepository(conn), OfferRepository(conn)
pr.save_or_update(CanonicalProduct(id='p1', canonical_key='probe1', title='Audit Phone 256GB', brand='Audit', category='phones'))
pr.save_or_update(CanonicalProduct(id='p2', canonical_key='probe2', title='Audit Phone 512GB', brand='Audit', category='phones'))
sr.save_or_update(Seller(id='s1', name='Synthetic seller', slug='synthetic-seller'))
for cid, typ in [('direct', 'direct'), ('kaspi', 'kaspi')]:
    cr.save_or_update(Channel(id=cid, seller_id='s1', channel_type=typ, name=cid))
now = dt.datetime.now(dt.timezone.utc)
def offer(oid='offer', pid='p1', cid='direct', condition='NEW', price=100, availability='in_stock'):
    return Offer(id=oid, product_id=pid, seller_id='s1', channel_id=cid, external_sku=oid,
                 url='https://example.invalid/'+oid, price=price, condition=condition,
                 availability=availability, observed_at=now.isoformat())

# Same offer changes all mutable dimensions after recording. Its old point must stay put.
for writer in ('canonical_single', 'canonical_batch', 'existing_offer_writer'):
    conn.execute('DELETE FROM offer_price_history')
    offers.save_or_update(offer())
    history = HistoryRepository(conn)
    if writer == 'canonical_single':
        history.record_point('offer', 100, observed_at='2026-10-30T10:00:00Z')
    elif writer == 'canonical_batch':
        history.record_batch([dict(offer_id='offer', price=100, observed_at='2026-10-30T10:00:00Z')])
    else:
        offers.add_price_observation('offer', 100, observed_at='2026-10-30T10:00:00Z')
    snapshot = dict(conn.execute('SELECT * FROM offer_price_history').fetchone())
    offers.save_or_update(offer(pid='p2', cid='kaspi', condition='USED', price=200))
    old_report = history.get_product_price_history('p1')
    new_report = history.get_product_price_history('p2')
    result[writer] = dict(snapshot=snapshot,
                         old_product=dataclasses.asdict(old_report),
                         new_product=dataclasses.asdict(new_report))

# An out-of-stock cheap price must not become the seller's current sale price.
conn.execute('DELETE FROM offer_price_history')
conn.execute('DELETE FROM offers')
offers.save_or_update(offer('available', price=100000))
offers.save_or_update(offer('unavailable', cid='kaspi', price=1000, availability='out_of_stock'))
core = SearchCore(SearchRepository(conn))
response = core.search('Audit Phone 256GB')
result['group_with_unavailable_price'] = response.items[0].to_dict()
conn.execute("UPDATE offers SET availability='out_of_stock'")
result['group_without_available_offers'] = core.search('Audit Phone 256GB').items[0].to_dict()

discovery = DiscoveryRepository(conn)
t1 = discovery.create_or_update_task('Synthetic laptop', city='Алматы', search_count=31, success_rate=.25)
t2 = discovery.create_or_update_task('Synthetic laptop', city='Алматы', search_count=40, success_rate=.1)
discovery.update_task_status(t1.id, 'RUNNING')
candidate = SellerCandidate(id='candidate', name='Synthetic candidate', source_type='website')
discovery.save_candidate(candidate)
discovery.update_candidate_status(candidate.id, CandidateStatus.REVIEW_REQUIRED)
result['discovery_contract'] = dict(first=dataclasses.asdict(discovery.get_task_by_id(t1.id)),
    second=dataclasses.asdict(t2), stats=discovery.get_discovery_stats(),
    sql_task_counts=[dict(r) for r in conn.execute('SELECT status,COUNT(*) n FROM discovery_tasks GROUP BY status')],
    sql_candidate_counts=[dict(r) for r in conn.execute('SELECT status,COUNT(*) n FROM seller_candidates GROUP BY status')])

empty = sqlite3.connect(':memory:')
result['scale_missing_schema'] = get_current_scale_status(empty)
cp = ScaleCheckpoint('unmeasured', 100000, 0, 0, 0, 0, float('nan'), 0, 0, 0, 0, 0, 0, 0, 0)
result['invalid_measurements_decision'] = evaluate_storage_decision(cp).to_dict()
cp.search_p95_ms = 80
result['80ms_decision'] = evaluate_storage_decision(cp).to_dict()
result['future_activity'] = build_seller_provenance(Seller(id='s', name='Synthetic', slug='syn'),
    offers=[{'observed_at': (now+dt.timedelta(days=365)).isoformat()}], now=now).to_dict()

# Brand filtering must not accept another brand merely mentioned in the title.
pr.save_or_update(CanonicalProduct(id='thirdparty', canonical_key='thirdparty',
    title='Acme adapter compatible with Apple iPhone', brand='Acme', category='accessories'))
offers.save_or_update(offer('adapter', pid='thirdparty'))
result['brand_apple'] = [i.product.brand for i in core.search('', brand='Apple').items]
def json_safe(v):
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)
    if isinstance(v, dict):
        return {k: json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [json_safe(x) for x in v]
    return v
print(json.dumps(json_safe(result), ensure_ascii=False, indent=2, allow_nan=False))
