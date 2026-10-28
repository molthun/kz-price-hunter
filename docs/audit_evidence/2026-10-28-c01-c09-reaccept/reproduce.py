"""Independent acceptance: synthetic data only, no Redis/AI/Telegram/network required."""
import asyncio
import datetime
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
CHILD = len(sys.argv) > 1 and sys.argv[1].startswith('--child-')
if not CHILD:
    os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph-c-reaccept-')
for key in ('TELEGRAM_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY','APP_URL','PUBLIC_ORIGIN','ADMIN_TELEGRAM_IDS'):
    os.environ[key] = ''
import database
import queue_service
from queue_service import ProductQueueManager
from web import server

results = []
def record(name, **values):
    row = {'name':name, **values}
    results.append(row)
    print('AUDIT_RESULT '+json.dumps(row,ensure_ascii=False),flush=True)

def clear():
    with database.get_connection() as c:
        c.execute('DELETE FROM product_queue_spool')
        c.commit()
    server._lease_state.update(lost=False,generation=2)

def prod(pid, price=100):
    return {'id':pid,'title':'Audit Laptop','shop':'Audit','price':price,
            'url':'https://example.invalid','category':'laptops','city':'Астана'}

async def child():
    database.init_db()
    qm = ProductQueueManager()
    if sys.argv[1] == '--child-crash':
        # First real scan increments initial generation 1 to 2.
        server._lease_state['generation'] = 2
        await qm.enqueue([prod('crash-recovered')],'Audit',{},shop_key='crash',lease_generation=2)
        await qm.dequeue()
        os.kill(os.getpid(),signal.SIGKILL)
    with patch.object(queue_service,'start_redis_daemon_if_available',return_value=False), patch.object(queue_service,'_is_port_open',return_value=False):
        await qm.initialize()
    item = await qm.dequeue(.05)
    result = await server._execute_queue_item(item)
    if result is not False:
        qm.ack(item)
    with database.get_connection() as c:
        written=c.execute("SELECT count(*) FROM products WHERE id='crash-recovered'").fetchone()[0]
    record('recovery_child',generation=server._lease_state['generation'],item_generation=item['lease_generation'],execution_result=result,written=written,spool=database.spool_stats())

