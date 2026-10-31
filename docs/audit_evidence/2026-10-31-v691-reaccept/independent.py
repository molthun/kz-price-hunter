"""Independent desired-behavior checks for 556ddf3. Temporary data, no external services.
Failures are acceptance findings, not assertions requiring a defect to remain.
"""
import asyncio
import base64
import datetime
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from domain.models import Seller, Channel, Condition
from domain.social_connector import FetchedPost
from domain.social_content import ContentObservation, ExtractedOfferCandidate, ContentType
from domain.social_media import FetchedImage
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import VisionExtractionEngine
from domain.search_core import SearchCore
from repositories import init_schema_v2
from repositories.content_repo import ContentObservationRepository
from repositories.social_source_repo import SocialSourceRepository
from repositories.seller_repo import SellerRepository
from repositories.channel_repo import ChannelRepository
from repositories.product_repo import ProductRepository
from repositories.search_repo import SearchRepository
from repositories.offer_repo import OfferRepository
from social_ingest import ingest_source, ingest_source_async

NOTES = {}
TITLE = 'Apple iPhone 15 128GB'


def conn():
    c = sqlite3.connect(':memory:')
    init_schema_v2(c)
    return c


def candidate(price=100000, city='Алматы', condition=Condition.NEW, title=TITLE):
    return ExtractedOfferCandidate(raw_title=title, price=price, city=city, condition=condition)


def observation(post='1', platform='instagram', handle='audit_shop', **kw):
    return ContentObservation(post_id=post, raw_metadata={'platform':platform,'handle':handle,
        'post_url': 'https://www.instagram.com/p/AUDIT/'}, **kw)


def connector(posts):
    return type('FixtureConnector', (), {'fetch': lambda self, handle: posts})()


class Positive(unittest.IsolatedAsyncioTestCase):
    def test_city_condition_platform_permalink_and_history(self):
        c = conn(); p = SocialOfferPipeline(c)
        first = p.process_candidates(observation('1', published_at='2026-10-30T10:00:00+00:00'), [candidate()])[0]
        second = p.process_candidates(observation('2', published_at='2026-10-31T10:00:00+00:00'),
                                      [candidate(70000,'Астана',Condition.USED)])[0]
        tg = p.process_candidates(observation('2',platform='telegram'), [candidate()])[0]
        self.assertNotEqual(first.id,second.id)
        self.assertNotEqual(first.seller_id,tg.seller_id)
        self.assertEqual(first.url,'https://www.instagram.com/p/AUDIT/')
        self.assertEqual(len(OfferRepository(c).get_price_history(first.id)),1)
        p.process_candidates(observation('1', published_at='2026-10-30T10:00:00+00:00'), [candidate()])
        self.assertEqual(len(OfferRepository(c).get_price_history(first.id)),1)

    def test_distinct_publication_times_newest_wins(self):
        c = conn(); p = SocialOfferPipeline(c)
        p.process_candidates(observation('2',published_at='2026-10-31T10:00:00+00:00'),[candidate(100000)])
        p.process_candidates(observation('1',published_at='2026-10-01T10:00:00+00:00'),[candidate(150000)])
        self.assertEqual(c.execute('SELECT price FROM offers').fetchone()[0],100000)

    def test_confidence_missing_nan_string_and_good(self):
        for bad in [None,float('nan'),'0.99',2]:
            rows=VisionExtractionEngine.extract_from_structured([
                {'title':'Invalid','price':1000,'confidence':bad},
                {'title':'Valid','price':2000,'confidence':.95}])
            self.assertFalse(rows[0].publishable)
            self.assertTrue(rows[1].publishable)

    async def test_schema_fallback(self):
        cfg={'enabled':True,'has_ai':True,'ai_provider':'auto','has_local':True,
             'local_api_base':'http://unused.invalid/v1','local_model':'audit',
             'gemini_api_key':'mock','gemini_model':'audit'}
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value={'wrong_schema':True}), \
             patch('ai_service.call_gemini_api',new_callable=AsyncMock,return_value={'items':[{'title':'Valid','price':2000,'confidence':.95}]}) as gem, \
             patch('ai_router.budget_allows',return_value=True),patch('ai_router.record_usage'),patch('local_ai.pause_reason',return_value=None):
            result=await VisionExtractionEngine.extract_from_image(b'audit-image-content',config=cfg)
        self.assertEqual((len(result),gem.await_count),(1,1))


