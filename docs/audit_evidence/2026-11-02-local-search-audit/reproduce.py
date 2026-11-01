"""Read-only audit of app code. All mutations use disposable SQLite databases.
Run: venv/bin/python docs/audit_evidence/2026-11-02-local-search-audit/reproduce.py
No network, real AI, Telegram, deployment or working DB writes.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests import test_support  # sets temporary DATA_DIR before application imports
import asyncio
import contextlib
import io
import json
import sqlite3
import tempfile
from unittest.mock import AsyncMock, patch
from tests.search.test_local_catalog_growth import ConfirmedShortNameTests, seed, PLAN
from domain.models import CanonicalProduct, Offer
from domain.query_parser import QueryParser
from repositories import ProductRepository, OfferRepository
from repositories.catalog_expansion_repo import CatalogExpansionRepository
from repositories.search_repo import SearchRepository
import local_search


def emit(case, **facts):
    print(json.dumps(dict(case=case, **facts), ensure_ascii=False), flush=True)


def fixture():
    case = ConfirmedShortNameTests('test_short_and_full_query_include_same_confirmed_models')
    case.setUp()
    return case


def watches_and_stale():
    case = fixture()
    try:
        for query in ['тахеометр', 'геодезический тахеометр']:
            result = case.search(query)
            matches = [i['product']['id'] for i in result['items'] if local_search.matches_watch(
                result['search_help']['watch_target'], dict(product_id=i['product']['id'],current_price=i['min_price']))]
            emit('watch_disagrees', query=query, search=result['total_products'], watch_matches=matches)
        conn = sqlite3.connect(case.path)
        conn.execute("UPDATE canonical_products SET title='Отражатель для прибора Audit' WHERE id='topcon'")
        conn.commit();conn.close()
        result = case.search('тахеометр')
        emit('stale_confirmation', outcome=result['outcome'],
             wrong_product=[i['product']['title'] for i in result['items'] if i['product']['id']=='topcon'])
    finally:case.tearDown()


def embedded_city():
    case=fixture()
    try:
        query='тахеометр в Шымкенте'
        result=case.search(query,city=None)
        conn=sqlite3.connect(case.path)
        request=CatalogExpansionRepository(conn).get(result['search_help']['expansion']['id'])
        emit('embedded_city_lost', parsed_city=QueryParser.parse(query).detected_city,
             search_city=result['parsed_query']['detected_city'], queued_city=request['city'])
        conn.close()
        args=dict(query='геодезический тахеометр',city='Астана',condition=None,channel_type=None,
                  seller_type=None,min_price=None,max_price=None,sort_by='relevance',limit=20)
        degraded=local_search.search_sync(args,{'status':'unavailable'},{})
        emit('ai_unavailable_loses_confirmations', normal=case.search(args['query'])['total_products'],
             without_ai=degraded['total_products'])
    finally:case.tearDown()


def inferred_attribute():
    with tempfile.TemporaryDirectory() as tmp:
        path=tmp+'/audit.db';conn=sqlite3.connect(path);seed(conn)
        title='RoadGrip Audit с шипами'
        ProductRepository(conn).save_or_update(CanonicalProduct(id='ice',canonical_key='ice',title=title,category='Шины и диски'))
        OfferRepository(conn).save_or_update(Offer(id='ice',product_id='ice',seller_id='seller',channel_id='channel',
            external_sku='ice',url='https://example.com/ice',price=10000,city='Астана'))
        repo=CatalogExpansionRepository(conn);task=repo.propose('зимняя резина','Астана',{**PLAN,'attributes':{}})
        repo.review(task['id'],True)
        repo.add_match(task['id'],'winter');repo.add_match(task['id'],'ice')
        repo.finish(task['id'],'indexed',2,'done',2)
        conn.close()
        args=dict(query='зимняя резина',city='Астана',condition=None,channel_type=None,seller_type=None,
                  min_price=None,max_price=None,sort_by='relevance',limit=20)
        with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
            before=local_search.search_sync(args,{'status':'ok','plan':PLAN},{})
            conn=sqlite3.connect(path);conn.execute("DELETE FROM product_search_attributes WHERE key='season'");conn.commit();conn.close()
            no_facts=local_search.search_sync(args,{'status':'ok','plan':PLAN},{})
        emit('inferred_attribute_hides_verified', with_some_season_facts=before['total_products'],
             without_season_facts=no_facts['total_products'], selected_implicitly=before['selected_attributes'],
             explicit_user_filters={})


async def batch_case(shrinking=False, partial_sources=False):
    with tempfile.TemporaryDirectory() as tmp:
        path=tmp+'/audit.db'
        def connect():return sqlite3.connect(path)
        conn=connect();seed(conn)
        plan=dict(search_query='геодезический тахеометр',category_id='ai_geo',category_name='Геодезическое оборудование',
                  parent_id='tools_repair',fields=[],attributes={},confidence=0.99,clarification='')
        repo=CatalogExpansionRepository(conn);task=repo.propose('геодезический тахеометр','Астана',plan);repo.review(task['id'],True)
        for n in range(50):
            ident=f'audit{n:03}'
            title=f'Audit Model {n:03}' if shrinking else f'Тахеометр Audit Model {n:03}'
            ProductRepository(conn).save_or_update(CanonicalProduct(id=ident,canonical_key=ident,title=title,
                category='Тахеометр' if shrinking else 'Без категории'))
            OfferRepository(conn).save_or_update(Offer(id=ident,product_id=ident,seller_id='seller',channel_id='channel',
                external_sku=ident,url='https://example.com/'+ident,price=10000,city='Астана'))
        conn.commit();conn.close()
        async def live(*args,**kwargs):
            if partial_sources:
                import search_engine
                health=search_engine._live_health.get();health['attempted']+=2;health['failed']+=1
            return []
        with patch('config.get_ai_config',return_value={'local_api_base':'http://mock','local_model':'mock'}), \
             patch('local_ai.pause_reason',return_value=None), \
             patch('local_search._store_queries',AsyncMock(return_value=['геодезический тахеометр','тахеометр'])), \
             patch('search_engine.search_live_stores',AsyncMock(side_effect=live)), \
             patch('ai_service.call_local_api',AsyncMock(return_value={'matches':True,'query_match':True,'confidence':0.99,'attributes':{}})), \
             patch('database.watched_offers',return_value=[]),patch('database.evaluate_watches',return_value=0), \
             patch('local_search.asyncio.sleep',AsyncMock()):
            await local_search.expand_one(connect)
            conn=connect();first=CatalogExpansionRepository(conn).get(task['id']);conn.close()
            await local_search.expand_one(connect)
            conn=connect();last=CatalogExpansionRepository(conn).get(task['id']);conn.close()
        emit('shrinking_batch_skips' if shrinking else 'source_failure_forgotten',
             first={k:first[k] for k in ['status','found','scan_offset','scan_total','message']},
             final={k:last[k] for k in ['status','found','scan_offset','scan_total','message']})


def rejection_race():
    with tempfile.TemporaryDirectory() as tmp:
        path=tmp+'/audit.db';c1=sqlite3.connect(path);seed(c1)
        r1=CatalogExpansionRepository(c1);task=r1.propose('зимняя резина','Астана',PLAN);r1.review(task['id'],True)
        c2=sqlite3.connect(path);r2=CatalogExpansionRepository(c2)
        original_get=r1.get;claimed=[]
        def read_then_claim(ident):
            row=original_get(ident)
            if not claimed:claimed.append(r2.claim())
            return row
        with patch.object(r1,'get',side_effect=read_then_claim):r1.review(task['id'],False)
        after_reject=original_get(task['id'])['status']
        r2.finish(task['id'],'indexed',1,'worker finished',1)
        emit('rejection_race', worker_claimed=claimed[0]['status'], after_reject=after_reject,
             after_old_worker_finish=original_get(task['id'])['status'])
        c1.close();c2.close()


watches_and_stale()
embedded_city()
inferred_attribute()
asyncio.run(batch_case(shrinking=True))
asyncio.run(batch_case(partial_sources=True))
rejection_race()


def retained_description():
    import os
    import database
    with tempfile.TemporaryDirectory() as tmp,patch.object(database,'DB_PATH',Path(tmp)/'prices.db'), \
         patch('config.DATA_DIR',Path(tmp)),patch.dict(os.environ,{'DUAL_WRITE_V2':'1'}):
        with contextlib.redirect_stdout(io.StringIO()):
            database.init_db()
        marker='AUDIT_SOURCE_DESCRIPTION ' * 20
        database.save_or_update_products_batch([dict(id='audit-source',title='Тахеометр Audit TC42',
            canonical_key='audit:content:tc42',category='Геодезическое оборудование',shop='Kaspi',
            city='Астана',url='https://example.com/audit',price=10000,description=marker)])
        with database.get_connection() as conn:
            legacy=conn.execute("SELECT description FROM products WHERE id='audit-source'").fetchone()[0]
            canonical=conn.execute('SELECT count(*) FROM canonical_products WHERE description=?',(marker,)).fetchone()[0]
        backup=database.backup_database('audit')
        copy=sqlite3.connect('file:'+str(backup)+'?mode=ro&immutable=1',uri=True)
        backup_count=copy.execute('SELECT count(*) FROM canonical_products WHERE description=?',(marker,)).fetchone()[0];copy.close()
        emit('source_description_retained',legacy_exact=legacy==marker,canonical_exact_rows=canonical,backup_exact_rows=backup_count)

retained_description()
