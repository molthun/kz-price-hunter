"""Independent late-failure and outer-rollback acceptance, synthetic SQLite only."""
import os,sys,tempfile,unittest,sqlite3
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph-651-atomic-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[key]=''
import database as d
from domain.adapter import prepare_legacy_offer_v2,write_legacy_offers_v2
TABLES=('sellers','seller_identities','channels','canonical_products','offers','offer_price_history')
NOW='2026-10-29T12:00:00+00:00'
def item(pid,title='Valid title',price=1000):
 return prepare_legacy_offer_v2(dict(id=pid,shop='Sulpak',city='Алматы',title=title,category='Test',price=price,url='https://example.kz/'+pid,canonical_key='shared'))
def snap(c):return {t:sorted([tuple(r) for r in c.execute('SELECT * FROM '+t)],key=repr) for t in TABLES}
class Atomicity(unittest.TestCase):
 def setUp(self):
  self.patch=patch.object(d,'DB_PATH',Path(tempfile.mkdtemp(prefix='kzph-651-atomic-case-'))/'prices.db');self.patch.start();d.init_db()
  self.ctx=d.get_connection();self.c=self.ctx.__enter__();self.c.execute('BEGIN IMMEDIATE')
 def tearDown(self):self.c.rollback();self.ctx.__exit__(None,None,None);self.patch.stop()
 def late_failure(self):
  self.c.execute("CREATE TEMP TRIGGER audit_fail BEFORE INSERT ON offer_price_history BEGIN SELECT RAISE(ABORT,'audit late history failure'); END")
 def assert_fts(self):
  self.c.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('integrity-check')")
 def test_new_batch_late_failure_leaves_no_v2_rows(self):
  self.late_failure();before=snap(self.c);seen=set()
  with self.assertRaises(sqlite3.IntegrityError):write_legacy_offers_v2(self.c,[item('a'),item('b')],NOW,seen_sellers=seen)
  self.assertEqual(snap(self.c),before);self.assertEqual(seen,set());self.assertTrue(self.c.in_transaction);self.assert_fts()
 def test_existing_rows_and_fts_restored_after_late_failure(self):
  seen=set();write_legacy_offers_v2(self.c,[item('a')],NOW,seen_sellers=seen);before=snap(self.c);known=set(seen);self.late_failure()
  with self.assertRaises(sqlite3.IntegrityError):write_legacy_offers_v2(self.c,[item('a','Changed title',2000),item('b')],NOW,seen_sellers=seen)
  self.assertEqual(snap(self.c),before);self.assertEqual(seen,known);self.assert_fts()
  self.assertEqual(self.c.execute("SELECT count(*) FROM canonical_products_fts WHERE canonical_products_fts MATCH 'Changed'").fetchone()[0],0)
 def test_success_does_not_commit_outer_transaction(self):
  before=snap(self.c);write_legacy_offers_v2(self.c,[item('a')],NOW);self.assertTrue(self.c.in_transaction)
  self.c.rollback();self.assertEqual(snap(self.c),before);self.assert_fts()
 def test_inner_savepoint_failure_preserves_outer_work(self):
  write_legacy_offers_v2(self.c,[item('a')],NOW);before=snap(self.c)
  self.c.execute('SAVEPOINT audit_outer');self.late_failure()
  with self.assertRaises(sqlite3.IntegrityError):write_legacy_offers_v2(self.c,[item('b')],NOW)
  self.c.execute('ROLLBACK TO audit_outer');self.c.execute('RELEASE audit_outer')
  self.assertEqual(snap(self.c),before);self.assert_fts()
if __name__=='__main__':unittest.main(verbosity=2)
