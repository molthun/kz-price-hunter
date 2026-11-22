import sqlite3,tempfile,subprocess,json,sys
from pathlib import Path
schema='''CREATE TABLE offers(id TEXT PRIMARY KEY,product_id TEXT,seller_id TEXT,raw_payload_json TEXT);CREATE TABLE offer_sizes(offer_id TEXT,product_id TEXT,size TEXT,updated_at TEXT,UNIQUE(offer_id,size));CREATE TABLE sellers(id TEXT PRIMARY KEY,name TEXT);'''
with tempfile.TemporaryDirectory() as td:
 src=Path(td)/'old.db';dst=Path(td)/'live.db'
 for db in (src,dst):
  with sqlite3.connect(db) as c:c.executescript(schema);c.execute("INSERT INTO sellers VALUES ('s','Fixture')")
 with sqlite3.connect(src) as c:
  for name in ('missing','empty','existing','invalid','onlyold'):
   c.execute('INSERT INTO offers VALUES (?,?,?,?)',(name,'old_product','s',json.dumps({'available_sizes':['42']})))
   c.execute('INSERT INTO offer_sizes VALUES (?,?,?,?)',(name,'old_product','42','2026-11-21T00:00:00Z'))
 with sqlite3.connect(dst) as c:
  for name,payload in [('missing','{}'),('empty','{"available_sizes":[]}'),('existing','{"available_sizes":["43"]}'),('invalid','{broken')]:
   c.execute('INSERT INTO offers VALUES (?,?,?,?)',(name,'live_product','s',payload))
  c.execute("INSERT INTO offer_sizes VALUES ('existing','live_product','43','2026-11-22T00:00:00Z')")
 def snapshot():
  with sqlite3.connect(dst) as c:return [c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ('offers','offer_sizes')]
 def run(*args):
  p=subprocess.run([sys.executable,'scripts/single_live/sizes_migrate.py',str(src),str(dst),*args],capture_output=True,text=True)
  assert p.returncode==0,p.stderr
  return json.loads(p.stdout)
 before=snapshot(); dry=run('--dry-run'); assert snapshot()==before
 actual=run();assert actual['offers']==2 and actual['size_rows_added']==2,actual
 with sqlite3.connect(dst) as c:
  rows=c.execute('SELECT offer_id,product_id,size,updated_at FROM offer_sizes ORDER BY 1').fetchall()
  assert not any(r[0] in ('empty','onlyold') for r in rows)
  assert ('existing','live_product','43','2026-11-22T00:00:00Z') in rows
  assert ('missing','live_product','42','2026-11-21T00:00:00Z') in rows
  assert json.loads(c.execute("SELECT raw_payload_json FROM offers WHERE id='invalid'").fetchone()[0])['available_sizes']==['42']
 repeated=run();assert repeated['offers']==0 and repeated['size_rows_added']==0,repeated
 with sqlite3.connect(src) as c:assert c.execute('SELECT count(*) FROM offer_sizes').fetchone()[0]==5
 print(json.dumps({'dry_run_no_mutation':True,'known_empty_preserved':True,'existing_sizes_preserved':True,'current_product_id_used':True,'original_timestamp_preserved':True,'invalid_target_payload_handled':True,'source_only_offer_skipped':True,'second_run_adds_zero':True,'source_rows_unchanged':True,'first_report':actual},indent=2))
