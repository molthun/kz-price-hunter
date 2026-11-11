import asyncio, hashlib, hmac, json, time
from threading import Barrier
from urllib.parse import urlencode
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import auth, database, notifier
from domain import barcode_resolver
from web import server

UID = 700000001
BOT_TOKEN = 'synthetic-token-for-audit'
EAN = '4870237159941'
TITLE = 'Tompi салфетки 2 упаковки по 100 шт'
WRONG = 'Tompi салфетки 100 упаковок по 2 шт'

def sign(uid):
    data = {'auth_date': str(int(time.time())), 'user': json.dumps({'id':uid})}
    key = hmac.new(b'WebAppData', BOT_TOKEN.encode(), hashlib.sha256).digest()
    message = '\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
    data['hash'] = hmac.new(key,message.encode(),hashlib.sha256).hexdigest()
    return urlencode(data)

def seed():
    database.init_db()
    database.upsert_telegram_user({'id':UID,'first_name':'Synthetic'})
    database.set_user_blocked(UID, True)
    with database.get_connection() as conn:
        conn.execute("INSERT INTO sellers(id,slug,name,created_at,updated_at) VALUES('s','s','Fixture Store',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO channels(id,seller_id,channel_type,name,created_at) VALUES('ch','s','website','site',CURRENT_TIMESTAMP)")
        conn.executemany("INSERT INTO canonical_products(id,canonical_key,title,category,created_at,updated_at) VALUES(?,?,?,'test',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",[('target','target','Test Product'),('right','right',TITLE),('wrong','wrong',WRONG)])
        conn.executemany("""INSERT INTO offers(id,product_id,seller_id,channel_id,external_sku,url,price,city,observed_at,expires_at,availability,created_at,updated_at)
                       VALUES(?,'target','s','ch',?,?,?,?,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""",[
            ('astana','a','https://example.invalid/astana',120000,'Астана',None,'in_stock'),
            ('almaty','b','https://example.invalid/almaty',80000,'Алматы',None,'in_stock'),
            ('expired','c','https://example.invalid/expired',40000,'Алматы','2020-01-01T00:00:00Z','out_of_stock')])
        conn.commit()

async def main():
    seed()
    app = web.Application(middlewares=[auth.auth_middleware])
    app.add_routes(server.routes)
    async with TestClient(TestServer(app)) as client:
        origin = str(client.make_url('')).rstrip('/')
        headers = {'Origin':origin}
        with patch.object(auth,'expected_origin',return_value=origin), patch.object(auth,'MAINTENANCE_DEV_MODE',False), patch.object(server,'get_bot_token',return_value=BOT_TOKEN), patch.object(notifier,'save_prepared_inline_message',return_value='fixture') as send:
            login = await client.post('/api/auth/telegram-webapp',headers=headers,json={'init_data':sign(UID)})
            share = await client.post('/api/telegram/prepared-message',headers=headers,json={'init_data':sign(UID),'canonical_product_id':'target','city':'Астана'})
            print('blocked_login_and_share',login.status,share.status,'calls',send.call_count)
            print('share_wrong_city_unavailable_expired',send.call_args.args[2],send.call_args.args[4],send.call_args.args[5])
        class Page:
            outcome = 'FOUND'
            def to_dict(self):
                return {'items':[{'product':{'id':'wrong','title':WRONG}}], 'total_products':2, 'total_pages':2}
        with patch.object(auth,'MAINTENANCE_DEV_MODE',False), patch('local_search.enabled',return_value=False), patch.object(barcode_resolver,'resolve_barcode',return_value={'title':TITLE,'source':'searxng'}), patch.object(server,'_run_search_v2_sync',return_value=Page()):
            response = await client.get('/api/v2/search',params={'q':EAN,'limit':1})
            body = await response.json()
            print('barcode_quantities_swapped',barcode_resolver.is_exact_variant_match(TITLE,WRONG),response.status,body['barcode_match'],barcode_resolver.get_barcode_product_ids(EAN))
        with database.get_connection() as conn:
            print('catalog_both_variants_present', conn.execute("SELECT count(*) FROM canonical_products WHERE id IN ('right','wrong')").fetchone()[0])
        barcode_resolver.unlink_barcode(EAN, ['wrong'])
        barrier = Barrier(2, timeout=3)
        read_links = barcode_resolver.get_barcode_product_ids
        def read_before_either_write(code):
            ids = read_links(code)
            barrier.wait()
            return ids
        admin = {'id': UID, 'settings': {}}
        request_headers = {**headers, 'Cookie': f'{auth.SESSION_COOKIE}=synthetic'}
        async def attach(pid):
            return await client.post(f'/api/v2/products/{pid}/barcode',headers=request_headers,json={'barcode':EAN})
        with patch.object(auth,'expected_origin',return_value=origin), patch.object(auth,'MAINTENANCE_DEV_MODE',False), patch.object(auth,'ADMIN_TELEGRAM_IDS',{UID}), patch.object(auth,'get_session_user',return_value=admin), patch.object(barcode_resolver,'get_barcode_product_ids',side_effect=read_before_either_write):
            replies = await asyncio.gather(attach('right'), attach('wrong'))
        print('parallel_admin_attachment',[reply.status for reply in replies], sorted(read_links(EAN)))
asyncio.run(main())
