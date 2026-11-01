from tests import test_support  # noqa: F401; isolate DATA_DIR before app imports

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from domain.models import CanonicalProduct, Seller, Channel, Offer
from domain.search_core import SearchCore
from repositories import init_schema_v2, ProductRepository, SellerRepository, ChannelRepository, OfferRepository
from repositories.category_repo import CategoryRepository
from repositories.catalog_expansion_repo import CatalogExpansionRepository
from repositories.search_repo import SearchRepository
import local_search
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


PLAN = dict(search_query='зимн', category_id='tires', category_name='Шины и диски',
            parent_id='auto', fields=[dict(key='season', label='Сезон', type='select')],
            attributes={'season':'Зимние'}, confidence=0.98, clarification='')


def seed(conn):
    init_schema_v2(conn)
    CategoryRepository(conn).seed_defaults_if_empty()
    SellerRepository(conn).save_or_update(Seller(id='seller', name='Shop', slug='shop'))
    ChannelRepository(conn).save_or_update(Channel(id='channel', seller_id='seller', channel_type='direct', name='Shop'))
    for ident, title, city, season in [
        ('winter','Шина зимняя Audit 205/55 R16','Астана','Зимние'),
        ('summer','Шина летняя Audit 205/55 R16','Астана','Летние'),
        ('almaty','Шина зимняя Audit 205/55 R17','Алматы','Зимние')]:
        ProductRepository(conn).save_or_update(CanonicalProduct(id=ident,canonical_key=ident,title=title,category='Шины и диски'))
        OfferRepository(conn).save_or_update(Offer(id=ident, product_id=ident,seller_id='seller',channel_id='channel',
                                                  external_sku=ident,url='https://example.com/'+ident,price=10000,city=city))
        CatalogExpansionRepository(conn).save_attributes(ident,title,{'season':{'value':season,'evidence':'зимняя' if season=='Зимние' else 'летняя'}})
    conn.commit()


class GrowthRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        seed(self.conn)
        SearchRepository._cat_cache = {}
        self.repo = CatalogExpansionRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_propose_dedupe_and_privacy(self):
        first = self.repo.propose('Зимняя резина','Астана',PLAN)
        self.repo.propose('зимняя   резина','Астана',PLAN)
        self.assertEqual(self.repo.get(first['id'])['demand'],2)
        self.assertIsNone(self.repo.propose('купить телефон +77771234567','Астана',PLAN))

    def test_approve_schema_restart_and_reject(self):
        task = self.repo.propose('зимняя резина','Астана',PLAN)
        self.repo.review(task['id'],True)
        self.assertEqual(CategoryRepository(self.conn).get_by_id('tires').attributes_schema['season']['label'],'Сезон')
        CategoryRepository(self.conn).sync_master_taxonomy()
        self.assertIn('season',CategoryRepository(self.conn).get_by_id('tires').attributes_schema)
        claimed = self.repo.claim()
        self.assertEqual(claimed['status'],'checking')
        self.assertIsNone(self.repo.claim())
        with self.assertRaises(ValueError): self.repo.review(task['id'],False)
        self.repo.finish(task['id'],'failed',0,'retry')
        self.repo.review(task['id'],False)
        self.assertEqual(self.repo.get(task['id'])['status'],'rejected')

    def test_filters_counts_facets_city_before_pagination(self):
        repo = SearchRepository(self.conn); repo.attribute_filters={'season':'Зимние'}
        core = SearchCore(repo)
        result = core.search('',category='tires',city='Астана',limit=1)
        self.assertEqual(result.total_products,1)
        self.assertEqual([i.product.id for i in result.items],['winter'])
        facets=repo.attribute_facets({'query':'','category':'tires','city':'Астана'}, {'season':{'label':'Сезон'}})
        self.assertEqual({v['value']:v['count'] for v in facets[0]['values']},{'Зимние':1,'Летние':1})

    def test_evidence_and_stale_title(self):
        self.repo.save_attributes('summer','Шина летняя Audit 205/55 R16',{'season':{'value':'Зимние','evidence':'зимняя'}})
        repo=SearchRepository(self.conn);repo.attribute_filters={'season':'Зимние'}
        self.conn.execute("UPDATE canonical_products SET title='Шина летняя Audit' WHERE id='winter'")
        result=SearchCore(repo).search('',category='tires',city='Астана')
        self.assertEqual(result.total_products,0)

    def test_new_category_appears_and_protects_pinned_cross_branch(self):
        plan={**PLAN,'category_id':'ai_rope','category_name':'Альпинистские верёвки','parent_id':'sport'}
        task=self.repo.propose('альпинистская верёвка','Все',plan)
        self.repo.review(task['id'],True)
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='rope',canonical_key='rope',title='Верёвка Audit',category='Без категории'))
        self.assertTrue(self.repo.classify('rope','ai_rope',0.99))
        self.assertFalse(self.repo.classify('winter','ai_rope',0.99))
        self.assertEqual(SearchRepository(self.conn)._category_filter('sport')[1].count('Альпинистские верёвки'),1)
        self.assertFalse(self.repo.classify('rope','tires',0.99))

    def test_forged_model_values_and_model_number_loss(self):
        cats=local_search.catalog_context(self.conn)
        plan=local_search.clean_plan({**PLAN,'search_query':'iphone','attributes':{}},'iphone 14 256',cats)
        self.assertEqual(plan['search_query'],'iphone 14 256')
        self.assertIsNone(local_search.clean_plan({'category_id':'bad!', 'category_name':''},'query',cats))
        deduped=local_search.clean_plan({**PLAN,'category_id':'unknown'},'зимняя резина',cats)
        self.assertEqual(deduped['category_id'],'tires')


class LocalModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        local_search._plans.clear(); local_search._failures.clear()
        self.cfg=patch('config.get_ai_config',return_value={'local_api_base':'http://local/v1','local_model':'test','ai_search_enabled':True})
        self.cfg.start()
        self.env=patch.dict(os.environ,{'LOCAL_SEARCH_ENABLED':'1'});self.env.start()
        self.pause=patch('local_ai.pause_reason',return_value=None);self.pause.start()

    async def asyncTearDown(self):
        self.cfg.stop();self.env.stop();self.pause.stop()

    async def test_every_query_cache_singleflight_no_paid_provider(self):
        model=AsyncMock(return_value=PLAN)
        cats=[{'id':'tires','name':'Шины и диски'}]
        with patch('ai_service.call_local_api',model):
            a,b=await asyncio.gather(local_search.understand('зимняя резина',cats),local_search.understand('зимняя резина',cats))
            self.assertEqual(model.await_count,1)
            cached=await local_search.understand('зимняя резина',cats)
            self.assertTrue(cached['cached'])
            await local_search.understand('iphone',cats)
            self.assertEqual(model.await_count,2)

    async def test_failure_circuit_and_pause(self):
        with patch('ai_service.call_local_api',AsyncMock(return_value=None)) as model:
            self.assertEqual((await local_search.understand('запрос',[]))['status'],'unavailable')
            await local_search.understand('другой запрос',[])
            self.assertEqual(model.await_count,1)
        with patch('local_ai.pause_reason',return_value='game'),patch('ai_service.call_local_api',AsyncMock()) as model:
            await local_search.understand('iphone',[])
            model.assert_not_awaited()

    async def test_worker_real_repository_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/test.db'
            def connect():
                return sqlite3.connect(path)
            c=connect();seed(c)
            task=CatalogExpansionRepository(c).propose('зимняя резина','Астана',PLAN)
            CatalogExpansionRepository(c).review(task['id'],True);c.close()
            with patch('database.evaluate_watches',return_value=0),patch('database.watched_offers',return_value=[]),patch('search_engine.search_live_stores',AsyncMock(return_value=[])),patch('ai_service.call_local_api',AsyncMock(return_value={
                'matches':True,'confidence':0.99,'attributes':{'season':{'value':'Зимние','evidence':'зимняя'}}})):
                self.assertTrue(await local_search.expand_one(connect))
            c=connect();self.assertEqual(CatalogExpansionRepository(c).get(task['id'])['status'],'indexed');c.close()

    async def test_search_sync_preserves_price_and_city_and_graceful_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/test.db';c=sqlite3.connect(path);seed(c);c.close()
            args=dict(query='зимняя резина до 9000',city='Астана',condition=None,channel_type=None,seller_type=None,
                min_price=None,max_price=None,sort_by='relevance',limit=20)
            with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
                data=local_search.search_sync(args,{'status':'ok','plan':PLAN},{})
                self.assertEqual(data['total_products'],0)
                self.assertIsNotNone(data['search_help']['expansion'])
                args['query']='Audit';data=local_search.search_sync(args,{'status':'unavailable'},{})
                self.assertEqual(data['total_products'],2)
                self.assertEqual(data['local_ai']['status'],'unavailable')

    async def test_structured_watch_matches_same_constraints(self):
        import watches
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/test.db';c=sqlite3.connect(path);seed(c);c.close()
            target=local_search.WATCH_PREFIX+json.dumps({'query':'зимн','category':'tires',
                'attributes':{'season':'Зимние'},'max_price':12000})
            watch={'kind':'search','target':target,'city':'Астана'}
            normalized=watches.normalize({**watch,'condition':'new_offer','repeat':False,'title':'Зимняя резина'})
            self.assertEqual(normalized['target'],target)
            with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
                self.assertTrue(watches.matches(watch,{'product_id':'winter','city':'Астана','current_price':10000}))
                self.assertFalse(watches.matches(watch,{'product_id':'summer','city':'Астана','current_price':10000}))
                self.assertFalse(watches.matches(watch,{'product_id':'almaty','city':'Алматы','current_price':10000}))
                self.assertFalse(watches.matches(watch,{'product_id':'winter','city':'Астана','current_price':15000}))


class GrowthHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=self.tmp.name+'/test.db';c=sqlite3.connect(self.path);seed(c);c.close()
        import web.server as srv
        self.db=patch('database.get_connection',side_effect=lambda:sqlite3.connect(self.path));self.db.start()
        self.env=patch.dict(os.environ,{'LOCAL_SEARCH_ENABLED':'1'});self.env.start()
        self.model=patch('local_search.understand',AsyncMock(return_value={'status':'ok','plan':PLAN}));self.model.start()
        self.admin=patch('auth.is_admin',side_effect=lambda user:user.get('is_admin',False));self.admin.start()
        @web.middleware
        async def auth(request,handler):
            if request.headers.get('X-Test-Role'):
                request['user']={'id':1,'is_admin':request.headers['X-Test-Role']=='admin'}
            return await handler(request)
        app=web.Application(middlewares=[auth])
        app.add_routes([web.get('/api/v2/search',srv.search_v2_handler),
            web.get('/api/admin/catalog-expansions',srv.catalog_expansions_handler),
            web.post('/api/admin/catalog-expansions/{id}/review',srv.review_catalog_expansion_handler)])
        self.client=TestClient(TestServer(app));await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.db.stop();self.env.stop();self.model.stop();self.admin.stop();self.tmp.cleanup()

    async def test_http_filters_and_admin_permissions(self):
        res=await self.client.get('/api/v2/search',params={'q':'зимняя резина','city':'Астана'})
        self.assertEqual(res.status,200);data=await res.json()
        self.assertEqual(data['total_products'],1)
        self.assertEqual(data['items'][0]['product']['id'],'winter')
        self.assertEqual((await self.client.get('/api/admin/catalog-expansions')).status,401)
        self.assertEqual((await self.client.get('/api/admin/catalog-expansions',headers={'X-Test-Role':'user'})).status,403)
        res=await self.client.get('/api/admin/catalog-expansions',headers={'X-Test-Role':'admin'})
        self.assertEqual(res.status,200)
        task=(await res.json())['items'][0]
        res=await self.client.post('/api/admin/catalog-expansions/'+task['id']+'/review',
                                  headers={'X-Test-Role':'admin'},json={'decision':'approve'})
        self.assertEqual(res.status,200)
        self.assertEqual((await res.json())['item']['status'],'approved')

    async def test_http_invalid_filters(self):
        for attrs in ['[]','{"bad.key":"value"}','{"key":[]}']:
            res=await self.client.get('/api/v2/search',params={'attributes':attrs})
            self.assertEqual(res.status,400)


class DurableGrowthWatchTests(unittest.TestCase):
    def test_persist_match_outbox_and_user_isolation(self):
        import database
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp,patch.object(database,'DB_PATH',Path(tmp)/'prices.db'),patch('config.DATA_DIR',Path(tmp)):
            database.init_db()
            database.upsert_telegram_user({'id':701,'first_name':'Test'})
            database.upsert_telegram_user({'id':702,'first_name':'Other'})
            with database.get_connection() as conn:
                seed(conn)
            target=local_search.WATCH_PREFIX+json.dumps({'query':'зимн','category':'tires',
                'attributes':{'season':'Зимние'},'max_price':12000,'brand':None,'model':None,'kind':None})
            payload=dict(kind='search',target=target,title='Зимняя резина',condition='new_offer',city='Астана',
                         repeat=False,quiet_from='00:00',quiet_to='00:00')
            watch=database.create_watch(701,payload)
            self.assertEqual(database.create_watch(701,payload)['id'],watch['id'])
            self.assertEqual(database.list_watches(702),[])
            rows=database.watched_offers(['winter'])
            self.assertTrue(rows)
            self.assertEqual(database.evaluate_watches(rows),1)
            self.assertEqual(database.evaluate_watches(rows),0)
            self.assertEqual(database.list_watches(701)[0]['target'],target)


if __name__=='__main__':
    unittest.main()
