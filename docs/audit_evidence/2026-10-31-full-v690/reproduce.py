"""Independent audit probes. Run with snapshot cwd/PYTHONPATH and temporary DATA_DIR.
Assertions describe the observed defects, not the desired acceptance behavior.
No network, credentials, working DB, or model requests.
"""
import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock, patch

from repositories import init_schema_v2
from repositories.candidate_repo import CandidateRepository
from repositories.product_repo import ProductRepository
from repositories.offer_repo import OfferRepository
from repositories.seller_repo import SellerRepository
from repositories.channel_repo import ChannelRepository
from repositories.search_repo import SearchRepository
from repositories.social_source_repo import SocialSourceRepository
from domain.models import Seller, Channel, Offer, Condition
from domain.discovery import SellerCandidate, CandidateStatus, DiscoverySource
from domain.social_content import ContentObservation, ExtractedOfferCandidate
from domain.social_connector import FetchedPost, SocialSource, posts_to_observations
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import VisionExtractionEngine, VisionCandidate
from domain.search_core import SearchCore
from social_ingest import ingest_source, ingest_source_async


def db():
    conn = sqlite3.connect(':memory:')
    init_schema_v2(conn)
    return conn


def observation(post='1', **kw):
    return ContentObservation(post_id=post, raw_metadata={
        'platform': 'instagram', 'handle': 'audit_shop',
        'post_url': 'https://www.instagram.com/p/AUDITPOST/'}, **kw)


def candidate(price=100000, city='Алматы', condition=Condition.NEW):
    return ExtractedOfferCandidate(raw_title='Apple iPhone 15 128GB', price=price,
                                   city=city, condition=condition)


results = {}


async def media_gap():
    c = db()
    src = SocialSourceRepository(c).add_source('telegram', 'audit_photo')
    post = FetchedPost(post_id='1', text='Прайс на картинке',
                       media_urls=['https://cdn.example.invalid/audit.jpg'])
    connector = type('FakeConnector', (), {'fetch': lambda self, handle: [post]})()
    with patch('ai_router.run', new_callable=AsyncMock) as model:
        r = await ingest_source_async(src, connector=connector, conn=c, with_vision=True)
    assert r.status == 'OK' and r.offers == 0 and model.await_count == 0
    results['media_gap'] = {'status': r.status, 'offers': r.offers,
                             'model_calls': model.await_count}


def permalink():
    c = db()
    o = SocialOfferPipeline(c).process_candidates(observation(), [candidate()])[0]
    assert o.url == ''
    results['permalink'] = {'expected': 'https://www.instagram.com/p/AUDITPOST/', 'actual': o.url}


def identity_dimensions():
    c = db(); p = SocialOfferPipeline(c)
    first = p.process_candidates(observation('1'), [candidate()])[0]
    second = p.process_candidates(observation('2'), [candidate(70000, 'Астана', Condition.USED)])[0]
    rows = [dict(x) for x in c.execute('SELECT id,city,condition,price FROM offers')]
    assert first.id == second.id and len(rows) == 1
    results['identity_dimensions'] = rows


def replay_order():
    c = db(); p = SocialOfferPipeline(c)
    p.process_candidates(observation('new', observed_at='2026-10-31T10:00:00+00:00'), [candidate(100000)])
    p.process_candidates(observation('old', observed_at='2026-10-01T10:00:00+00:00'), [candidate(150000)])
    row = dict(c.execute('SELECT price,observed_at FROM offers').fetchone())
    assert row['price'] == 150000 and '10-01' in row['observed_at']
    results['replay_order'] = row
    live = db()
    source = SocialSourceRepository(live).add_source('telegram', 'audit_replay')
    posts = [FetchedPost(post_id='new', text='Apple iPhone 15 128GB 100000 ₸', published_at='2026-10-31T10:00:00+00:00'),
             FetchedPost(post_id='old', text='Apple iPhone 15 128GB 150000 ₸', published_at='2026-10-01T10:00:00+00:00')]
    connector = type('FakeConnector', (), {'fetch': lambda self, handle: posts})()
    r = ingest_source(source, connector=connector, conn=live)
    stored = dict(live.execute('SELECT price,published_at FROM offers').fetchone())
    assert r.status == 'OK' and stored['price'] == 150000 and stored['published_at'] is None
    results['replay_newest_first_ingest'] = {'status':r.status, **stored}


