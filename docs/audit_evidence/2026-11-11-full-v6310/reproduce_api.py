import asyncio, json
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import auth, database, notifier
from domain import barcode_resolver
from web import server

BARCODE = '4870237159941'
UID = 700000001

async def main():
    database.init_db()
    app = web.Application(middlewares=[auth.auth_middleware])
    app.add_routes(server.routes)
    async with TestClient(TestServer(app)) as client:
        origin = str(client.make_url('')).rstrip('/')
        with patch.object(auth, 'expected_origin', return_value=origin), patch.object(auth, 'MAINTENANCE_DEV_MODE', True), patch.object(notifier, 'save_prepared_inline_message', return_value='synthetic-prepared-id') as send:
            response = await client.post('/api/telegram/prepared-message', headers={'Origin': origin}, json={'user_id': UID, 'title': 'Synthetic listing', 'price': 1, 'url': 'https://example.invalid/item'})
            print('prepared_anonymous_closed', response.status, 'called', send.call_count, 'for_supplied_user', send.call_args.args[0] == UID)
            with patch.object(auth, 'get_session_user', return_value={'id': UID + 1}):
                response = await client.post('/api/telegram/prepared-message', headers={'Origin': origin, 'Cookie': f'{auth.SESSION_COOKIE}=test'}, json={'user_id': UID, 'title': 'Synthetic listing', 'price': 1})
                print('prepared_other_user', response.status, 'for_supplied_user', send.call_args.args[0] == UID)
        with patch.object(auth, 'MAINTENANCE_DEV_MODE', True), patch.object(server, 'get_bot_username', return_value='SyntheticBot'):
            statuses = []
            for _ in range(3):
                response = await client.get('/api/telegram/deep-link', params={'action': 'search', 'target': 'x' * 100})
                statuses.append(response.status)
                await response.read()
            with database.get_connection() as conn:
                counts = conn.execute('SELECT COUNT(*), COUNT(DISTINCT payload_json) FROM bot_deep_links').fetchone()
            print('anonymous_deep_link_duplicate', statuses, tuple(counts))
        with patch.object(auth, 'expected_origin', return_value=origin), patch.object(auth, 'MAINTENANCE_DEV_MODE', False):
            response = await client.post('/api/v2/products/synthetic-nonexistent/barcode', headers={'Origin': origin}, json={'barcode': BARCODE})
            print('barcode_anonymous_nonexistent', response.status, 'persisted', barcode_resolver.get_product_barcodes('synthetic-nonexistent') == [BARCODE])
            with database.get_connection() as conn:
                conn.executemany("INSERT INTO canonical_products(id,canonical_key,title,brand,category,created_at,updated_at) VALUES(?,?,?,?,'grocery',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)", [('prod_a','key_a','Влажные салфетки Tompi 100 шт','Tompi'),('prod_b','key_b','Влажные салфетки Другой бренд 10 шт','Другой бренд')])
                conn.commit()
            async def resolver(code):
                return {'title': 'Влажные салфетки Tompi 100 шт'}
            class Response:
                outcome = 'FOUND'
                def to_dict(self):
                    return {'items': [{'product': {'id': 'prod_a', 'title': 'Влажные салфетки Tompi 100 шт'}}, {'product': {'id': 'prod_b', 'title': 'Влажные салфетки Другой бренд 10 шт'}}]}
            with patch('local_search.enabled', return_value=False), patch.object(barcode_resolver,'resolve_barcode', side_effect=resolver), patch.object(server,'_run_search_v2_sync',return_value=Response()):
                response = await client.get('/api/v2/search', params={'q':BARCODE})
                data = await response.json()
                for _ in range(50):
                    if barcode_resolver.get_product_barcodes('prod_b'):
                        break
                    await asyncio.sleep(0.02)
                print('barcode_search_wrong_variant', response.status, 'advertised', data['items'][1].get('barcode') == BARCODE, 'persisted', barcode_resolver.get_product_barcodes('prod_b') == [BARCODE])
            response = await client.get('/api/v2/search', params={'q':'²'*8})
            print('barcode_unicode_search', response.status)
    database.upsert_telegram_user({'id': UID, 'first_name': 'Synthetic'})
    database.save_user_topic(UID, 'alerts', 900)
    database.save_ai_feedback(UID, 'good')
    exported = database.export_user_data(UID)
    database.delete_user_account(UID)
    with database.get_connection() as conn:
        counts = [conn.execute(f'SELECT COUNT(*) FROM {table} WHERE user_id = ?', (UID,)).fetchone()[0] for table in ('user_telegram_topics','ai_feedback')]
    print('privacy_export_fields', 'topics' in exported, 'ai_feedback' in exported, 'remaining_topic_and_feedback', counts)

asyncio.run(main())