class Residual(unittest.IsolatedAsyncioTestCase):
    def test_content_is_not_retained(self):
        c=conn()
        src=SocialSourceRepository(c).add_source('telegram','audit_content')
        text=TITLE+' 100000 ₸\nСинтетическое полное описание продавца, не данные сравнения.'
        r=ingest_source(src,connector=connector([FetchedPost(post_id='1',text=text,
             media_urls=['https://cdn.example.invalid/signed.jpg?token=synthetic'],
             published_at='2026-10-31T10:00:00+00:00')]),conn=c)
        row=dict(c.execute('SELECT text,media_urls,expires_at FROM content_observations').fetchone())
        NOTES['storage']={'status':r.status,'full_post_persisted':row['text']==text,
                           'media_url_persisted':'signed.jpg' in row['media_urls'],'expires_at':row['expires_at']}
        ContentObservationRepository(c).purge_expired('2030-01-01T00:00:00+00:00')
        copy=sqlite3.connect(':memory:');c.backup(copy)
        NOTES['storage']['backup_retains_post_after_purge'] = copy.execute('SELECT text FROM content_observations').fetchone()[0] == text
        copy.close()
        self.assertFalse(row['text'],'The original post must not be stored as catalog data')

    def test_base64_image_is_not_retained(self):
        c=conn(); payload=base64.b64encode(b'synthetic-image-content-only').decode()
        o=observation();o.raw_metadata['image_bytes']=payload
        ContentObservationRepository(c).save_observation(o)
        raw=c.execute('SELECT raw_metadata FROM content_observations').fetchone()[0]
        NOTES['storage_base64']={'base64_persisted':payload in raw}
        self.assertNotIn(payload,raw,'Supported base64 input must not be persisted')

    async def test_caption_cannot_publish_an_unqualified_unit_price(self):
        c=conn()
        for handle,price,quantity,unit in [('audit_small',1000,100,'g'),('audit_large',6000,1,'kg')]:
            src=SocialSourceRepository(c).add_source('telegram',handle)
            post=FetchedPost(post_id='1',text=f'Говядина {price} ₸ за {quantity} {unit}',
                 media_urls=['https://cdn.example.invalid/photo.jpg'],published_at='2030-01-01T10:00:00+00:00')
            answer={'result':{'items':[{'title':'Говядина','price':price,'confidence':.95,
                    'is_unit_price':True,'quantity':quantity,'unit':unit}]}}
            with patch('domain.vision_extraction.fetch_image',new_callable=AsyncMock,return_value=FetchedImage(b'audit-image-content')), \
                 patch('ai_router.run',new_callable=AsyncMock,return_value=answer):
                r=await ingest_source_async(src,connector=connector([post]),conn=c,with_vision=True)
                self.assertEqual(r.status,'OK')
        rows=SearchCore(SearchRepository(c)).search('говядина').items
        offers=[o for item in rows for o in item.offers]
        NOTES['caption_units']=[{'price':o.price,'unit':o.unit,'quantity':o.quantity,
                                 'product_id':o.product_id} for o in offers]
        self.assertTrue(all(o.unit for o in offers),'Caption created price offers with no unit basis')

    async def test_wrong_unit_type_falls_back_without_losing_good_item(self):
        cfg={'enabled':True,'has_ai':True,'ai_provider':'auto','has_local':True,
             'local_api_base':'http://unused.invalid/v1','local_model':'audit',
             'gemini_api_key':'mock','gemini_model':'audit'}
        invalid={'items':[{'title':'Malformed','price':1000,'confidence':.95,'unit':['kg']},
                          {'title':'Valid','price':2000,'confidence':.95}]}
        valid={'items':[{'title':'Valid','price':2000,'confidence':.95,'unit':'pcs'}]}
        with patch('ai_service.call_local_api',new_callable=AsyncMock,return_value=invalid), \
             patch('ai_service.call_gemini_api',new_callable=AsyncMock,return_value=valid) as gem, \
             patch('ai_router.budget_allows',return_value=True),patch('ai_router.record_usage'),patch('local_ai.pause_reason',return_value=None):
            try: result=await VisionExtractionEngine.extract_from_image(b'audit-image-content',config=cfg)
            except Exception as exc:
                NOTES['typed_schema']={'exception':type(exc).__name__,'fallback_calls':gem.await_count}
                raise
        self.assertGreaterEqual(len(result),1)

    def test_numeric_post_order_at_same_timestamp(self):
        c=conn();p=SocialOfferPipeline(c)
        for post,price in [('9',150000),('10',100000)]:
            p.process_candidates(observation(post,platform='telegram',published_at='2026-10-31T10:00:00+00:00'),[candidate(price)])
        row=dict(c.execute('SELECT price,raw_payload_json FROM offers').fetchone())
        NOTES['numeric_post_order']={'price':row['price'],'post_id':json.loads(row['raw_payload_json'])['source_post_id']}
        self.assertEqual(row['price'],100000,'Telegram post 10 is later than post 9')

    def test_distinct_handles_of_confirmed_seller_keep_channels(self):
        c=conn();p=SocialOfferPipeline(c)
        SellerRepository(c).save_or_update(Seller(id='confirmed',slug='confirmed',name='Confirmed business'))
        a=p.process_candidates(observation(handle='audit_one',seller_id='confirmed'),[candidate()])[0]
        b=p.process_candidates(observation('2',handle='audit_two',seller_id='confirmed'),[candidate()])[0]
        NOTES['channels']={'first_channel':a.channel_id,'second_channel':b.channel_id,'same_offer':a.id==b.id}
        self.assertNotEqual(a.channel_id,b.channel_id,'Two different handles are two different channels')

    def test_story_recollection_keeps_original_expiry(self):
        c=conn();p=SocialOfferPipeline(c)
        now=datetime.datetime.now(datetime.timezone.utc)
        def at(hours):return (now+datetime.timedelta(hours=hours)).isoformat()
        pub=at(-25)
        old=observation('1',content_type=ContentType.STORY,published_at=pub,observed_at=pub)
        first=p.process_candidates(old,[candidate()])[0]
        new=observation('1',content_type=ContentType.STORY,published_at=pub,observed_at=at(0))
        second=p.process_candidates(new,[candidate()])[0]
        NOTES['story_ttl']={'first_expiry':first.expires_at,'recollected_expiry':second.expires_at,
                            'recollected_availability':second.availability}
        self.assertEqual(second.expires_at,first.expires_at,'Recollection must not give an old Story another 24 hours')


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(__import__(__name__)))
    print('INDEPENDENT_NOTES='+json.dumps(NOTES,ensure_ascii=False,sort_keys=True))
    raise SystemExit(not result.wasSuccessful())
