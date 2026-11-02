from tests import test_support  # noqa: F401
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from repositories import init_schema_v2,ProductRepository,SellerRepository,ChannelRepository,OfferRepository
from repositories.classification_repo import ClassificationRepository,Conflict,source_hash
from repositories.category_repo import CategoryRepository
from domain.models import CanonicalProduct,Seller,Channel,Offer
from catalog_classifier import CatalogClassifier
from domain.feed_categories import apply_feed_categories,apply_section_categories,repair_storage_categories
import product_classification as pc


def seed(conn,title='Оперативная память Kingston DDR5 32GB',category='SSD накопители'):
    init_schema_v2(conn)
    conn.execute('CREATE TABLE IF NOT EXISTS products(id TEXT,shop TEXT,category TEXT,is_active INTEGER)')
    SellerRepository(conn).save_or_update(Seller(id='s',slug='s',name='Shop'))
    ChannelRepository(conn).save_or_update(Channel(id='c',seller_id='s',name='Shop',channel_type='direct'))
    ProductRepository(conn).save_or_update(CanonicalProduct(id='p',canonical_key='key:p',title=title,category=category))
    OfferRepository(conn).save_or_update(Offer(id='off_p',product_id='p',seller_id='s',channel_id='c',external_sku='p',url='https://example.com/p',price=10000,city='Астана'))
    conn.execute("INSERT OR REPLACE INTO products VALUES('p','Белый Ветер','SSD диски',1)")
    conn.execute("INSERT OR REPLACE INTO category_ai_verdicts VALUES('p',? ,?,0.99,'feed',1,'x')",(category,category))
    conn.commit()


def body(item,action='assign',category='ram'):
    return dict(action=action,category_id=category,generation=item['job']['generation'],source_hash=item['source_hash'],edit_token=item['edit_token'])


class TagRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');seed(self.conn);self.addCleanup(self.conn.close)
        self.repo=ClassificationRepository(self.conn)

    def test_manual_tag_changes_only_category_and_survives_all_legacy_writers(self):
        before=tuple(self.conn.execute('SELECT id,canonical_key,title,model,brand,item_kind FROM canonical_products').fetchone())
        with self.conn:item=self.repo.detail('p')
        with self.conn:result=self.repo.act('p',body(item),'v1')
        self.assertTrue(result['tag']['locked']);self.assertEqual(result['tag']['category_id'],'ram')
        apply_feed_categories(self.conn);apply_section_categories(self.conn,CatalogClassifier());repair_storage_categories(self.conn)
        import catalog_ai_sort
        catalog_ai_sort.save_verdict(self.conn,'p','Оперативная память','SSD накопители',.99,'qwen',True)
        ProductRepository(self.conn).save_or_update(CanonicalProduct(id='p',canonical_key='key:p',title=before[2],category='SSD накопители'))
        self.conn.commit()
        self.assertEqual(self.conn.execute('SELECT category FROM canonical_products').fetchone()[0],'Оперативная память')
        self.assertEqual(before,tuple(self.conn.execute('SELECT id,canonical_key,title,model,brand,item_kind FROM canonical_products').fetchone()))
        self.assertEqual(self.conn.execute('SELECT price FROM offers').fetchone()[0],10000)
        self.assertEqual(tuple(self.conn.execute('SELECT model,applied FROM category_ai_verdicts').fetchone()),('classification_tag',1))

    def test_source_pages_include_archived_legacy_fallback_and_deduplicate(self):
        self.conn.execute('ALTER TABLE products ADD COLUMN url TEXT')
        self.conn.execute("UPDATE products SET url='https://shop.example/original',category='Память магазина'")
        self.conn.execute("UPDATE offers SET url='',is_active=0")
        self.conn.commit()
        with self.conn:detail=self.repo.detail('p')
        self.assertEqual(len(detail['sources']),1)
        source=detail['sources'][0]
        self.assertEqual(source['url'],'https://shop.example/original')
        self.assertFalse(source['active']);self.assertEqual(source['section'],'Память магазина')
        self.assertEqual(source['host'],'shop.example')
        self.conn.execute("UPDATE offers SET url='https://shop.example/original#info',is_active=1")
        row=self.conn.execute('SELECT * FROM offers').fetchone()
        columns=[x[1] for x in self.conn.execute('PRAGMA table_info(offers)')]
        duplicate=dict(zip(columns,row));duplicate.update(id='off_other',external_sku='other',city='Алматы',is_active=0)
        self.conn.execute('INSERT INTO offers VALUES('+','.join('?' for _ in columns)+')',list(duplicate.values()))
        self.conn.commit()
        self.assertEqual(len(self.repo.sources('p')),1)
        self.assertTrue(self.repo.sources('p')[0]['active'])

    def test_missing_or_unsafe_source_is_not_invented(self):
        for url in ('javascript:alert(1)','https://user:password@shop.example/p','', 'https://[broken'):
            self.conn.execute('UPDATE offers SET url=?',(url,))
            self.assertEqual(self.repo.sources('p'),[])
        self.conn.execute("UPDATE offers SET url='https://example.com/p',is_active=0")
        self.assertFalse(self.repo.sources('p')[0]['active'])

    def test_source_change_invalidates_editor_and_preserves_manual_tag_for_review(self):
        with self.conn:item=self.repo.detail('p')
        with self.conn:self.repo.act('p',body(item),'v1')
        with self.conn:stale=self.repo.detail('p')
        self.conn.execute("UPDATE canonical_products SET title='SSD Samsung 990' WHERE id='p'");self.conn.commit()
        with self.assertRaises(Conflict),self.conn:self.repo.act('p',body(stale,category='storage'),'v1')
        with self.conn:fresh=self.repo.detail('p')
        self.assertEqual(fresh['job']['status'],'review')
        self.assertTrue(fresh['tag']['locked']);self.assertEqual(fresh['tag']['category_id'],'ram')

    def test_manual_can_win_during_model_call_and_old_worker_cannot_apply(self):
        with self.conn:item=self.repo.detail('p')
        request=self.repo.claim('v1')
        # A worker claim alone does not invalidate the administrator's editor.
        with self.conn:self.repo.act('p',body(item),'v1')
        with self.conn:accepted=self.repo.finish(request,dict(category_id='storage',confidence=.99,reason='old',apply=True),'qwen')
        self.assertFalse(accepted)
        self.assertEqual(self.repo.detail('p')['tag']['category_id'],'ram')
        self.conn.commit()

    def test_changed_source_or_expired_lease_drops_stale_result(self):
        with self.conn:self.repo.touch(['p'])
        first=self.repo.claim('v1')
        self.conn.execute("UPDATE classification_jobs SET lease_until=0 WHERE product_id='p'");self.conn.commit()
        second=self.repo.claim('v1')
        self.assertGreater(second['generation'],first['generation'])
        with self.conn:self.assertFalse(self.repo.finish(first,dict(apply=True,category_id='ram',reason='old'),'qwen'))
        self.conn.execute("UPDATE canonical_products SET title='HDD Seagate 2TB' WHERE id='p'");self.conn.commit()
        with self.conn:self.assertFalse(self.repo.finish(second,dict(apply=True,category_id='ram',reason='old'),'qwen'))
        self.assertEqual(self.repo.job('p')['status'],'queued')
        self.assertIsNone(self.conn.execute('SELECT 1 FROM product_category_tags').fetchone())

    def test_unavailable_retries_with_backoff_and_no_fake_done_or_rename(self):
        with self.conn:self.repo.touch(['p'])
        request=self.repo.claim('v1')
        with self.conn:self.repo.finish(request,None,'qwen')
        job=self.repo.job('p')
        self.assertEqual((job['status'],job['failures']),('queued',1));self.assertGreater(job['next_attempt'],0)
        self.assertIsNone(self.repo.claim('v1'))
        self.assertIsNone(self.conn.execute('SELECT 1 FROM product_category_tags').fetchone())

    def test_policy_refresh_low_confidence_and_manual_unlock(self):
        with self.conn:self.repo.discover('v1')
        req=self.repo.claim('v1')
        with self.conn:self.repo.finish(req,dict(category_id='ram',confidence=.6,reason='Не уверен',apply=False),'qwen')
        self.assertEqual(self.repo.job('p')['status'],'review')
        with self.conn:self.repo.discover('v2') # wraps scanner first
        with self.conn:self.repo.discover('v2')
        self.assertEqual(self.repo.job('p')['status'],'queued')
        with self.conn:item=self.repo.detail('p')
        with self.conn:self.repo.act('p',body(item),'v2')
        with self.conn:item=self.repo.detail('p')
        with self.conn:self.repo.act('p',body(item,'unlock'),'v2')
        req=self.repo.claim('v2')
        with self.conn:self.repo.finish(req,dict(category_id='storage',confidence=.99,reason='Проверено',apply=True),'qwen')
        self.assertEqual(self.repo.detail('p')['tag']['category_id'],'storage');self.conn.commit()
        self.assertGreaterEqual(self.conn.execute('SELECT count(*) FROM classification_history').fetchone()[0],4)

    def test_new_section_and_filters_are_data_and_title_is_unchanged(self):
        title=self.repo.source('p')['title']
        with self.conn:item=self.repo.detail('p')
        request=body(item,'create')
        request['new_category']=dict(name='Промышленная память',parent_id='electronics',fields=['Объём','Интерфейс'])
        with self.conn:result=self.repo.act('p',request,'v1')
        category=CategoryRepository(self.conn).get_by_id(result['tag']['category_id'])
        self.assertEqual(category.name,'Промышленная память');self.assertEqual(len(category.attributes_schema),2)
        self.assertEqual(self.repo.source('p')['title'],title)
        self.assertTrue(result['tag']['locked'])

    def test_price_update_does_not_enqueue_again_but_title_update_does(self):
        with self.conn:self.repo.touch(['p'])
        req=self.repo.claim('v1')
        with self.conn:self.repo.finish(req,dict(category_id='ram',confidence=.99,reason='test',apply=True),'qwen')
        before=self.repo.job('p')
        with patch.dict(os.environ,{'CATALOG_CLASSIFICATION_ENABLED':'1'}):
            ProductRepository(self.conn).save_or_update(CanonicalProduct(id='p',canonical_key='key:p',title=self.repo.source('p')['title'],category='SSD накопители'))
            self.conn.commit()
        self.assertEqual(self.repo.job('p')['generation'],before['generation'])
        with patch.dict(os.environ,{'CATALOG_CLASSIFICATION_ENABLED':'1'}):
            ProductRepository(self.conn).save_or_update(CanonicalProduct(id='p',canonical_key='key:p',title='SSD New Model',category='SSD накопители'))
            self.conn.commit()
        self.assertEqual(self.repo.job('p')['status'],'queued')


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_feed_pin_is_checked_and_manual_retry_really_calls_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn);conn.close()
            answer=AsyncMock(return_value={'category_id':'ram','confidence':.99,'reason':'Это модуль ОЗУ'})
            before=None
            with patch.dict(os.environ,{'CATALOG_CLASSIFICATION_ENABLED':'1'}):
                stats=await pc.tick(connect,answer,'mock',CatalogClassifier(),limit=1,pause=0)
                self.assertEqual(stats['asked'],1)
                with connect() as conn:
                    repo=ClassificationRepository(conn);before=repo.source('p')['title'];item=repo.detail('p')
                self.assertEqual(item['tag']['category_id'],'ram')
                with connect() as conn:ClassificationRepository(conn).act('p',body(item),'v1')
                with connect() as conn:
                    repo=ClassificationRepository(conn);item=repo.detail('p');repo.act('p',body(item,'retry'),'v1')
                await pc.tick(connect,answer,'mock',CatalogClassifier(),limit=1,pause=0)
                self.assertEqual(answer.await_count,2)
                with connect() as conn:
                    item=ClassificationRepository(conn).detail('p')
                    self.assertEqual(item['job']['status'],'review');self.assertTrue(item['tag']['locked'])
                    self.assertEqual(item['title'],before)

    async def test_unclear_article_is_reviewed_not_guessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn,'KF560C30BBEAK2-64');conn.close()
            answer=AsyncMock(return_value={'category_id':None,'confidence':.2,'reason':'Нужна карточка источника','proposed_category_name':'Память для оборудования','proposed_parent_id':'electronics','fields':[{'key':'capacity','label':'Объём'}]})
            await pc.tick(connect,answer,'mock',CatalogClassifier(),limit=1,pause=0)
            with connect() as conn:
                item=ClassificationRepository(conn).detail('p')
                self.assertIsNone(item['tag']);self.assertEqual(item['job']['status'],'review')
                self.assertEqual(item['job']['suggestion']['proposed_category_name'],'Память для оборудования')

    def test_invalid_confidence_or_unknown_ids_never_auto_apply(self):
        cats=[dict(id='ram',name='Оперативная память',parent_id='electronics')]
        for value in (float('nan'),float('inf'),True,-1,2,None):
            self.assertIsNone(pc.decision({'category_id':'ram','confidence':value},cats,'SSD'))
        self.assertFalse(pc.decision({'category_id':'fake','confidence':1},cats,'SSD')['apply'])
        self.assertFalse(pc.decision({'category_id':'ram','confidence':1},cats,'SSD',rule='storage')['apply'])


class ClassificationHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient,TestServer
        import web.server as srv
        self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db'
        conn=sqlite3.connect(self.path);seed(conn);conn.close()
        self.db=patch('database.get_connection',side_effect=lambda:sqlite3.connect(self.path));self.db.start()
        self.admin=patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin')));self.admin.start()
        @web.middleware
        async def auth(request,handler):
            if request.headers.get('X-Test-Role'):request['user']={'id':1,'is_admin':request.headers['X-Test-Role']=='admin'}
            return await handler(request)
        app=web.Application(middlewares=[auth]);app.add_routes([
          web.get('/api/admin/classification',srv.classification_list_handler),
          web.get('/api/admin/classification/{id}',srv.classification_detail_handler),
          web.post('/api/admin/classification/{id}',srv.classification_action_handler)])
        self.client=TestClient(TestServer(app));await self.client.start_server()
        self.headers={'X-Test-Role':'admin'}

    async def asyncTearDown(self):
        await self.client.close();self.db.stop();self.admin.stop();self.tmp.cleanup()

    async def test_admin_only_and_tag_action_preserves_title(self):
        url='/api/admin/classification'
        for suffix in ('','/p'):
            self.assertEqual((await self.client.get(url+suffix)).status,401)
            self.assertEqual((await self.client.get(url+suffix,headers={'X-Test-Role':'user'})).status,403)
        self.assertEqual((await self.client.post(url+'/p',json={})).status,401)
        self.assertEqual((await self.client.post(url+'/p',headers={'X-Test-Role':'user'},json={})).status,403)
        listing=await (await self.client.get(url,params={'status':'all'},headers=self.headers)).json()
        self.assertEqual(listing['items'][0]['id'],'p')
        item=await (await self.client.get(url+'/p',headers=self.headers)).json()
        rejected=await self.client.post(url+'/p',headers=self.headers,json={**body(item),'title':'Rename forbidden'})
        self.assertEqual(rejected.status,400)
        response=await self.client.post(url+'/p',headers=self.headers,json=body(item))
        self.assertEqual(response.status,200)
        result=(await response.json())['item'];self.assertEqual(result['title'],item['title'])
        self.assertEqual(result['tag']['category_id'],'ram');self.assertTrue(result['tag']['locked'])
        stale=await self.client.post(url+'/p',headers=self.headers,json=body(item,category='hdd'))
        self.assertEqual(stale.status,409)
        retry=await self.client.post(url+'/p',headers=self.headers,json=body(result,'retry'))
        self.assertEqual(retry.status,200);self.assertEqual((await retry.json())['item']['job']['status'],'queued')

    async def test_new_section_validation_and_empty_search(self):
        url='/api/admin/classification'
        item=await (await self.client.get(url+'/p',headers=self.headers)).json()
        for cat in ({'name':'<script>','parent_id':'electronics'},{'name':'Новый раздел','parent_id':{}}):
            response=await self.client.post(url+'/p',headers=self.headers,json={**body(item,'create'),'new_category':cat})
            self.assertEqual(response.status,400)
        response=await self.client.post(url+'/p',headers=self.headers,json={**body(item),'category_id':'unknown'})
        self.assertEqual(response.status,400)
        response=await self.client.get(url,params={'status':'all','q':'MISSING'},headers=self.headers)
        self.assertEqual((await response.json())['total'],0)


