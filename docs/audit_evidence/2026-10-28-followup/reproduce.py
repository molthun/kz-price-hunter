"""Read-only source audit: synthetic queues/temporary DB only; no external services."""
import asyncio
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph-followup-')
for key in ('TELEGRAM_BOT_TOKEN', 'GEMINI_API_KEY', 'OPENAI_API_KEY', 'PUBLIC_ORIGIN', 'ADMIN_TELEGRAM_IDS'):
    os.environ[key] = ''
import database
import queue_service
from queue_service import ProductQueueManager
from web import server

results = []
def record(name, **values):
    item = dict(name=name, **values)
    results.append(item)
    print('AUDIT_RESULT ' + json.dumps(item), flush=True)

async def main():
    database.init_db()
    # Exhausted retries disappear from the completion barrier.
    qm = ProductQueueManager()
    await qm.enqueue([{'id': 'never-saved'}], 'Synthetic', {})
    item = await qm.dequeue()
    for attempt in range(4):
        qm.nack(item)
        if attempt < 3:
            item = await qm.dequeue()
    qm.is_worker_running = True
    from aiohttp.test_utils import make_mocked_request
    with patch.object(queue_service, 'product_queue', qm):
        health = await server.health_handler(make_mocked_request('GET', '/api/health'))
    record('dead_letter_false_success', dead_letters=len(qm.dead_letter_queue),
           queue_size=await qm.qsize(), drained=await qm.wait_drained(.1), readiness=health.status)

    from scrapers.base import ScanResult
    class SyntheticScraper:
        async def scrape(self, *args, **kwargs):
            return ScanResult([{'id':'never-committed','title':'Audit Laptop','price':100,
                                'url':'https://example.invalid','shop':'Audit','city':'Астана'}],complete=True)
    qm = ProductQueueManager()
    with patch.object(queue_service, 'product_queue', qm), patch.object(server, '_execute_queue_item', side_effect=sqlite3.OperationalError('synthetic persistent lock')):
        worker = asyncio.create_task(server.single_db_writer_worker())
        await asyncio.sleep(0)
        await server._scan_shop_categories('audit-loss', SyntheticScraper(), [{'name':'Audit','url':'https://example.invalid/category'}], 'Audit', {})
        with database.get_connection() as conn:
            status = conn.execute("SELECT status FROM shop_scans WHERE shop_key='audit-loss'").fetchone()[0]
            saved = conn.execute("SELECT count(*) FROM products WHERE id LIKE 'never-committed%'").fetchone()[0]
        record('scan_complete_after_dead_letter', shop_status=status, saved=saved, dead_letters=len(qm.dead_letter_queue))
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    # Full fallback times out and returns the same success value as accepted enqueue.
    qm = ProductQueueManager(maxsize=1)
    first = await qm.enqueue([{'id': 'first'}], 'Synthetic', {})
    before = time.monotonic()
    second = await qm.enqueue([{'id': 'discarded'}], 'Synthetic', {})
    record('fallback_silent_drop', first_return=first, second_return=second,
           queued=await qm.qsize(), elapsed=round(time.monotonic()-before, 2))

    # Reconcile failure is swallowed: worker acknowledges the task and loses retryability.
    qm = ProductQueueManager()
    await qm.enqueue([], 'Synthetic', {}, reconcile={'key':'audit', 'url':'https://example.invalid'})
    with patch.object(queue_service, 'product_queue', qm), patch.object(database, 'reconcile_source', side_effect=sqlite3.OperationalError('synthetic lock')):
        worker = asyncio.create_task(server.single_db_writer_worker())
        await asyncio.sleep(.15)
        record('reconcile_failure_acknowledged', queued=await qm.qsize(),
               in_flight=len(qm._in_flight), retries=len(qm._retry_queue), drained=await qm.wait_drained(.1))
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    # A queued category rejected by fencing is ACKed as success by the real worker.
    qm = ProductQueueManager()
    await qm.enqueue([{'id':'fenced'}], 'Synthetic', {})
    server._lease_state['lost'] = True
    with patch.object(queue_service, 'product_queue', qm), patch.object(server, '_save_and_detect_direct', new=AsyncMock()) as save:
        worker = asyncio.create_task(server.single_db_writer_worker())
        await asyncio.sleep(.1)
        record('fencing_acknowledges_discard', saved=save.await_count, queued=await qm.qsize(), drained=await qm.wait_drained(.1))
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    # No task owner/generation is checked when the shared lost flag is reset.
    server._lease_state['lost'] = False
    with patch.object(server, '_save_and_detect_direct', new=AsyncMock()) as save:
        await server._execute_queue_item({'prods':[{'id':'old-owner'}], 'shop_name':'Synthetic'})
        record('fencing_generation_absent', old_task_saved=save.await_count)

    # Liveness still goes through cookie lookup in the shared scraper executor.
    import auth
    gate = threading.Event()
    loop = asyncio.get_running_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    busy = loop.run_in_executor(None, gate.wait)
    req = make_mocked_request('GET', '/api/live', headers={'Cookie':'kzph_session=synthetic-invalid'})
    with patch.object(auth, 'get_session_user', return_value=None):
        start = time.monotonic()
        request = asyncio.create_task(auth.auth_middleware(req, server.live_handler))
        await asyncio.sleep(.2)
        blocked = not request.done()
        gate.set()
        response = await request
        await busy
        record('liveness_cookie_shared_pool', blocked_at_200ms=blocked, status=response.status,
               elapsed=round(time.monotonic()-start,3))

    # Availability sort excludes expired offers, but candidates still include them.
    from repositories import init_schema_v2, ProductRepository, SellerRepository, ChannelRepository, OfferRepository, SearchRepository
    from domain.models import Seller, Channel, CanonicalProduct, Offer
    from domain.search_core import SearchCore
    c = sqlite3.connect(':memory:')
    init_schema_v2(c)
    SellerRepository(c).save_or_update(Seller(id='s',slug='audit',name='Audit'))
    ChannelRepository(c).save_or_update(Channel(id='c',seller_id='s',channel_type='website',name='Web'))
    for n, price in enumerate((1,100)):
        ProductRepository(c).save_or_update(CanonicalProduct(id=f'p{n}',canonical_key=f'key{n}',title='Audit Laptop',category='laptops'))
        OfferRepository(c).save_or_update(Offer(id=f'o{n}',product_id=f'p{n}',seller_id='s',channel_id='c',external_sku=str(n),url='https://example.invalid',price=price,expires_at='2020-01-01T00:00:00+00:00' if n==0 else None))
    core = SearchCore(SearchRepository(c))
    small = core.search('Audit Laptop',sort_by='price_asc',limit=1)
    large = core.search('Audit Laptop',sort_by='price_asc',limit=50)
    record('expired_only_candidate_before_limit', limit1=[{'id':x.product.id,'min':x.min_price,'purchasable':x.has_purchasable_offers} for x in small.items], limit50=[{'id':x.product.id,'min':x.min_price,'purchasable':x.has_purchasable_offers} for x in large.items])
    c.close()
    # Boundary differs from content/showcase at exactly expires_at.
    from domain.search_core import offer_expired
    from domain.seller_showcase import is_offer_expired
    now = datetime.datetime.now(datetime.timezone.utc)
    record('ttl_boundary', search_expired=offer_expired(now.isoformat(),now), showcase_expired=is_offer_expired(now.isoformat(),now))

    # Saved V2 availability/TTL are lost when entering the notification path.
    with database.get_connection() as conn:
        SellerRepository(conn).save_or_update(Seller(id='watch-s',slug='watch-audit',name='Watch Audit'))
        ChannelRepository(conn).save_or_update(Channel(id='watch-c',seller_id='watch-s',channel_type='website',name='Web'))
        ProductRepository(conn).save_or_update(CanonicalProduct(id='watch-p',canonical_key='watch-audit',title='Watch Audit Laptop',category='laptops'))
        OfferRepository(conn).save_or_update(Offer(id='off_expired-watch',product_id='watch-p',seller_id='watch-s',channel_id='watch-c',external_sku='expired-watch',url='https://example.invalid',price=100,availability='out_of_stock',expires_at='2020-01-01T00:00:00+00:00'))
    database.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
    database.create_watch(987654321,{'kind':'product','target':'watch-p','condition':'target_price','threshold':150,'quiet_from':'','quiet_to':'','cooldown_hours':0})
    rows = database.watched_offers(['off_expired-watch'])
    triggered = database.evaluate_watches(rows)
    record('expired_unavailable_watch', input_available=rows[0]['is_available'],
           includes_expiry='expires_at' in rows[0], includes_availability='availability' in rows[0],
           notification_events=triggered)

asyncio.run(main())
Path(__file__).with_name('results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