def platform_identity():
    c = db(); p = SocialOfferPipeline(c)
    a = observation('1')
    b = ContentObservation(post_id='2', raw_metadata={'platform': 'telegram', 'handle': 'audit_shop'})
    x = p.process_candidates(a, [candidate()])[0]
    y = p.process_candidates(b, [candidate()])[0]
    assert x.seller_id == y.seller_id
    results['platform_identity'] = {'seller_ids': [x.seller_id, y.seller_id]}


def history_gap():
    c = db(); p = SocialOfferPipeline(c)
    p.process_candidates(observation(), [candidate(100000)])
    p.process_candidates(observation(), [candidate(90000)])
    count = c.execute('SELECT COUNT(*) FROM offer_price_history').fetchone()[0]
    assert count == 0
    results['history_gap'] = {'observations': 2, 'history_rows': count}


def profile_terminal():
    c = db(); repo = CandidateRepository(c)
    repo.save_candidate(SellerCandidate(id='cand_rejected', name='Audit store',
        source_type=DiscoverySource.WEBSITE, source_url='https://example.invalid',
        status=CandidateStatus.REJECTED))
    after = repo.profile_candidate('cand_rejected')
    assert after.status == CandidateStatus.PROFILED
    results['profile_terminal'] = {'before': 'REJECTED', 'after': after.status}


def approve_profiled():
    c = db(); repo = CandidateRepository(c)
    repo.save_candidate(SellerCandidate(id='cand_profiled', name='Audit store',
        source_type=DiscoverySource.WEBSITE, status=CandidateStatus.PROFILED))
    try:
        repo.approve_onboarding_candidate('cand_profiled')
    except ValueError as e:
        results['approve_profiled'] = str(e)
    else:
        raise AssertionError('Expected invalid transition')


def filter_before_limit():
    c = db()
    for suffix, private, price in [('business', False, 100000), ('private', True, 120000)]:
        SellerRepository(c).save_or_update(Seller(id=suffix, slug=suffix, name=suffix,
            metadata={'is_private': private}))
        ChannelRepository(c).save_or_update(Channel(id=suffix, seller_id=suffix,
            channel_type='classifieds' if private else 'website', name=suffix))
        product = ProductRepository(c).get_or_create_canonical('audit:'+suffix,
            title='Apple iPhone 15 128GB '+suffix, category='electronics', brand='Apple')
        OfferRepository(c).save_or_update(Offer(id=suffix, product_id=product.id,
            seller_id=suffix, channel_id=suffix, external_sku=suffix,
            url='https://example.invalid', price=price))
    core = SearchCore(SearchRepository(c))
    small = core.search('iphone 15', seller_type='private', limit=1, sort_by='price_asc')
    large = core.search('iphone 15', seller_type='private', limit=10, sort_by='price_asc')
    assert len(small.items) == 0 and len(large.items) == 1, (small.to_dict(), large.to_dict())
    results['filter_before_limit'] = {'limit_1': small.outcome, 'limit_10_items': len(large.items)}


def units_lost():
    c = db(); p = SocialOfferPipeline(c)
    vcs = VisionExtractionEngine.extract_from_structured([
        {'title': 'Говядина', 'price': 1000, 'currency': 'KZT', 'confidence': .95,
         'is_unit_price': True, 'unit': 'g', 'quantity': 100},
        {'title': 'Говядина', 'price': 6000, 'currency': 'KZT', 'confidence': .95,
         'is_unit_price': True, 'unit': 'kg', 'quantity': 1}])
    x = p.process_candidates(observation('1'), [vcs[0].to_offer_candidate()])[0]
    y = p.process_candidates(observation('2'), [vcs[1].to_offer_candidate()])[0]
    assert x.product_id == y.product_id and x.id == y.id
    results['units_lost'] = {'same_product': True, 'same_offer': True,
        'raw_payload': y.raw_payload, 'category': ProductRepository(c).get_by_id(y.product_id).category}


