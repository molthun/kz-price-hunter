"""Independent deterministic comparison to the actual 6.5.0 implementation, synthetic DBs only."""
import ast, json, os, random, subprocess, sys, tempfile, time, sqlite3, threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path.cwd()))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph651-independent-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'): os.environ[key]=''
import database as d
import domain.adapter as adapter
import config
source = subprocess.check_output(['git','show','1b33330:database.py'], text=True)
node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name=='_save_products_chunk')
ns = dict(d.__dict__)
exec(compile(ast.Module(body=[node], type_ignores=[]), '<6.5.0 _save_products_chunk>', 'exec'), ns)
old_write = ns['_save_products_chunk']
old_adapter = {'__name__':'audit_old_adapter'}
exec(subprocess.check_output(['git','show','1b33330:domain/adapter.py'], text=True), old_adapter)
root = Path(os.environ['DATA_DIR'])
paths = [root/'old.db',root/'new.db']
for path in paths:
 with patch.object(d,'DB_PATH',path): d.init_db()
rng = random.Random(651029)
chunks=[]
for cycle in range(24):
 chunk=[]
 for _ in range(35):
  i=rng.randrange(70)
  chunk.append(dict(id=f'audit_{i}',shop=rng.choice(['Sulpak','Kaspi Магазин','DNS Казахстан']),
   city=rng.choice(['Алматы','Астана']),title=f'Audit model {i%9}',category='Тест',url=f'https://audit{i%3}.example.kz/p/{i}',
   image_url=rng.choice(['',f'https://img.example.kz/{i}.jpg']),description=rng.choice(['',f'Description {cycle}']),
   price=rng.choice([0,1,10000+rng.randrange(10000),10000001]),old_price_on_site=rng.choice([None,0,25000]),
   canonical_key=f'audit-key-{i%9}',condition=rng.choice(['NEW','USED']),availability=rng.choice(['in_stock','out_of_stock'])))
 chunks.append(chunk)
for idx,path in enumerate(paths):
 with patch.object(d,'DB_PATH',path):
  for k,chunk in enumerate(chunks):
   # Include stale and unmarked observations, duplicates and changing dimensions.
   seq=[1000+k,1000-k,None][k%3]
   with d.get_connection() as c:
    c.execute('BEGIN IMMEDIATE')
    if idx==0:
     with patch.object(adapter,'sync_legacy_product_to_v2',old_adapter['sync_legacy_product_to_v2']):
      old_write(c,chunk,f'2026-10-29T08:{k:02d}:00+00:00',seq)
    else: d._save_products_chunk(c,chunk,f'2026-10-29T08:{k:02d}:00+00:00',seq)
    c.commit()
  with d.get_connection() as c:
   c.execute("INSERT INTO products_fts(products_fts) VALUES('integrity-check')")
   c.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('integrity-check')")
   c.commit()
cons=[sqlite3.connect(p) for p in paths]
for table in ['products','price_observations','offers','offer_price_history','canonical_products','sellers','seller_identities','channels']:
 cols=[r[1] for r in cons[0].execute(f'PRAGMA table_info({table})')]
 if table=='canonical_products': cols=[c for c in cols if c not in ('title','category','title_offer_id')]
 # Autoincrement identity is internal; compare persisted business data in sorted order.
 if table in ('price_observations','offer_price_history'): cols=[c for c in cols if c!='id']
 sql='SELECT '+','.join('"'+c+'"' for c in cols)+' FROM '+table
 snapshots=[sorted(c.execute(sql).fetchall(),key=repr) for c in cons]
 assert snapshots[0]==snapshots[1], f'{table}: old/new mismatch'
 print(table,len(snapshots[0]),'SAME',flush=True)
for c in cons:c.close()
print('24 chunks / 840 input rows / actual 6.5.0 vs 6.5.1: PASS',flush=True)
# Reproduce write contention with six pure-Python threads; bounded, synthetic data only.
stop=threading.Event()
def burn():
 n=1
 while not stop.is_set(): n=(n*1664525+1013904223)&0xffffffff
previous=sys.getswitchinterval()
try:
 config.apply_gil_switch_interval()
 threads=[threading.Thread(target=burn) for _ in range(6)]
 for t in threads:t.start()
 with patch.object(d,'DB_PATH',paths[1]):
  chunk=[dict(id=f'load_{i}',title=f'Audit load {i}',url=f'https://load.example.kz/{i}',price=10000+i,shop='Sulpak') for i in range(100)]
  t=time.perf_counter(); prepared=d._prepare_products_chunk(chunk); prep=time.perf_counter()-t
  with d.get_connection() as c:
   t=time.perf_counter(); c.execute('BEGIN IMMEDIATE'); d._save_products_chunk(c,chunk,'2026-10-29T09:00:00+00:00',3000,prepared=prepared); c.commit(); held=time.perf_counter()-t
 print(json.dumps({'cpu_threads':6,'products':100,'switch_seconds':sys.getswitchinterval(),'prepare_seconds':prep,'transaction_seconds':held}),flush=True)
finally:
 stop.set()
 for t in threads:t.join()
 sys.setswitchinterval(previous)
