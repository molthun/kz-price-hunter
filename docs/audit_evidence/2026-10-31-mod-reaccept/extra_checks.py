"""Local API quota competition and zero-limit cache behavior; no live search."""
import runpy,asyncio,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
old=runpy.run_path(str(Path(__file__).resolve().parents[1]/'2026-10-31-loss-codex/moderation_residuals.py'))
db,pk,config,pv=old['db'],old['pk'],old['config'],old['product_verify']
class Extra(old['Residuals']):
 async def test_parallel_manual_and_background_share_budget(self):
  await self._login_dev_admin()
  a,b='Apple EarPods Lightning','Apple EarPods USB-C'
  db.save_title_fields_batch({a:{'brand':'Apple','model':'EarPods Lightning'},b:{'brand':'Apple','model':'EarPods USB-C'}},'k2','local','audit')
  db.enqueue_disputed_pair(a,b,'audit');ph=pk.pair_hash(a,b)
  fake=AsyncMock(return_value=None)
  async def manual():
   r=await self.client.get('/api/admin/matching/search',params={'pair_hash':ph,'fetch_live':'1'});self.assertEqual(r.status,200)
  with patch.object(config,'get_ai_config',return_value={'search_api_base':'http://offline.invalid','search_daily_limit':1,'search_interval_seconds':0}),patch.object(pv,'searxng_search',fake):
   results=await asyncio.gather(manual(),manual(),asyncio.to_thread(db.reserve_web_search,'background',1))
  self.assertEqual(fake.await_count+int(results[2]),1)
  self.assertEqual(db.web_searches_today(),1)
  print('MANUAL_AND_BACKGROUND_TOTAL_ATTEMPTS',fake.await_count+int(results[2]))
 async def test_zero_limit_serves_cache_without_external_calls(self):
  await self._login_dev_admin()
  a,b='Apple EarPods Lightning','Apple EarPods USB-C';ph=pk.pair_hash(a,b)
  db.save_title_fields_batch({a:{'brand':'Apple','model':'EarPods Lightning'},b:{'brand':'Apple','model':'EarPods USB-C'}},'k2','local','audit')
  db.enqueue_disputed_pair(a,b,'audit');q='Apple EarPods Lightning';hit=[{'title':'cached','url':'https://example.kz/p','content':'cache'}];db.save_web_search(q,hit)
  fake=AsyncMock(return_value=None)
  with patch.object(config,'get_ai_config',return_value={'search_api_base':'http://offline.invalid','search_daily_limit':0}),patch.object(pv,'searxng_search',fake):
   r=await self.client.get('/api/admin/matching/search',params={'pair_hash':ph,'fetch_live':'1'});self.assertEqual(r.status,200);body=await r.json()
  self.assertEqual(body['results'][q],hit);fake.assert_not_awaited();self.assertEqual(db.web_searches_today(),0)
if __name__=='__main__':
 names=['test_live_search_consumes_daily_attempts','test_scan_reaches_other_conflict_after_human_same','test_parallel_manual_and_background_share_budget','test_zero_limit_serves_cache_without_external_calls']
 result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(Extra(n) for n in names));raise SystemExit(not result.wasSuccessful())
