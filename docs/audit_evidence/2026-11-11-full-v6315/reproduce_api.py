import asyncio
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import auth, database, notifier
from domain import barcode_resolver as barcode
from web import server

TITLE = 'Сок Добрый яблочный 1 л'
CODE = '4870237159941'

def seed():
    database.init_db()
    database.upsert_telegram_user({'id': 700000001, 'first_name': 'Synthetic'})
    with database.get_connection() as conn:
        rows = [('candidate1', TITLE)]
        rows.extend((f'decoy{i}', f'{TITLE} дополнительные сведения') for i in range(499))
        rows.append(('candidate2', TITLE))
        conn.executemany(
            """INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at)
            VALUES (?, ?, ?, 'grocery', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            [(pid, pid, title) for pid, title in rows],
        )
        conn.execute("""INSERT INTO products (id,title,shop,url,current_price,first_seen_price,min_price,
             max_price,is_active,city,updated_at) VALUES
             ('legacy-almaty','Synthetic product','Synthetic shop','https://example.invalid/almaty',
              80000,80000,80000,80000,1,'Алматы',CURRENT_TIMESTAMP)""")
        count = conn.execute('SELECT count(*) FROM canonical_products WHERE title=?',(TITLE,)).fetchone()[0]
        print('same_title_in_catalog',count)

async def main():
    seed()
    hits = barcode.find_exact_catalog_matches(TITLE)
    print('fts_exact_result', [row['id'] for row in hits])
    data = {'items': []}
    await server._apply_barcode_matches(data,CODE,{'title':TITLE,'source':'searxng'})
    print('barcode_ambiguity_persisted', data['barcode_match'], barcode.get_barcode_product_ids(CODE))
    print('barcode_startup_repair', barcode.repair_unverified_auto_links(), barcode.get_barcode_product_ids(CODE))

    app = web.Application(middlewares=[auth.auth_middleware])
    app.add_routes(server.routes)
    async with TestClient(TestServer(app)) as client:
        token = database.create_session(700000001)
        origin = str(client.make_url('')).rstrip('/')
        with patch.object(auth,'expected_origin',return_value=origin), patch.object(auth,'MAINTENANCE_DEV_MODE',False), patch.object(notifier,'save_prepared_inline_message',return_value='synthetic') as save:
            res = await client.post('/api/telegram/prepared-message',
                headers={'Origin':origin,'Cookie':f'{auth.SESSION_COOKIE}={token}'},
                json={'product_id':'legacy-almaty','city':'Астана'})
            args = save.call_args.args
            print('legacy_share', res.status, args[2], args[4], args[5])
asyncio.run(main())
