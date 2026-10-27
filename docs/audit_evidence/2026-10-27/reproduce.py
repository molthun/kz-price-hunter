import os, sys, tempfile, sqlite3, json, logging, io, asyncio, time, threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph-audit-repro-')
for k in ('TELEGRAM_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY','APP_URL','PUBLIC_ORIGIN','ADMIN_TELEGRAM_IDS'):
    os.environ[k] = ''
import database
import release_gate
with tempfile.TemporaryDirectory() as td:
    Path(td,'source.py').write_text('API_KEY = '+repr('sk-'+'proj-'+('R9mN7qT2pL6vB4cD8hJ5sK3wZ1aE0fG'*4)))
    report=release_gate.check_secrets(td)
    print('SECRET_SCAN_PROJECT_KEY',json.dumps({'ok':report['ok'],'findings_count':len(report['findings'])}))
from repositories import init_schema_v2, ProductRepository, SellerRepository, ChannelRepository, OfferRepository, SearchRepository
from domain.models import Seller, Channel, CanonicalProduct, Offer
from domain.search_core import SearchCore
from domain.social_content import ContentObservation
from repositories.content_repo import ContentObservationRepository

conn = sqlite3.connect(':memory:')
init_schema_v2(conn)
SellerRepository(conn).save_or_update(Seller(id='s',slug='seller',name='Audit Seller'))
ChannelRepository(conn).save_or_update(Channel(id='c',seller_id='s',channel_type='website',name='Website'))
for n, price in enumerate((900, 100)):
    ProductRepository(conn).save_or_update(CanonicalProduct(id=f'p{n}',canonical_key=f'key{n}',title='Audit Laptop',category='laptops'))
    OfferRepository(conn).save_or_update(Offer(id=f'o{n}',product_id=f'p{n}',seller_id='s',channel_id='c',external_sku=str(n),url='https://example.invalid',price=price))
core = SearchCore(SearchRepository(conn))
limited = core.search('Audit Laptop',sort_by='price_asc',limit=1)
all_items = core.search('Audit Laptop',sort_by='price_asc',limit=50)
print('SORT_LIMIT',json.dumps({'limit1':limited.items[0].min_price,'limit50':all_items.items[0].min_price}))

repo = ContentObservationRepository(conn)
obs = ContentObservation(id='ttl',seller_id='s',channel_id='c',expires_at='2026-10-27T13:00:00-05:00')
repo.save_observation(obs)
print('TTL_OFFSET',json.dumps({'expired_python':obs.is_expired('2026-10-27T17:00:00+00:00'),'visible_sql':len(repo.find_by_seller('s',now_iso='2026-10-27T17:00:00+00:00')),'purged_sql':repo.purge_expired('2026-10-27T17:00:00+00:00')}))

class BrokenRepo:
    def search_products(self,**kw): raise sqlite3.OperationalError('synthetic failure')
stream=io.StringIO(); handler=logging.StreamHandler(stream)
logger=logging.getLogger('kz_price_hunter.domain.search_core')
# Capture the actual module logger irrespective of its configured name.
import domain.search_core as sc
sc.logger.addHandler(handler)
SearchCore(BrokenRepo()).search('audit.person@example.invalid')
sc.logger.removeHandler(handler)
print('QUERY_ERROR_LOG', 'audit.person@example.invalid' in stream.getvalue())

database.init_db()
with database.get_connection() as db:
    db.execute("CREATE TABLE IF NOT EXISTS audit_lock (id INTEGER)")
    db.commit()
async def blocking_demo():
    holder=sqlite3.connect(database.DB_PATH,check_same_thread=False)
    holder.execute('BEGIN IMMEDIATE')
    def release():
        time.sleep(.7); holder.rollback();holder.close()
    thread=threading.Thread(target=release);thread.start()
    async def tick():
        start=time.monotonic();await asyncio.sleep(.02)
        return round(time.monotonic()-start,3)
    pending=asyncio.create_task(tick());await asyncio.sleep(0)
    with database.get_connection() as db:
        db.execute('INSERT INTO audit_lock VALUES (1)');db.commit()
    delay=await pending;thread.join();return delay
print('EVENT_LOOP_DELAY_SECONDS',asyncio.run(blocking_demo()))

async def http_demo():
    from aiohttp import web
    from aiohttp.test_utils import TestServer, TestClient
    import web.server as server
    app=server.create_app();app.cleanup_ctx.clear()
    client=TestClient(TestServer(app));await client.start_server()
    try:
        with patch.object(SearchRepository,'search_products',side_effect=sqlite3.OperationalError('synthetic failure')):
            response=await client.get('/api/v2/search?q=Audit')
            body=await response.json()
            print('HTTP_SEARCH_FAILURE',json.dumps({'http':response.status,'outcome':body.get('outcome')}))
        with patch.object(server,'get_stats',side_effect=sqlite3.OperationalError('synthetic failure')):
            version=await client.get('/api/version')
            stats=await client.get('/api/stats')
            print('READINESS_FALSE_GREEN',json.dumps({'version_http':version.status,'stats_http':stats.status}))
    finally: await client.close()
asyncio.run(http_demo())

if Path(sys.argv[1], 'domain/seller_showcase.py').exists():
    import watches as w
    from domain.seller_showcase import group_seller_offers_by_product
    showcase=group_seller_offers_by_product([
        {'id':'old','product_id':'p','title':'Audit','price':1,'condition':'used','availability':'out_of_stock','expires_at':'2020-01-01T00:00:00+00:00'},
        {'id':'new','product_id':'p','title':'Audit','price':100,'condition':'new','availability':'in_stock'}])
    print('SHOWCASE_MIN',showcase[0]['min_price'])
    with database.get_connection() as db:
        db.execute("INSERT INTO products(id,title,shop,city,current_price,first_seen_price,min_price,max_price,canonical_key,url) VALUES ('legacy','Audit Laptop','Audit Seller','Астана',200,200,200,200,'audit-key','https://example.invalid')")
        db.commit()
    print('WATCHED_OFFERS_KEYS',sorted(database.watched_offers(['legacy'])[0]))
    database.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
    for condition in ('better_seller','new_seller'):
        watch=database.create_watch(987654321,{'kind':'product','target':'canon','condition':condition,'quiet_from':'','quiet_to':'','cooldown_hours':0})
        batch=[{'id':'a','product_id':'canon','title':'Audit','shop':'Seller A','seller_slug':'a','channel_type':'website','current_price':200},
               {'id':'b','product_id':'canon','title':'Audit','shop':'Seller B' if condition=='better_seller' else 'Seller A','seller_slug':'b' if condition=='better_seller' else 'a','channel_type':'telegram','current_price':100}]
        before=len(database.watch_events(987654321))
        database.evaluate_watches(batch)
        print('WATCH_'+condition.upper(),len(database.watch_events(987654321))-before)
        database.delete_watch(987654321,watch['id'])
