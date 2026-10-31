"""Independent new moderation paths; local HTTP/SQLite, no external requests."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.test_disputed_matching_moderation import TestDisputedMatchingWebAPI
from unittest.mock import patch,AsyncMock
import database as db
import product_key as pk
import product_verify,config

class Residuals(TestDisputedMatchingWebAPI):
 async def test_live_search_consumes_daily_attempts(self):
  await self._login_dev_admin()
  a,b='Apple EarPods Lightning','Apple EarPods USB-C'
  db.save_title_fields_batch({a:{'brand':'Apple','model':'EarPods Lightning'},b:{'brand':'Apple','model':'EarPods USB-C'}},'k2','local','audit')
  db.enqueue_disputed_pair(a,b,'audit');ph=pk.pair_hash(a,b)
  fake=AsyncMock(return_value=None)
  with patch.object(config,'get_ai_config',return_value={'search_api_base':'http://offline.invalid','search_daily_limit':1,'search_interval_seconds':0}),patch.object(product_verify,'searxng_search',fake):
   for _ in range(2):
    res=await self.client.get('/api/admin/matching/search',params={'pair_hash':ph,'fetch_live':'1'})
    self.assertEqual(res.status,200)
  print('DAILY_LIMIT',1,'EXTERNAL_ATTEMPTS',fake.await_count,'ACCOUNTED',db.web_searches_today())
  self.assertLessEqual(fake.await_count,1,'manual search bypasses daily attempt quota')

 async def test_scan_reaches_other_conflict_after_human_same(self):
  a,b,c='Samsung Galaxy S24 128GB a','Samsung Galaxy S24 256GB b','Samsung Galaxy S24 512GB c'
  names=[a,b,c];key='k2:samsung:galaxys24'
  for i,t in enumerate(names):
   db.save_or_update_product({'id':str(i),'title':t,'canonical_key':key,'shop':'s','city':'Алматы','price':1000,'url':'https://example.kz/p'})
  db.save_cached_canonical_keys_batch({t:key for t in names})
  db.save_title_fields_batch({t:{'brand':'Samsung','model':'Galaxy S24','variants':[{'value':n,'unit':'gb'}],'unbranded':False} for t,n in zip(names,[128,256,512])},'k2','local','audit')
  db.enqueue_disputed_pair(a,b,'operator override');self.assertTrue(db.resolve_disputed_pair_human(pk.pair_hash(a,b),'same','audit')['ok'])
  queued=[]
  for _ in range(2):
   res=db.find_and_enqueue_conflicting_canonical_keys(10);print('SCAN_AFTER_RESOLVED_SAME',res);queued.append(res['enqueued'])
  self.assertGreater(sum(queued),0,'first resolved pair hides unresolved conflict with third member')

if __name__=='__main__':
 suite=unittest.TestSuite(Residuals(name) for name in ('test_live_search_consumes_daily_attempts','test_scan_reaches_other_conflict_after_human_same'))
 result=unittest.TextTestRunner(verbosity=2).run(suite);sys.exit(not result.wasSuccessful())
