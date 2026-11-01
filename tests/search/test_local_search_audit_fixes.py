"""Regression scenarios from LSA-01–09; disposable DBs, no network or notifications."""
from tests import test_support  # noqa: F401
import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, patch

from tests.search.test_local_catalog_growth import ConfirmedShortNameTests as _ConfirmedShortNameTests, seed, PLAN
from repositories import ProductRepository, OfferRepository
from repositories.catalog_expansion_repo import CatalogExpansionRepository
from repositories.search_repo import SearchRepository
from domain.models import CanonicalProduct, Offer
import local_search


class SemanticRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = _ConfirmedShortNameTests('test_short_and_full_query_include_same_confirmed_models')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_watch_uses_confirmations_including_arrivals_after_subscription(self):
        for query in ('тахеометр', 'геодезический тахеометр'):
            data = self.fixture.search(query)
            target = data['search_help']['watch_target']
            for item in data['items']:
                self.assertTrue(local_search.matches_watch(target, dict(product_id=item['product']['id'],price=item['min_price'])))
            with sqlite3.connect(self.fixture.path) as conn:
                repo = CatalogExpansionRepository(conn)
                conn.execute("DELETE FROM catalog_expansion_matches WHERE product_id='geomax'")
                conn.commit()
                self.assertFalse(local_search.matches_watch(target, dict(product_id='geomax',price=20000)))
                repo.add_match(self.fixture.task['id'],'geomax')
            self.assertTrue(local_search.matches_watch(target, dict(product_id='geomax',price=20000)))
            restricted = self.fixture.search(query,max_price=15000)['search_help']['watch_target']
            self.assertFalse(local_search.matches_watch(restricted,dict(product_id='geomax',price=20000)))

    def test_confirmation_expires_on_identity_changes(self):
        for column, value in [('title','Отражатель Audit'),('model','DIFFERENT'),('canonical_key','changed:key'),('item_kind','accessory')]:
            with self.subTest(column=column), sqlite3.connect(self.fixture.path) as conn:
                before = conn.execute(f'SELECT {column} FROM canonical_products WHERE id=?',('topcon',)).fetchone()[0]
                conn.execute(f'UPDATE canonical_products SET {column}=? WHERE id=?',(value,'topcon'));conn.commit()
                self.assertNotIn('topcon',CatalogExpansionRepository(conn).verified_products('тахеометр','ai_geo'))
                conn.execute(f'UPDATE canonical_products SET {column}=? WHERE id=?',(before,'topcon'));conn.commit()

    def test_cached_exact_intent_survives_ai_unavailable_and_paused(self):
        args=dict(query='геодезический тахеометр',city='Астана',limit=20)
        for status in ('unavailable','disabled'):
            for query in ('геодезический тахеометр','тахеометр'):
                result=local_search.search_sync({**args,'query':query},{'status':status},{})
                self.assertEqual(result['total_products'],3)
                self.assertTrue(result['local_ai']['cached_plan'])
        self.assertEqual(local_search.search_sync({**args,'max_price':15000},{'status':'unavailable'}, {})['total_products'],1)

    def test_embedded_city_used_for_queue_and_explicit_city_wins(self):
        data=self.fixture.search('тахеометр в Шымкенте',city=None)
        with sqlite3.connect(self.fixture.path) as conn:
            task=CatalogExpansionRepository(conn).get(data['search_help']['expansion']['id'])
            self.assertEqual(task['city'],'Шымкент')
        data=self.fixture.search('тахеометр в Шымкенте',city='Астана')
        self.assertEqual(data['parsed_query']['detected_city'],'Астана')

    def test_legacy_confirmations_rechecked_once_on_schema_upgrade(self):
        from repositories import init_schema_v2
        with sqlite3.connect(self.fixture.path) as conn:
            conn.execute('ALTER TABLE catalog_expansion_matches DROP COLUMN source_identity')
            conn.execute('ALTER TABLE catalog_expansions DROP COLUMN generation')
            conn.execute('ALTER TABLE catalog_expansions DROP COLUMN source_health')
            conn.commit()
            init_schema_v2(conn)
            repo=CatalogExpansionRepository(conn)
            self.assertEqual(repo.get(self.fixture.task['id'])['status'],'approved')
            self.assertEqual(repo.verified_products('геодезический тахеометр'),[])
            repo.add_match(self.fixture.task['id'],'topcon')
            init_schema_v2(conn)
            self.assertEqual(repo.verified_products('геодезический тахеометр'),['topcon'])


