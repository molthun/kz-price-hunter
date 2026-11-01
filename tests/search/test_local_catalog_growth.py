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

    def test_model_name_is_not_season_evidence(self):
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='arctic',canonical_key='arctic',
            title='Audit Arctic 205/55 R16 без шипов',category='Шины и диски'))
        self.repo.save_attributes('arctic','Audit Arctic 205/55 R16 без шипов',{
            'season':{'value':'Зимние','evidence':'Arctic'},'studs':{'value':'Нет','evidence':'без шипов'}})
        rows=self.conn.execute("SELECT key,value FROM product_search_attributes WHERE product_id='arctic'").fetchall()
        self.assertEqual([tuple(r) for r in rows],[('studs','Нет')])

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
        model=AsyncMock(return_value={**PLAN,'fields':PLAN['fields']+[
            {'key':'width','label':'Ширина'}, {'key':'diameter','label':'Диаметр'}]})
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

    async def test_refine_broad_category_into_reviewable_new_type(self):
        cats=[{'id':'tools_repair','name':'Инструменты и ремонт'}]
        first={**PLAN,'category_id':'tools_repair','category_name':'Инструменты и ремонт',
               'search_query':'геодезический тахеометр','fields':[],'attributes':{}}
        schema={'new_category_name':'Геодезическое оборудование','parent_id':'tools_repair',
                'search_query':'тахеометр','fields':[{'key':'accuracy','label':'Точность'}]}
        with patch('ai_service.call_local_api',AsyncMock(side_effect=[first,schema])):
            result=await local_search.understand('геодезический тахеометр',cats)
        plan=result['plan']
        self.assertTrue(plan['category_id'].startswith('ai_'))
        self.assertEqual(plan['parent_id'],'tools_repair')
        self.assertEqual(plan['search_query'],'тахеометр')

    async def test_worker_real_repository_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/test.db'
            def connect():
                return sqlite3.connect(path)
            c=connect();seed(c)
            task=CatalogExpansionRepository(c).propose('зимняя резина','Астана',PLAN)
            CatalogExpansionRepository(c).review(task['id'],True);c.close()
            with patch('database.evaluate_watches',return_value=0),patch('database.watched_offers',return_value=[]),patch('search_engine.search_live_stores',AsyncMock(return_value=[])),patch('ai_service.call_local_api',AsyncMock(return_value={
                'matches':True,'query_match':True,'confidence':0.99,'attributes':{'season':{'value':'Зимние','evidence':'зимняя'}}})):
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


class ConfirmedShortNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = self.tmp.name + '/test.db'
        conn = sqlite3.connect(self.path); seed(conn)
        self.plan = dict(search_query='геодезический тахеометр', category_id='ai_geo',
            category_name='Геодезическое оборудование', parent_id='tools_repair',
            fields=[dict(key='accuracy', label='Точность', type='select')], attributes={},
            confidence=0.98, clarification='')
        repo = CatalogExpansionRepository(conn)
        self.task = repo.propose('геодезический тахеометр', 'Астана', self.plan)
        repo.review(self.task['id'], True)
        rows = [('topcon','TopCon TC42 точность 2','Астана',10000),
                ('geomax','GeoMax G90 точность 5','Астана',20000),
                ('foif','Тахеометр FOIF X','Астана',30000),
                ('other_city','TopCon Audit','Алматы',9000),
                ('accessory','Отражатель Audit','Астана',1000)]
        for ident,title,city,price in rows:
            ProductRepository(conn).save_or_update(CanonicalProduct(id=ident,canonical_key=ident,
                title=title,category=self.plan['category_name']))
            OfferRepository(conn).save_or_update(Offer(id=ident,product_id=ident,seller_id='seller',
                channel_id='channel',external_sku=ident,url='https://example.com/'+ident,price=price,city=city))
            if ident != 'accessory':repo.add_match(self.task['id'], ident)
        repo.set_scope(self.task['id'], 'тахеометр', 5)
        repo.finish(self.task['id'], 'indexed', 4, 'done', 5)
        repo.save_attributes('topcon', rows[0][1], {'accuracy':{'value':'2','evidence':'точность 2'}})
        repo.save_attributes('geomax', rows[1][1], {'accuracy':{'value':'5','evidence':'точность 5'}})
        conn.close()
        self.db = patch('database.get_connection', side_effect=lambda:sqlite3.connect(self.path))
        self.db.start()

    def tearDown(self):
        self.db.stop(); self.tmp.cleanup()

    def search(self, query, attrs=None, **filters):
        args=dict(query=query,city='Астана',condition=None,channel_type=None,seller_type=None,
                  min_price=None,max_price=None,sort_by='relevance',limit=20)
        args.update(filters)
        plan={**self.plan,'search_query':query}
        return local_search.search_sync(args,{'status':'ok','plan':plan},attrs or {})

    def test_short_and_full_query_include_same_confirmed_models(self):
        for query in ('тахеометр','геодезический тахеометр'):
            data=self.search(query)
            self.assertEqual(data['total_products'],3)
            self.assertEqual({i['product']['id'] for i in data['items']},{'topcon','geomax','foif'})
            self.assertEqual(data['outcome'],'FOUND')

    def test_city_price_attributes_and_facets_still_apply(self):
        self.assertEqual(self.search('тахеометр',city='Алматы')['total_products'],1)
        self.assertEqual([i['product']['id'] for i in self.search('тахеометр',max_price=15000)['items']],['topcon'])
        data=self.search('тахеометр',{'accuracy':'5'})
        self.assertEqual([i['product']['id'] for i in data['items']],['geomax'])
        self.assertEqual({v['value']:v['count'] for v in data['attribute_facets'][0]['values']},{'2':1,'5':1})

    def test_alias_does_not_expand_models_categories_or_neighbour_types(self):
        conn=sqlite3.connect(self.path);repo=CatalogExpansionRepository(conn)
        self.assertEqual(repo.verified_products('тахеометр','tires'),[])
        self.assertEqual(repo.verified_products('нивелир','ai_geo'),[])
        self.assertEqual(self.search('тахеометр RTS102')['total_products'],0)
        repo.set_scope(self.task['id'],'tc42',5)
        self.assertEqual(repo.verified_products('TC42','ai_geo'),[])
        conn.close()

    def test_rejection_also_removes_short_name_confirmations(self):
        conn=sqlite3.connect(self.path);repo=CatalogExpansionRepository(conn)
        self.assertEqual(len(repo.verified_products('тахеометр','ai_geo')),4)
        repo.review(self.task['id'],False)
        self.assertEqual(repo.verified_products('тахеометр','ai_geo'),[])
        conn.close()
        self.assertEqual(self.search('тахеометр')['total_products'],1)

    def test_store_alternative_does_not_alias_compatibility_or_unrelated_words(self):
        conn=sqlite3.connect(self.path);repo=CatalogExpansionRepository(conn)
        for query in ('штатив для тахеометр','не тахеометр','оптический нивелир'):
            conn.execute('UPDATE catalog_expansions SET query=? WHERE id=?',(query,self.task['id']))
            conn.commit()
            self.assertEqual(repo.verified_products('тахеометр','ai_geo'),[])
        conn.close()


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


