"""Codex independent migration, rollback, p95 differential, retention and WAL acceptance."""
import os,sys,tempfile,sqlite3,json,subprocess,ast,random,datetime,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph652-independent-')
for k in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[k]=''
import database as d
import telemetry as t
NOW=datetime.datetime(2026,10,29,12,tzinfo=datetime.timezone.utc)
KEY=('hour','2026-10-29T12','audit.example','audit')
# Use real previous flush implementation, without changing the checkout.
source=subprocess.check_output(['git','show','6e955a8:telemetry.py'],text=True)
cls=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='TelemetryService')
fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_flush_aggregate')
ns=dict(t.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),'<6.5.1 flush>','exec'),ns)
old_flush=ns['_flush_aggregate']
def agg(values):
 a=t._PendingAggregate();a.total=a.s2xx=len(values);a.latencies=list(values);a.latency_sum=sum(values);a.codes={'200':len(values)};return a
class Audit(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'prices.db';self.p=patch.object(d,'DB_PATH',self.path);self.p.start();d.init_db();self.s=t.TelemetryService()
 def tearDown(self):self.p.stop();self.tmp.cleanup()
 def connect(self):return self.s._connect()
 def seed(self,c,values=(900.001,901.002,902.003),key=KEY):
  c.execute('INSERT INTO telemetry_http_aggregates(bucket_type,bucket_start,host,shop,total_requests) VALUES(?,?,?,?,?)',(*key,len(values)))
  c.executemany('INSERT INTO telemetry_http_samples(bucket_type,bucket_start,host,shop,latency_ms) VALUES(?,?,?,?,?)',[(*key,v) for v in values]);c.commit()
 def test_additive_upgrade_preserves_old_rows(self):
  with self.connect() as c:
   self.seed(c);c.execute('ALTER TABLE telemetry_http_aggregates DROP COLUMN latency_samples');c.commit()
   before=[tuple(r) for r in c.execute('SELECT * FROM telemetry_http_samples')]
   d._create_schema(c.cursor());c.commit()
   self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM telemetry_http_samples')],before)
   self.assertEqual(c.execute('SELECT total_requests,latency_samples FROM telemetry_http_aggregates').fetchone()[0],3)
   self.s._flush_aggregate(c,KEY,agg([1.]));c.commit()
   self.assertEqual(c.execute('SELECT total_requests,json_array_length(latency_samples) FROM telemetry_http_aggregates').fetchone()[:],(4,4))
 def test_exact_once_and_other_key(self):
  with self.connect() as c:
   self.seed(c);self.seed(c,(7,),(*KEY[:2],'other','audit'))
   self.s._flush_aggregate(c,KEY,agg([1.23456]));c.commit()
   row=c.execute('SELECT latency_samples,total_requests FROM telemetry_http_aggregates WHERE host=?',(KEY[2],)).fetchone()
   self.assertEqual(sorted(json.loads(row[0])),[1.23,900.,901.,902.]);self.assertEqual(row[1],4)
   self.assertEqual(c.execute('SELECT count(*) FROM telemetry_http_samples').fetchone()[0],1)
   self.s._flush_aggregate(c,KEY,agg([8.]));c.commit()
   row=c.execute('SELECT latency_samples,total_requests FROM telemetry_http_aggregates WHERE host=?',(KEY[2],)).fetchone()
   self.assertEqual(len(json.loads(row[0])),5);self.assertEqual(row[1],5)
 def test_failed_upsert_restores_legacy_samples(self):
  with self.connect() as c:
   self.seed(c);c.execute("CREATE TRIGGER audit_fail BEFORE UPDATE ON telemetry_http_aggregates BEGIN SELECT RAISE(ABORT,'late aggregate failure'); END");c.commit()
   c.execute('BEGIN IMMEDIATE')
   with self.assertRaises(sqlite3.IntegrityError):self.s._flush_aggregate(c,KEY,agg([10.]))
   c.rollback()
   self.assertEqual(c.execute('SELECT count(*) FROM telemetry_http_samples').fetchone()[0],3)
   self.assertIsNone(c.execute('SELECT latency_samples FROM telemetry_http_aggregates').fetchone()[0])
   c.execute('DROP TRIGGER audit_fail');self.s._flush_aggregate(c,KEY,agg([10.]));c.commit()
   self.assertEqual(c.execute('SELECT total_requests FROM telemetry_http_aggregates').fetchone()[0],4)
 def test_cooldown_preserves_sample_and_p95(self):
  with self.connect() as c:
   self.seed(c);self.s._flush_aggregate(c,KEY,agg([1.]));c.commit()
   before=tuple(c.execute('SELECT latency_samples,latency_p95_ms,total_requests FROM telemetry_http_aggregates').fetchone())
   a=agg([]);a.cooldown=2;a.codes={'cooldown':2};self.s._flush_aggregate(c,KEY,a);c.commit()
   self.assertEqual(tuple(c.execute('SELECT latency_samples,latency_p95_ms,total_requests FROM telemetry_http_aggregates').fetchone()),before)
 def test_full_flush_failure_rolls_back_migration(self):
  # A second failing bucket must undo migration performed for the first bucket.
  with self.connect() as c:
   self.seed(c);c.execute("CREATE TRIGGER audit_fail BEFORE INSERT ON telemetry_http_aggregates WHEN NEW.host='fail' BEGIN SELECT RAISE(ABORT,'second key'); END");c.commit()
  self.s._pending_http={KEY:agg([1.]),(*KEY[:2],'fail','audit'):agg([2.])}
  self.assertFalse(self.s.flush())
  with self.connect() as c:
   self.assertEqual(c.execute('SELECT count(*) FROM telemetry_http_samples').fetchone()[0],3)
   self.assertIsNone(c.execute('SELECT latency_samples FROM telemetry_http_aggregates').fetchone()[0])
 def test_old_new_p95_differential(self):
  paths=[Path(self.tmp.name)/'old.db',self.path];errors=[]
  for p in paths:
   with patch.object(d,'DB_PATH',p):d.init_db()
  for cycle in range(15):
   values=[random.Random(cycle*1000+i).uniform(0,3000) for i in range(130)]
   rows=[]
   for idx,p in enumerate(paths):
    with patch.object(d,'DB_PATH',p),self.connect() as c:
     random.seed(652+cycle)
     (old_flush if idx==0 else t.TelemetryService._flush_aggregate)(self.s,c,KEY,agg(values));c.commit()
     r=c.execute('SELECT * FROM telemetry_http_aggregates').fetchone();rows.append(dict(r))
   self.assertEqual({k:v for k,v in rows[0].items() if k not in ('latency_samples','latency_p95_ms')},{k:v for k,v in rows[1].items() if k not in ('latency_samples','latency_p95_ms')})
   errors.append(abs(rows[0]['latency_p95_ms']-rows[1]['latency_p95_ms']))
  print({'p95_max_delta_ms':max(errors),'flushes':15,'requests':1950},flush=True)
  self.assertLessEqual(max(errors),0.01000001)
 def test_prune_all_bucket_types_boundary(self):
  with self.connect() as c:
   for typ,fmt in t.BUCKET_FORMATS.items():
    for days in (6,8):
     k=(typ,(NOW-datetime.timedelta(days=days)).strftime(fmt),'h','')
     c.execute('INSERT INTO telemetry_http_aggregates(bucket_type,bucket_start,host,shop,latency_samples,latency_p95_ms) VALUES(?,?,?,?,?,?)',(*k,'[1,2]',42))
     c.execute('INSERT INTO telemetry_http_samples(bucket_type,bucket_start,host,shop,latency_ms) VALUES(?,?,?,?,?)',(*k,1))
   c.commit()
  self.s.prune(now=NOW)
  with self.connect() as c:
   for typ,fmt in t.BUCKET_FORMATS.items():
    for days in (6,8):
     row=c.execute('SELECT latency_samples,latency_p95_ms FROM telemetry_http_aggregates WHERE bucket_type=? AND bucket_start=?',(typ,(NOW-datetime.timedelta(days=days)).strftime(fmt))).fetchone()
     if typ=='minute' and days==8:self.assertIsNone(row)
     else:self.assertEqual(tuple(row),(None if days==8 else '[1,2]',42))
 def test_wal_limit_reuse_and_pinned_reader(self):
  d.ensure_journal_mode()
  with patch.object(d,'WAL_SIZE_LIMIT_BYTES',65536),d.get_connection() as w:
   w.execute('PRAGMA wal_autocheckpoint=1');w.execute('CREATE TABLE audit_wal(x BLOB)');w.commit()
   reader=sqlite3.connect(self.path);reader.execute('BEGIN');reader.execute('SELECT * FROM audit_wal').fetchall()
   try:
    for _ in range(12):w.execute('INSERT INTO audit_wal VALUES(zeroblob(16384))');w.commit()
    pinned=os.path.getsize(str(self.path)+'-wal');self.assertGreater(pinned,65536)
   finally:reader.rollback();reader.close()
   for _ in range(3):w.execute('INSERT INTO audit_wal VALUES(zeroblob(16))');w.commit()
   reused=os.path.getsize(str(self.path)+'-wal');print({'limit':65536,'pinned_wal':pinned,'reused_wal':reused},flush=True);self.assertLessEqual(reused,65536)
if __name__=='__main__':unittest.main(verbosity=2)
