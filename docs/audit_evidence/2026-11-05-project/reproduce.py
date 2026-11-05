"""Offline audit probes. Run from repo with PYTHONPATH=. and a temporary DATA_DIR."""
import json
import sqlite3
from unittest.mock import patch, MagicMock

from domain.attribute_indexing import extract_title_attributes, sync_all_source_attributes
from domain.catalog_enrichment_ai import batch_enrich_catalog, DDL_ATTRIBUTES
from repositories import init_schema_v2
from repositories.search_repo import SearchRepository
import product_evidence


def db():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    init_schema_v2(c)
    c.executescript(DDL_ATTRIBUTES)
    c.executescript(product_evidence.DDL)
    return c


def add(c, pid, title, category='gpus', attrs='{}', model=None):
    c.execute('INSERT INTO canonical_products(id,canonical_key,title,category,attributes_json,model,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
              (pid,pid,title,category,attrs,model,'2026-11-05','2026-11-05'))


c = db()
for i in range(15):
    add(c, f'a{i:02}', 'Неизвестный товар', 'other')
add(c, 'z16', 'Смартфон Apple iPhone 15 256GB Black', 'smartphones')
with patch('domain.catalog_enrichment_ai.extract_attributes_with_local_ai', return_value={}):
    runs = [batch_enrich_catalog(c, limit=15, use_ai=True, pause_between_ai_calls=0) for _ in range(3)]
assert all(r['empty'] == 15 for r in runs)
assert c.execute("SELECT attributes_json FROM canonical_products WHERE id='z16'").fetchone()[0] == '{}'
print('STARVATION', json.dumps(runs))

c = db()
add(c, 'gpu', 'Видеокарта GeForce RTX 4060 8GB')
c.execute("INSERT INTO product_source_evidence VALUES(?,?,?,?,?,?,?,?,?)", ('gpu','sig','hash',json.dumps([{'name':'Объем видеопамяти','value':'16 ГБ'}]),'[]','[]','ready',0,0))
sync_all_source_attributes(c)
actual = dict(c.execute("SELECT key,value FROM product_search_attributes WHERE product_id='gpu'"))
assert actual['vram'] == '8 ГБ'
print('EVIDENCE_OVERWRITTEN', json.dumps(actual, ensure_ascii=False))

for title in ['Ноутбук ASUS ROG Strix 18" Core Ultra 9 / 64 ГБ / SSD 1024 ГБ / GeForce RTX 5080', 'Ноутбук Lenovo 16GB DDR5 512GB SSD RTX 4060', 'Блок питания ATX 850W 80 PLUS Gold', 'SSD накопитель M.2 2230 1TB']:
    print('TITLE_EXTRACTION', title, json.dumps(extract_title_attributes(title), ensure_ascii=False))

c = db()
add(c,'base','Видеокарта RTX 4060 8GB',model='RTX 4060')
add(c,'ti','Видеокарта RTX 4060 Ti 8GB',model='GeForce RTX 4060 Ti')
sql, args = SearchRepository._model_filter('GeForce RTX 4060')
ids = [r[0] for r in c.execute('SELECT p.id FROM canonical_products p WHERE 1=1 '+sql,args)]
assert ids == ['base','ti']
print('MODEL_FILTER_4060_MATCHES_4060_TI', ids)

# AI output absent from title is accepted without supporting facts.
from domain.catalog_enrichment_ai import extract_attributes_with_local_ai
response = MagicMock()
response.__enter__.return_value.status = 200
response.__enter__.return_value.read.return_value = json.dumps({'response':json.dumps({'ram':'64 GB','storage':'2 TB'})}).encode()
with patch('domain.catalog_enrichment_ai.can_use_local_ai',return_value=(True,'OK')), patch('urllib.request.urlopen',return_value=response):
    attrs = extract_attributes_with_local_ai('Неизвестный товар',ollama_url='http://127.0.0.1:1')
assert attrs == {'ram':'64 ГБ','storage':'2 ТБ'}
print('UNSUPPORTED_AI_FACTS_ACCEPTED', json.dumps(attrs,ensure_ascii=False))

# A writer remains locked during the next slow model request.
import tempfile
with tempfile.TemporaryDirectory(prefix='kzph-audit-lock-') as td:
    disk = sqlite3.connect(td+'/probe.db', timeout=0.05)
    disk.executescript('CREATE TABLE canonical_products(id TEXT PRIMARY KEY,title TEXT,category TEXT,description TEXT,attributes_json TEXT,updated_at TEXT);')
    disk.executescript(DDL_ATTRIBUTES)
    disk.executemany('INSERT INTO canonical_products VALUES(?,?,?,?,?,?)', [('a','Товар один','other','','{}',''),('b','Товар два','other','','{}','')])
    disk.commit()
    events=[]
    def slow_ai(*args, **kwargs):
        other = sqlite3.connect(td+'/probe.db',timeout=0.05)
        try:
            other.execute("UPDATE canonical_products SET updated_at='probe' WHERE id='b'")
            other.rollback()
            events.append('writer_available')
        except sqlite3.OperationalError as e:
            events.append(str(e))
        finally:
            other.close()
        return {'ram':'16 ГБ'}
    with patch('domain.catalog_enrichment_ai.extract_attributes_with_local_ai',side_effect=slow_ai):
        batch_enrich_catalog(disk,limit=2,pause_between_ai_calls=0)
    disk.close()
    assert events == ['writer_available','database is locked']
    print('WRITE_LOCK_DURING_AI',events)

# Zero daily limit must stop requests, but the web search converts it to 300.
import asyncio
from domain.web_search import search_web
import aiohttp


async def quota_probe():
    response = MagicMock()
    response.status = 200
    from unittest.mock import AsyncMock
    response.json = AsyncMock(return_value={'results': []})
    response_ctx = MagicMock()
    response_ctx.__aenter__ = AsyncMock(return_value=response)
    response_ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get.return_value = response_ctx
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)
    with patch('config.get_ai_config',return_value={'search_daily_limit':0,'search_api_base':'http://127.0.0.1:1'}), patch('database.get_web_search',return_value=None), patch('database.reserve_web_search',return_value=True) as reserve, patch.object(aiohttp,'ClientSession',return_value=session_ctx):
        result = await search_web('test')
        assert reserve.call_args.args[1] == 300
        assert session.get.called
        print('ZERO_QUOTA_BECOMES',reserve.call_args.args[1],'REQUEST_SENT',session.get.called)


asyncio.run(quota_probe())

# The new enrichment path does not consult configured AI enablement or endpoint.
response = MagicMock()
response.__enter__.return_value.status = 200
response.__enter__.return_value.read.return_value = b'{"response":"{}"}'
with patch('config.get_ai_config', return_value={'enabled':False,'ai_search_enabled':False,'local_api_base':'http://configured.invalid/v1','local_model':'configured-model'}) as cfg, patch('domain.catalog_enrichment_ai.can_use_local_ai',return_value=(True,'OK')), patch('urllib.request.urlopen',return_value=response) as call:
    extract_attributes_with_local_ai('Неизвестный товар')
    assert not cfg.called
    assert call.called
    print('AI_SETTINGS_IGNORED', 'config_read',cfg.called,'request_sent',call.called)
