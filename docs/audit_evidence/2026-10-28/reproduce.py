"""Audit-only reproductions. All databases and TCP peers are temporary and local."""
import asyncio, json, os, sys, tempfile, sqlite3, threading, time
from pathlib import Path
from unittest.mock import patch, AsyncMock
sys.path.insert(0,sys.argv[1])
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph-audit28-repro-')
for key in ('TELEGRAM_BOT_TOKEN','DNS_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY','APP_URL','PUBLIC_ORIGIN','ADMIN_TELEGRAM_IDS','TRUSTED_PROXIES'):
    os.environ[key]=''
import database,queue_service
from web import server
from queue_service import ProductQueueManager,MiniRedisClient
database.init_db()
def result(name,**data): print('AUDIT_RESULT '+json.dumps({'name':name,**data},ensure_ascii=False),flush=True)
with database.get_connection() as c:
    result('connection_pragmas',synchronous=c.execute('PRAGMA synchronous').fetchone()[0],journal_mode=c.execute('PRAGMA journal_mode').fetchone()[0])

async def main():
    # Destructive pop + worker exception.
    qm=ProductQueueManager()
    await qm.enqueue([{'id':'lost'}],'Synthetic',{})
    entered=asyncio.Event()
    async def fail(item): entered.set();raise sqlite3.OperationalError('synthetic writer failure')
    with patch.object(queue_service,'product_queue',qm),patch.object(server,'_execute_queue_item',side_effect=fail):
        worker=asyncio.create_task(server.single_db_writer_worker())
        await entered.wait();await asyncio.sleep(.03)
        result('worker_failure_loses_item',queued=await qm.qsize())
        worker.cancel();await asyncio.gather(worker,return_exceptions=True)

    # Redis enqueue fallback strands old pending items.
    class RedisFailure:
        def __init__(self): self.pending=['older-unwritten-batch']
        async def rpush(self,*args):raise ConnectionError('synthetic outage')
        async def lpop(self,*args):return self.pending.pop(0) if self.pending else None
        async def llen(self,*args):return len(self.pending)
    qm=ProductQueueManager();qm.use_redis=True;qm.redis_client=RedisFailure()
    await qm.enqueue([{'id':'newer'}],'Synthetic',{})
    next_item=await qm.dequeue(.01);after=await qm.dequeue(.01)
    result('fallback_strands_redis',redis_mode=qm.use_redis,reported_size=await qm.qsize(),stranded=len(qm.redis_client.pending),next_id=next_item['prods'][0]['id'],next_after=after)

    # Real local RESP peer accepts commands but never replies.
    stop=asyncio.Event();handlers=[]
    async def silent_peer(reader,writer):
        handlers.append(asyncio.current_task())
        try:await reader.read(4096);await stop.wait()
        finally:writer.close();await writer.wait_closed()
    peer=await asyncio.start_server(silent_peer,'127.0.0.1',0)
    client=MiniRedisClient('127.0.0.1',peer.sockets[0].getsockname()[1])
    await client.connect();one=asyncio.create_task(client.get('a'));two=asyncio.create_task(client.llen('queue'))
    await asyncio.sleep(.25)
    result('redis_commands_no_deadline',first_pending=not one.done(),second_pending=not two.done())
    one.cancel();two.cancel();await asyncio.gather(one,two,return_exceptions=True)
    stop.set();await client.close();peer.close();await peer.wait_closed()
    await asyncio.gather(*handlers,return_exceptions=True)

    # Actual scanner acquires a SQLite lease synchronously in the event loop.
    hold=sqlite3.connect(database.DB_PATH,check_same_thread=False)
    hold.execute('BEGIN IMMEDIATE')
    def release():time.sleep(.7);hold.rollback();hold.close()
    t=threading.Thread(target=release);t.start()
    async def tick():
        start=time.monotonic();await asyncio.sleep(.02);return round(time.monotonic()-start,3)
    pending=asyncio.create_task(tick());await asyncio.sleep(0)
    with patch.object(server,'_scan_task_body',new=AsyncMock()):
        scan=asyncio.create_task(server._do_scan_task([]))
        delay=await pending;await scan
    t.join();result('scan_lease_blocks_loop',timer_20ms_seconds=delay)

    # Already queued results still write after ownership has been lost.
    item={'prods':[{'id':'after-lease','title':'Synthetic lease item','shop':'Audit','price':100,'url':'https://example.invalid','category':'audit'}],'shop_name':'Audit','candidate_settings':{'detect_market_arbitrage':False},'shop_key':'audit'}
    server._lease_state['lost']=True
    await server._execute_queue_item(item)
    with database.get_connection() as c:found=c.execute("SELECT count(*) FROM products WHERE id='after-lease'").fetchone()[0]
    result('queued_write_after_lease_loss',persisted=bool(found))
    server._lease_state['lost']=False

    # Readiness accepts a database with no application tables.
    original=database.DB_PATH;database.DB_PATH=Path(os.environ['DATA_DIR'])/'empty.db'
    try:result('readiness_empty_database',ready=server._check_db_readiness_sync(),size_bytes=database.DB_PATH.stat().st_size)
    finally:database.DB_PATH=original

    from aiohttp.test_utils import make_mocked_request
    qm=ProductQueueManager();qm.is_worker_running=False
    with patch.object(queue_service,'product_queue',qm):
        r=await server.health_handler(make_mocked_request('GET','/api/health'))
        result('readiness_stopped_writer',http=r.status,body=json.loads(r.text))
    with patch.object(server,'_check_db_readiness_sync',side_effect=RuntimeError('synthetic failure')):
        r=await server.health_handler(make_mocked_request('GET','/api/health'))
        result('readiness_exception',http=r.status,body=json.loads(r.text))

    # Shared canonical scope introduces a price drop between unchanged offers.
    database.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
    w=database.create_watch(987654321,{'kind':'search','target':'Audit','condition':'any_drop','quiet_from':'','quiet_to':'','cooldown_hours':0})
    a={'id':'offer-a','product_id':'canon-audit','title':'Audit','shop':'A','current_price':100}
    b={'id':'offer-b','product_id':'canon-audit','title':'Audit','shop':'B','current_price':200}
    first=database.evaluate_watches([a,b]);second=database.evaluate_watches([a])
    events=database.watch_events(987654321)
    result('unchanged_offer_false_drop',initial_events=first,unchanged_events=second,reasons=[e.get('reason') for e in events])

    # Scanner emits legacy IDs, but V2 stores offers with an off_ prefix.
    with database.get_connection() as c:
        canonical=c.execute("SELECT product_id FROM offers WHERE id='off_after-lease'").fetchone()[0]
    legacy_rows=database.watched_offers(['after-lease'])
    v2_rows=database.watched_offers(['off_after-lease'])
    product_watch=database.create_watch(987654321,{'kind':'product','target':canonical,'condition':'new_offer','quiet_from':'','quiet_to':'','cooldown_hours':0})
    triggered=database.evaluate_watches(legacy_rows)
    result('scanner_v2_identity_mismatch',scanner_row_canonical=legacy_rows[0]['canonical_product_id'],actual_canonical=canonical,v2_lookup_canonical=v2_rows[0]['canonical_product_id'],canonical_watch_events=triggered)

    # Active but stale offers still drive seller showcase minima.
    from domain.seller_showcase import group_seller_offers_by_product
    stale=group_seller_offers_by_product([{'id':'stale','product_id':'p','title':'Audit','price':1,'observed_at':'2020-01-01T00:00:00+00:00','availability':'in_stock'}])
    result('stale_showcase_offer',min_price=stale[0]['min_price'],purchasable=stale[0]['channels'][0]['is_purchasable'])

    # SQL price sorting considers expired offers before SearchCore excludes them.
    from repositories import init_schema_v2,ProductRepository,SellerRepository,ChannelRepository,OfferRepository,SearchRepository
    from domain.models import Seller,Channel,CanonicalProduct,Offer
    from domain.search_core import SearchCore
    c=sqlite3.connect(':memory:');init_schema_v2(c)
    SellerRepository(c).save_or_update(Seller(id='s',slug='audit',name='Audit'))
    ChannelRepository(c).save_or_update(Channel(id='c',seller_id='s',channel_type='website',name='Web'))
    for n,price in enumerate((900,100)):
        ProductRepository(c).save_or_update(CanonicalProduct(id=f'p{n}',canonical_key=f'key{n}',title='Audit Laptop',category='laptops'))
        OfferRepository(c).save_or_update(Offer(id=f'o{n}',product_id=f'p{n}',seller_id='s',channel_id='c',external_sku=str(n),url='https://example.invalid',price=price))
    OfferRepository(c).save_or_update(Offer(id='expired',product_id='p0',seller_id='s',channel_id='c',external_sku='expired',url='https://example.invalid',price=1,expires_at='2020-01-01T00:00:00+00:00'))
    core=SearchCore(SearchRepository(c))
    one=core.search('Audit Laptop',sort_by='price_asc',limit=1)
    all_rows=core.search('Audit Laptop',sort_by='price_asc',limit=50)
    result('search_sort_expired_candidate',limit1=one.items[0].min_price,limit50=all_rows.items[0].min_price)
    c.close()

    # Category completion is reported before its queued data has been persisted.
    from scrapers.base import ScanResult
    class Scraper:
        async def scrape(self,*args,**kwargs):
            return ScanResult([{'id':'queued-only','title':'Audit product','price':100,'url':'https://example.invalid','shop':'Audit','city':'Астана'}],complete=True)
    qm=ProductQueueManager();qm.is_worker_running=True
    with patch.object(queue_service,'product_queue',qm):
        count=await server._scan_shop_categories('audit-q',Scraper(),[{'name':'Audit category','url':'https://example.invalid/category'}],'Audit',{})
    with database.get_connection() as c:
        status=c.execute("SELECT status FROM shop_scans WHERE shop_key='audit-q'").fetchone()[0]
        products=c.execute("SELECT count(*) FROM products WHERE id LIKE 'queued-only%'").fetchone()[0]
    result('scan_complete_before_commit',reported_status=status,collected=count,queued=await qm.qsize(),persisted=products)

    # Diagnostics is imported lazily on the first request, not on application startup.
    if Path(sys.argv[1],'diagnostics.py').exists():
        import diagnostics
        result('diagnostic_lazy_uptime',diagnostic_uptime_seconds=round(diagnostics.uptime_seconds(),4),process_already_initialized=True)

asyncio.run(main())