class ExpansionScanTests(unittest.IsolatedAsyncioTestCase):
    """Проверка подтверждает товары для запроса, листается порциями и переживает сбои модели."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self.tmp.name + '/test.db'
        conn = self.connect()
        seed(conn)
        ProductRepository(conn).save_or_update(CanonicalProduct(id='ice', canonical_key='ice',
            title='Sailun Ice Blazer 205/55 R16 с шипами', category='Шины и диски'))
        OfferRepository(conn).save_or_update(Offer(id='ice', product_id='ice', seller_id='seller', channel_id='channel',
            external_sku='ice', url='https://example.com/ice', price=10000, city='Астана'))
        self.task = CatalogExpansionRepository(conn).propose('зимняя резина', 'Астана', {**PLAN, 'attributes': {}})
        CatalogExpansionRepository(conn).review(self.task['id'], True)
        conn.commit()
        conn.close()

    def connect(self):
        return sqlite3.connect(self.path)

    def row(self):
        conn = self.connect()
        try:
            return CatalogExpansionRepository(conn).get(self.task['id'])
        finally:
            conn.close()

    async def run_batch(self, model):
        with patch('database.evaluate_watches', return_value=0), patch('database.watched_offers', return_value=[]), \
                patch('search_engine.search_live_stores', AsyncMock(return_value=[])), \
                patch('ai_service.call_local_api', model), patch('local_search.MODEL_RETRY_DELAY_SECONDS', 0), \
                patch('config.get_ai_config', return_value={'local_api_base': 'http://x', 'local_model': 'm'}), \
                patch('local_ai.pause_reason', return_value=None):
            await local_search.expand_one(self.connect)

    @staticmethod
    def answer(prompt, *args, **kwargs):
        winter = 'летняя' not in prompt
        return {'matches': True, 'query_match': winter, 'confidence': 0.99, 'attributes': {}}

    async def test_verified_tire_is_found_without_the_word_in_title(self):
        args = dict(query='зимняя резина', city='Астана', condition=None, channel_type=None, seller_type=None,
                    min_price=None, max_price=None, sort_by='relevance', limit=20)
        plan = {'status': 'ok', 'plan': {**PLAN, 'attributes': {}}}
        with patch('database.get_connection', side_effect=self.connect):
            before = local_search.search_sync(dict(args), plan, {})
            await self.run_batch(AsyncMock(side_effect=self.answer))
            after = local_search.search_sync(dict(args), plan, {})
        self.assertEqual(self.row()['status'], 'indexed')
        self.assertEqual(self.row()['found'], 2)  # летняя подходит к разделу, но не к запросу
        self.assertEqual(self.row()['scan_total'], 3)  # проверен весь небольшой раздел
        self.assertEqual(before['total_products'], 1)
        self.assertEqual(after['total_products'], 2)
        self.assertEqual(after['query'], 'зимняя резина')

    async def test_large_category_is_scanned_by_query_words_only(self):
        model = AsyncMock(side_effect=self.answer)
        with patch('local_search.FULL_SCAN_LIMIT', 1):
            await self.run_batch(model)
        self.assertEqual(self.row()['scan_text'], 'зимн')
        self.assertEqual(model.await_count, 2)  # названия для магазина и один товар

    async def test_model_failure_resumes_from_the_same_product(self):
        answers = [{'queries': ['шины']}, self.answer('')] + [None] * 2
        await self.run_batch(AsyncMock(side_effect=answers))
        row = self.row()
        self.assertEqual((row['status'], row['scan_offset'], row['found'], row['failures']), ('approved', 1, 1, 0))
        self.assertIsNone(row['checked_at'])  # порция — ещё не завершённая проверка
        for attempt in range(3):
            await self.run_batch(AsyncMock(return_value=None))
        row = self.row()
        self.assertEqual((row['status'], row['scan_offset'], row['found']), ('failed', 1, 1))
        self.assertTrue(row['checked_at'])
        conn = self.connect()
        CatalogExpansionRepository(conn).review(row['id'], True)
        conn.close()
        row = self.row()
        self.assertEqual((row['status'], row['scan_offset'], row['found'], row['failures']), ('approved', 1, 1, 0))
        await self.run_batch(AsyncMock(side_effect=self.answer))
        self.assertEqual(self.row()['status'], 'indexed')
        self.assertEqual(self.row()['scan_offset'], 3)

    async def test_store_is_asked_for_the_simple_name_of_the_product(self):
        conn = self.connect()
        plan = {**PLAN, 'search_query': 'геодезический тахеометр', 'category_id': 'ai_geo', 'category_name': 'Геодезия',
                'parent_id': 'tools_repair', 'attributes': {}}
        task = CatalogExpansionRepository(conn).propose('геодезический тахеометр', 'Астана', plan)
        CatalogExpansionRepository(conn).review(self.task['id'], False)
        CatalogExpansionRepository(conn).review(task['id'], True)
        ProductRepository(conn).save_or_update(CanonicalProduct(id='geo', canonical_key='geo',
            title='Тахеометр FOIF RTS102', category='Инструменты и ремонт'))
        OfferRepository(conn).save_or_update(Offer(id='geo', product_id='geo', seller_id='seller', channel_id='channel',
            external_sku='geo', url='https://example.com/geo', price=10000, city='Астана'))
        conn.commit()
        conn.close()
        live = AsyncMock(return_value=[])
        answer = lambda prompt, *a, **k: {'queries': ['Тахеометр']} if 'queries' in prompt and 'query_match' not in prompt else self.answer(prompt)
        with patch('database.evaluate_watches', return_value=0), patch('database.watched_offers', return_value=[]), \
                patch('search_engine.search_live_stores', live), patch('ai_service.call_local_api', AsyncMock(side_effect=answer)), \
                patch('config.get_ai_config', return_value={'local_api_base': 'http://x', 'local_model': 'm'}), \
                patch('local_ai.pause_reason', return_value=None):
            await local_search.expand_one(self.connect)
        self.assertEqual([call.args[0] for call in live.await_args_list], ['геодезический тахеометр', 'тахеометр'])
        conn = self.connect()
        row = CatalogExpansionRepository(conn).get(task['id'])
        conn.close()
        self.assertEqual(row['scan_text'], 'тахеометр')
        self.assertEqual(row['scan_total'], 1)
        self.assertEqual(row['found'], 1)

    async def test_store_queries_fall_back_without_model_and_keep_exact_models(self):
        cfg = {'local_api_base': 'http://x', 'local_model': 'm'}
        with patch('ai_service.call_local_api', AsyncMock(return_value=None)) as model:
            self.assertEqual(await local_search._store_queries('геодезический тахеометр', cfg),
                             ['геодезический тахеометр', 'тахеометр'])
            self.assertEqual(await local_search._store_queries('iPhone 16 Pro', cfg), ['iphone 16 pro'])
        self.assertEqual(model.await_count, 1)

    async def test_reject_removes_verified_products(self):
        await self.run_batch(AsyncMock(side_effect=self.answer))
        conn = self.connect()
        repo = CatalogExpansionRepository(conn)
        self.assertEqual(len(repo.verified_products('зимняя резина')), 2)
        repo.review(self.task['id'], False)
        self.assertEqual(repo.verified_products('зимняя резина'), [])
        conn.close()

    def test_old_table_gets_columns_and_unfinished_scan_restarts(self):
        conn = self.connect()
        for column in ('scan_total', 'scan_text', 'failures'):
            conn.execute(f'ALTER TABLE catalog_expansions DROP COLUMN {column}')
        conn.execute("UPDATE catalog_expansions SET scan_offset=90, found=69")
        conn.commit()
        init_schema_v2(conn)
        row = CatalogExpansionRepository(conn).get(self.task['id'])
        conn.close()
        self.assertEqual((row['scan_offset'], row['found'], row['scan_total']), (0, 0, 0))


if __name__=='__main__':
    unittest.main()