class AttributeRegressionTests(unittest.TestCase):
    def test_inferred_filter_does_not_hide_verified_unknown_but_explicit_remains_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db';conn=sqlite3.connect(path);seed(conn)
            title='RoadGrip с шипами'
            ProductRepository(conn).save_or_update(CanonicalProduct(id='ice',canonical_key='ice',title=title,category='Шины и диски'))
            OfferRepository(conn).save_or_update(Offer(id='ice',product_id='ice',seller_id='seller',channel_id='channel',
                external_sku='ice',url='https://example.com/ice',price=10000,city='Астана'))
            repo=CatalogExpansionRepository(conn);task=repo.propose('зимняя резина','Астана',PLAN);repo.review(task['id'],True)
            repo.add_match(task['id'],'winter');repo.add_match(task['id'],'ice');conn.close()
            args=dict(query='зимняя резина',city='Астана',limit=20)
            with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
                data=local_search.search_sync(args,{'status':'ok','plan':PLAN},{})
                self.assertEqual(data['total_products'],2)
                self.assertEqual(sum(v['count'] for f in data['attribute_facets'] if f['key']=='season' for v in f['values']),1)
                for item in data['items']:
                    self.assertTrue(local_search.matches_watch(data['search_help']['watch_target'],dict(product_id=item['product']['id'],price=10000)))
                strict=local_search.search_sync(args,{'status':'ok','plan':PLAN},{'season':'Зимние'})
                self.assertEqual([i['product']['id'] for i in strict['items']],['winter'])


class QueueRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, shrinking=False, partial=False, count=50):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn)
            plan=dict(search_query='геодезический тахеометр',category_id='ai_geo',category_name='Геодезическое оборудование',
                parent_id='tools_repair',fields=[],attributes={},confidence=0.99,clarification='')
            repo=CatalogExpansionRepository(conn);task=repo.propose('геодезический тахеометр','Астана',plan);repo.review(task['id'],True)
            for n in range(count):
                pid=f'audit{n:03}'
                ProductRepository(conn).save_or_update(CanonicalProduct(id=pid,canonical_key=pid,title=(f'Audit Model {n}' if shrinking else f'Тахеометр Audit Model {n}'),category='Тахеометр' if shrinking else 'Без категории'))
                OfferRepository(conn).save_or_update(Offer(id=pid,product_id=pid,seller_id='seller',channel_id='channel',external_sku=pid,url='https://example.com/'+pid,price=10000,city='Астана'))
            conn.commit();conn.close()
            async def live(*args,**kwargs):
                if partial:
                    import search_engine
                    health=search_engine._live_health.get();health['attempted']+=2;health['failed']+=1
                return []
            model=AsyncMock(return_value={'matches':True,'query_match':True,'confidence':0.99,'attributes':{}})
            with patch('config.get_ai_config',return_value={'local_api_base':'http://mock','local_model':'mock'}), \
                 patch('local_ai.pause_reason',return_value=None),patch('local_search._store_queries',AsyncMock(return_value=['геодезический тахеометр','тахеометр'])), \
                 patch('search_engine.search_live_stores',AsyncMock(side_effect=live)),patch('ai_service.call_local_api',model), \
                 patch('database.watched_offers',return_value=[]),patch('database.evaluate_watches',return_value=0),patch('local_search.asyncio.sleep',AsyncMock()):
                await local_search.expand_one(connect)
                with connect() as conn:
                    first=CatalogExpansionRepository(conn).get(task['id'])
                self.assertEqual((first['scan_offset'], first['scan_total']), (30,count))
                for _ in range((count-1)//30):
                    await local_search.expand_one(connect)
                with connect() as conn:
                    final=CatalogExpansionRepository(conn).get(task['id'])
                    self.assertEqual((final['status'],final['scan_offset'],final['scan_total'],final['found']),('indexed',count,count,count))
                    self.assertEqual(len(CatalogExpansionRepository(conn).verified_products(task['query'])),count)
                self.assertEqual(model.await_count,count)
                if partial:
                    self.assertIn('неполная',final['message'])
                    self.assertGreater(json.loads(final['source_health'])['failed'],0)

    async def test_mutating_category_does_not_skip_snapshot_tail(self):await self.scenario(shrinking=True)
    async def test_source_failure_survives_multiple_batches(self):await self.scenario(partial=True)

    async def test_snapshot_is_not_limited_to_public_page_cap(self):await self.scenario(count=150)

    async def test_recheck_includes_classified_models_without_type_word(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn)
            plan=dict(search_query='геодезический тахеометр',category_id='ai_geo',category_name='Геодезическое оборудование',parent_id='tools_repair',fields=[],attributes={},confidence=0.99,clarification='')
            repo=CatalogExpansionRepository(conn);task=repo.propose('геодезический тахеометр','Астана',plan);repo.review(task['id'],True)
            ProductRepository(conn).save_or_update(CanonicalProduct(id='top',canonical_key='top',title='TopCon TC42',category=plan['category_name']))
            OfferRepository(conn).save_or_update(Offer(id='top',product_id='top',seller_id='seller',channel_id='channel',external_sku='top',url='https://example.com/top',price=10000,city='Астана'))
            conn.commit();conn.close()
            with patch('config.get_ai_config',return_value={'local_api_base':'http://mock','local_model':'mock'}),patch('local_ai.pause_reason',return_value=None), \
                 patch('local_search._store_queries',AsyncMock(return_value=['геодезический тахеометр','тахеометр'])),patch('search_engine.search_live_stores',AsyncMock(return_value=[])), \
                 patch('ai_service.call_local_api',AsyncMock(return_value={'matches':True,'query_match':True,'confidence':0.99,'attributes':{}})), \
                 patch('database.watched_offers',return_value=[]),patch('database.evaluate_watches',return_value=0),patch('local_search.asyncio.sleep',AsyncMock()):
                for _ in range(2):
                    await local_search.expand_one(connect)
                    with connect() as conn:
                        repo=CatalogExpansionRepository(conn)
                        self.assertEqual(repo.verified_products('тахеометр','ai_geo'),['top'])
                        self.assertEqual(repo.saved_plan('тахеометр')['category_id'],'ai_geo')
                        repo.review(task['id'],True)

    def test_expired_worker_cannot_write_or_finish_after_new_claim_or_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db';c1=sqlite3.connect(path);seed(c1);r1=CatalogExpansionRepository(c1)
            task=r1.propose('зимняя резина','Астана',PLAN);r1.review(task['id'],True);old=r1.claim()
            c2=sqlite3.connect(path);r2=CatalogExpansionRepository(c2)
            c2.execute("UPDATE catalog_expansions SET updated_at='2000-01-01' WHERE id=?",(task['id'],));c2.commit()
            new=r2.claim();self.assertNotEqual(old['generation'],new['generation'])
            with r1.lease(old) as active:self.assertFalse(active)
            r1.finish(task['id'],'indexed',9,'old worker',9,generation=old['generation'])
            self.assertEqual(r2.get(task['id'])['found'],0)
            r2.finish(task['id'],'failed',0,'retry',generation=new['generation']);r2.review(task['id'],False)
            r1.finish(task['id'],'indexed',9,'old worker',9,generation=old['generation'])
            self.assertEqual(r2.get(task['id'])['status'],'rejected')
            self.assertIsNone(r1.claim())
            c1.close();c2.close()


class ReviewSerializationTests(unittest.TestCase):
    def test_claim_waits_until_atomic_rejection_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            conn=sqlite3.connect(path);seed(conn);repo=CatalogExpansionRepository(conn)
            task=repo.propose('зимняя резина','Астана',PLAN);repo.review(task['id'],True)
            started=threading.Event();claims=[];errors=[]
            def claim():
                try:
                    with sqlite3.connect(path,timeout=3) as other:
                        started.set();claims.append(CatalogExpansionRepository(other).claim())
                except BaseException as exc:errors.append(type(exc).__name__)
            thread=threading.Thread(target=claim)
            original=repo.get
            triggered=False
            def after_read(ident):
                nonlocal triggered
                row=original(ident)
                if not triggered:
                    triggered=True;thread.start();self.assertTrue(started.wait(1))
                return row
            with patch.object(repo,'get',side_effect=after_read):repo.review(task['id'],False)
            thread.join(4)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(claims,[None])
            self.assertEqual(repo.get(task['id'])['status'],'rejected');conn.close()