def metadata_private():
    c = db()
    SellerRepository(c).save_or_update(Seller(id='meta', slug='meta', name='Иван', metadata={'is_private': True}))
    ChannelRepository(c).save_or_update(Channel(id='meta', seller_id='meta', channel_type='telegram', name='Test'))
    prod = ProductRepository(c).get_or_create_canonical('audit:meta', 'Apple iPhone 15', 'electronics')
    OfferRepository(c).save_or_update(Offer(id='meta', product_id=prod.id, seller_id='meta', channel_id='meta', external_sku='meta', url='https://example.invalid', price=100))
    raw = SearchRepository(c).search_products('iphone 15')[0]['offers'][0]
    assert raw['is_private_seller'] is False
    results['metadata_private'] = {'stored': raw['seller_metadata_json'], 'actual_private': raw['is_private_seller']}


def k2_interop():
    from product_key import build_key
    c = db(); title = 'Apple iPhone 15 128GB'
    key = build_key({'brand': 'Apple', 'model': 'iPhone 15', 'variants': ['128gb']}, title=title)
    assert key
    existing = ProductRepository(c).get_or_create_canonical(key, title, 'smartphones', brand='Apple')
    saved = SocialOfferPipeline(c).process_candidates(observation(), [candidate()])[0]
    actual = ProductRepository(c).get_by_id(saved.product_id)
    assert existing.id != actual.id
    results['k2_interop'] = {'existing_key': existing.canonical_key, 'social_key': actual.canonical_key,
                             'canonical_products': c.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]}


async def invalid_vision_response():
    import ai_router
    cfg = {'enabled': True, 'has_ai': True, 'ai_provider': 'auto', 'has_local': True,
           'local_api_base': 'http://unused.invalid/v1', 'local_model': 'audit',
           'gemini_api_key': 'mock', 'gemini_model': 'audit'}
    with patch('ai_service.call_local_api', new_callable=AsyncMock, return_value={'wrong_schema': True}) as loc, \
         patch('ai_service.call_gemini_api', new_callable=AsyncMock) as gem, \
         patch('ai_router.budget_allows', return_value=True), \
         patch('ai_router.record_usage') as usage, patch('local_ai.pause_reason', return_value=None):
        data = await VisionExtractionEngine.extract_from_image(b'audit-image-content', config=cfg)
    assert data == [] and loc.await_count == 1 and gem.await_count == 0
    missing = VisionExtractionEngine.extract_from_structured([{'title':'Audit product', 'price':1000}])[0]
    assert missing.publishable and missing.confidence == .85
    results['invalid_vision_response'] = {'local_calls':loc.await_count, 'fallback_calls':gem.await_count,
        'items':len(data), 'missing_confidence_published':missing.publishable, 'invented_confidence':missing.confidence}


def candidate_counts():
    c = db(); repo = CandidateRepository(c)
    for suffix in ['one', 'two']:
        repo.save_candidate(SellerCandidate(id='cand_'+suffix, name='Audit '+suffix,
            source_type=DiscoverySource.WEBSITE, domain=suffix+'.invalid'))
    items = repo.find_candidates(domain='one.invalid')
    total = repo.count_candidates()
    assert len(items) == 1 and total == 2
    results['candidate_counts'] = {'filtered_items':len(items), 'api_total':total}


candidate_counts()
asyncio.run(invalid_vision_response())
asyncio.run(media_gap())
for probe in [permalink, identity_dimensions, replay_order, platform_identity,
              history_gap, metadata_private, profile_terminal, approve_profiled, filter_before_limit, units_lost, k2_interop]:
    probe()
print(json.dumps(results, ensure_ascii=False, indent=2))