async def main():
    database.init_db()
    clear()
    qm=ProductQueueManager()
    with patch.object(database,'spool_enqueue',side_effect=sqlite3.OperationalError('synthetic unavailable spool')):
        accepted=await qm.enqueue([prod('fallback')],'Audit',{},shop_key='fallback',lease_generation=2)
    item=await qm.dequeue(.01)
    qm.nack(item,error='synthetic write failure')
    record('fallback_task_id_loses_retry',accepted=accepted,has_task_id=bool(item.get('task_id')),queue_after_nack=await qm.qsize(),spool=database.spool_stats(),next_item=await qm.dequeue(.01))

    clear()
    qm=ProductQueueManager()
    database.spool_enqueue('unreadable-task','unreadable','Audit',2,{'prods':[]})
    with patch.object(database,'spool_stats',side_effect=sqlite3.OperationalError('synthetic unreadable spool')):
        drained=await qm.wait_drained(timeout=.1)
    record('unreadable_spool_false_drained',drained=drained,actual_pending=database.spool_stats()['pending'])

    clear()
    database.spool_enqueue('old-rejected','same-shop','Audit',1,{'prods':[]})
    database.spool_reject('old-rejected')
    qm=ProductQueueManager()
    await qm.enqueue([prod('new-scan')],'Audit',{},shop_key='same-shop',lease_generation=2)
    item=await qm.dequeue();qm.ack(item)
    qm.is_worker_running=True
    from aiohttp.test_utils import make_mocked_request
    with patch.object(queue_service,'product_queue',qm):
        health=await server.health_handler(make_mocked_request('GET','/api/health'))
    record('old_rejection_poison_new_scan',new_scan_drained=await qm.wait_drained(shop_key='same-shop',timeout=.1),health=json.loads(health.text),spool=database.spool_stats())
    database.spool_enqueue('dl','other-shop','Audit',2,{})
    with database.get_connection() as c:
        c.execute("UPDATE product_queue_spool SET status='dead_letter' WHERE task_id='dl'");c.commit()
    with patch.object(queue_service,'product_queue',qm):
        health=await server.health_handler(make_mocked_request('GET','/api/health'))
    record('dead_letters_still_ready',http=health.status,body=json.loads(health.text))

    clear()
    qm=ProductQueueManager()
    await qm.enqueue([prod('ack')],'Audit',{},shop_key='ack',lease_generation=2)
    item=await qm.dequeue()
    holder=sqlite3.connect(database.DB_PATH,check_same_thread=False)
    holder.execute('BEGIN IMMEDIATE')
    def release():
        time.sleep(.45);holder.rollback();holder.close()
    t=threading.Thread(target=release);t.start()
    async def ticker():
        start=time.monotonic();await asyncio.sleep(.02);return round(time.monotonic()-start,3)
    tick=asyncio.create_task(ticker());await asyncio.sleep(0)
    qm.ack(item)
    record('ack_blocks_event_loop',timer_20ms_seconds=await tick)
    t.join()

    clear()
    env=dict(os.environ)
    producer=await asyncio.to_thread(subprocess.run,[sys.executable,str(Path(__file__).resolve()),'--child-crash'],env=env,capture_output=True,text=True,timeout=15)
    recovery=await asyncio.to_thread(subprocess.run,[sys.executable,str(Path(__file__).resolve()),'--child-recover'],env=env,capture_output=True,text=True,timeout=15)
    child_rows=[json.loads(x.removeprefix('AUDIT_RESULT ')) for x in recovery.stdout.splitlines() if x.startswith('AUDIT_RESULT ')]
    record('real_sigkill_recovery',producer_returncode=producer.returncode,recovery_returncode=recovery.returncode,recovered=child_rows)

    clear()
    database.spool_enqueue('active-owner','active','Audit',2,{})
    original=database.spool_dequeue()
    recovered=database.spool_recover_on_startup()
    stolen=database.spool_dequeue()
    record('second_start_steals_processing',recovered=recovered,same_task=original['task_id']==stolen['task_id'])

    clear()
    qm=ProductQueueManager()
    await qm.enqueue([prod('late-fenced')],'Audit',{},shop_key='late',lease_generation=2)
    item=await qm.dequeue()
    entered=asyncio.Event();resume=asyncio.Event()
    original_save=server._save_and_detect_direct
    async def delayed_save(*args,**kwargs):
        entered.set();await resume.wait();await original_save(*args,**kwargs)
    with patch.object(queue_service,'product_queue',qm),patch.object(server,'_save_and_detect_direct',side_effect=delayed_save):
        task=asyncio.create_task(server._execute_queue_item(item))
        await entered.wait();server._lease_state['lost']=True;resume.set()
        execution=await task
        if execution is not False:qm.ack(item)
    with database.get_connection() as c:
        written=c.execute("SELECT count(*) FROM products WHERE id='late-fenced'").fetchone()[0]
    record('lease_lost_after_guard_still_writes',lost=server._lease_state['lost'],execution_result=execution,written=written,spool=database.spool_stats())

    clear()
    # A committed price followed by an error before watch evaluation loses the transition on retry.
    initial=prod('retry-watch',100)
    database.save_or_update_products_batch([initial])
    database.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
    database.create_watch(987654321,{'kind':'search','target':'Audit Laptop','condition':'any_drop','quiet_from':'','quiet_to':'','cooldown_hours':0})
    database.evaluate_watches(database.watched_offers(['retry-watch']))
    qm=ProductQueueManager()
    await qm.enqueue([prod('retry-watch',80)],'Audit',{'detect_market_arbitrage':False},shop_key='retry',lease_generation=2)
    item=await qm.dequeue()
    with patch.object(server,'check_anomaly',side_effect=RuntimeError('synthetic post-commit failure')):
        try:await server._execute_queue_item(item)
        except RuntimeError:qm.nack(item,error='post-commit failure')
    item=await qm.dequeue()
    with patch.object(server,'check_anomaly',return_value=None),patch.object(server,'check_market_arbitrage',return_value=None):
        await server._execute_queue_item(item);qm.ack(item)
    record('retry_loses_price_transition',events=len(database.watch_events(987654321)),spool=database.spool_stats())
    with patch.object(server,'check_anomaly',return_value=None),patch.object(server,'check_market_arbitrage',return_value=None):
        await server._execute_queue_item({'prods':[prod('retry-watch',60)],'shop_name':'Audit','lease_generation':2})
    record('price_transition_positive_control',events=len(database.watch_events(987654321)))

    clear()
    from repositories import init_schema_v2,ProductRepository,SellerRepository,ChannelRepository,OfferRepository,SearchRepository
    from domain.models import Seller,Channel,CanonicalProduct,Offer
    from domain.search_core import SearchCore
    c=sqlite3.connect(':memory:');init_schema_v2(c)
    SellerRepository(c).save_or_update(Seller(id='s',slug='audit',name='Audit'))
    ChannelRepository(c).save_or_update(Channel(id='c',seller_id='s',channel_type='website',name='Web'))
    now=datetime.datetime.now(datetime.timezone.utc)
    for n,price in enumerate((1,100)):
        ProductRepository(c).save_or_update(CanonicalProduct(id=f'p{n}',canonical_key=f'k{n}',title='Audit Laptop',category='laptops'))
        OfferRepository(c).save_or_update(Offer(id=f'o{n}',product_id=f'p{n}',seller_id='s',channel_id='c',external_sku=str(n),url='https://example.invalid',price=price,observed_at='2020-01-01T00:00:00+00:00' if n==0 else now.isoformat()))
    core=SearchCore(SearchRepository(c))
    a=core.search('Audit Laptop',sort_by='price_asc',limit=1);b=core.search('Audit Laptop',sort_by='price_asc',limit=50)
    record('stale_candidate_still_wins_limit',limit1=[{'id':x.product.id,'min':x.min_price} for x in a.items],limit50=[{'id':x.product.id,'min':x.min_price} for x in b.items])
    # At exact TTL SQL >= still ranks an expired offer as if it were purchasable.
    c.execute("UPDATE offers SET observed_at=?, expires_at=? WHERE id='o0'",(now.isoformat(),now.isoformat()));c.commit()
    sql_first=SearchRepository(c).search_products('Audit Laptop',sort_by='price_asc',limit=1,now=now)
    from domain.search_core import offer_expired
    record('sql_exact_ttl_contradiction',first_product=sql_first[0]['product'].id,python_expired=offer_expired(now.isoformat(),now))
    c.close()

    # Disabled channel and stale observation are not checked by watched_offers.
    with database.get_connection() as c:
        SellerRepository(c).save_or_update(Seller(id='ws',slug='waudit',name='Watch Audit'))
        ChannelRepository(c).save_or_update(Channel(id='wc',seller_id='ws',channel_type='website',name='Web',is_active=False))
        ProductRepository(c).save_or_update(CanonicalProduct(id='wp',canonical_key='watch-disabled',title='Disabled Laptop',category='laptops'))
        OfferRepository(c).save_or_update(Offer(id='off_disabled',product_id='wp',seller_id='ws',channel_id='wc',external_sku='disabled',url='https://example.invalid',price=50,observed_at='2020-01-01T00:00:00+00:00'))
    watch=database.create_watch(987654321,{'kind':'product','target':'wp','condition':'target_price','threshold':100,'quiet_from':'','quiet_to':'','cooldown_hours':0})
    rows=database.watched_offers(['off_disabled'])
    count=database.evaluate_watches(rows)
    record('disabled_stale_offer_watch',available=rows[0]['is_available'],new_events=count)

if CHILD:
    asyncio.run(child())
else:
    asyncio.run(main())
    Path(__file__).with_name('results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