class IngestionTagTests(unittest.TestCase):
    def test_real_bulk_ingestion_updates_title_and_price_without_overwriting_manual_tag(self):
        from pathlib import Path
        import database
        with tempfile.TemporaryDirectory() as tmp,patch.object(database,'DB_PATH',Path(tmp)/'prices.db'),patch('config.DATA_DIR',Path(tmp)),patch.dict(os.environ,{'CATALOG_CLASSIFICATION_ENABLED':'1','DUAL_WRITE_V2':'1'}):
            database.init_db()
            original=dict(id='tag-source',title='Оперативная память Kingston DDR5 32GB',canonical_key='test:tag-source',category='SSD диски',shop='Kaspi',url='https://example.com/item',price=10000,city='Астана')
            database.save_or_update_products_batch([original])
            with database.get_connection() as conn:
                pid=conn.execute('SELECT id FROM canonical_products').fetchone()[0]
                repo=ClassificationRepository(conn);item=repo.detail(pid)
            with database.get_connection() as conn:ClassificationRepository(conn).act(pid,body(item),'v1')
            with database.get_connection() as conn:
                self.assertEqual(ClassificationRepository(conn).job(pid)['status'],'done')
            database.save_or_update_products_batch([{**original,'price':9000}])
            with database.get_connection() as conn:
                self.assertEqual(ClassificationRepository(conn).job(pid)['status'],'done')
                self.assertEqual(conn.execute('SELECT price FROM offers').fetchone()[0],9000)
            database.save_or_update_products_batch([{**original,'title':'SSD Kingston New Model','price':8000}])
            with database.get_connection() as conn:
                item=ClassificationRepository(conn).detail(pid)
                self.assertEqual(item['title'],'SSD Kingston New Model')
                self.assertEqual(item['tag']['category_id'],'ram');self.assertTrue(item['tag']['locked'])
                self.assertEqual(item['job']['status'],'review')
                self.assertEqual(conn.execute('SELECT price FROM offers').fetchone()[0],8000)


class MoreWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_high_confidence_bare_article_still_needs_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn,'KF560C30BBEAK2-64');conn.close()
            await pc.tick(connect,AsyncMock(return_value={'category_id':'ram','confidence':1,'reason':'Модель памяти'}),'mock',CatalogClassifier(),limit=1,pause=0)
            with connect() as conn:
                item=ClassificationRepository(conn).detail('p')
                self.assertEqual(item['job']['status'],'review');self.assertIsNone(item['tag'])
                self.assertIn('только артикул',item['job']['message'])

    async def test_taxonomy_change_during_call_does_not_commit_stale_proposal(self):
        from domain.category import Category
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn);conn.close()
            async def ask(_):
                with connect() as conn:CategoryRepository(conn).save(Category(id='test_new',name='Другой тип',parent_id='electronics'))
                return {'category_id':'ram','confidence':1,'reason':'Модуль памяти'}
            await pc.tick(connect,ask,'mock',CatalogClassifier(),limit=1,pause=0)
            with connect() as conn:
                self.assertIsNone(conn.execute('SELECT 1 FROM product_category_tags').fetchone())
                self.assertEqual(ClassificationRepository(conn).job('p')['status'],'queued')


class PauseWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_during_call_prevents_application_and_preserves_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=tmp+'/db'
            def connect():return sqlite3.connect(path)
            conn=connect();seed(conn);conn.close();allowed=True
            async def ask(_):
                nonlocal allowed
                allowed=False
                return {'category_id':'ram','confidence':1,'reason':'Это память'}
            stats=await pc.tick(connect,ask,'mock',CatalogClassifier(),limit=2,pause=0,can_ask=lambda:allowed)
            self.assertEqual(stats['applied'],0)
            with connect() as conn:
                self.assertIsNone(conn.execute('SELECT 1 FROM product_category_tags').fetchone())
                self.assertEqual(ClassificationRepository(conn).job('p')['status'],'queued')
                self.assertEqual(ClassificationRepository(conn).job('p')['failures'],0)
                self.assertEqual(conn.execute('SELECT event FROM classification_history ORDER BY id DESC LIMIT 1').fetchone()[0],'paused')


class HistoryAndRetryTests(unittest.TestCase):
    def test_legacy_decision_is_preserved_and_repeat_retry_does_not_restart_job(self):
        with sqlite3.connect(':memory:') as conn:
            seed(conn);repo=ClassificationRepository(conn);item=repo.detail('p');conn.commit()
            with conn:item=repo.act('p',body(item),'v1')
            self.assertIn('legacy',[h['event'] for h in item['history']])
            with conn:queued=repo.act('p',body(item,'retry'),'v1')
            with conn:repeated=repo.act('p',body(queued,'retry'),'v1')
            self.assertEqual(queued['job']['generation'],repeated['job']['generation'])
            self.assertEqual(sum(h['event']=='retry' for h in repeated['history']),1)

    def test_history_expiry_and_id_cap_do_not_remove_current_tag(self):
        with sqlite3.connect(':memory:') as conn:
            seed(conn);repo=ClassificationRepository(conn);item=repo.detail('p');conn.commit()
            with conn:repo.act('p',body(item),'v1')
            conn.execute("UPDATE classification_history SET created_at=0")
            conn.execute("INSERT INTO classification_history(id,product_id,source_hash,event,created_at) VALUES(200001,'p','hash','review',0)")
            with conn:repo._history('p',repo.source('p')['signature'],'review','SSD',reason='current')
            self.assertEqual(conn.execute('SELECT count(*) FROM classification_history').fetchone()[0],1)
            self.assertIsNotNone(conn.execute('SELECT 1 FROM product_category_tags').fetchone())
