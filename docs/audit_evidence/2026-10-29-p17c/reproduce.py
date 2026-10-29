"""Independent P17-C acceptance, synthetic full-schema databases and fake external providers only."""
import sys,asyncio,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from tests.ai.test_local_ai import _DbCase
import database as d,product_key as pk,product_verify as pv,ai_service,local_ai
from scripts import migrate_to_k2 as mig

def fields(cap=None,model='SM-S911B'):
 return {'brand':'Samsung','model':model,'sku':None,'variants':[] if cap is None else [{'value':cap,'unit':'gb'}],'unbranded':False}
def emit(case,**data):print(json.dumps({'case':case,**data},ensure_ascii=False),flush=True)
class Audit(_DbCase):
 def test_C01_variant_bridge_does_not_merge_128_and_256(self):
  fs={'phone A':fields(128),'phone B':fields(),'phone C':fields(256)}
  base={t:pk.build_key(f) for t,f in fs.items()};linked=pk.resolve_keys_with_articles(fs)
  emit('variant_bridge',base=base,linked=linked)
  self.assertNotEqual(linked['phone A'],linked['phone C'],'missing variant must not bridge contradictory capacities')
 def test_C01_different_verdict_vetoes_article_link(self):
  fs={'A':fields(None,'Galaxy S24'),'B':fields(None,'Galaxy S24 Ultra')}
  for f in fs.values():f['sku']='ABC123456'
  entries={t:(f,pk.build_key(f)) for t,f in fs.items()};ph=pv.pair_hash('A','B')
  linked=pk.link_by_article(entries,pair_verdicts={ph:'different'})
  emit('different_veto',base={t:k for t,(_,k) in entries.items()},linked=linked)
  self.assertNotEqual(linked['A'],linked['B'],'explicit different verdict must prevent union')
 def test_C02_migration_updates_v2(self):
  titles=['Audit phone shop A','Audit phone shop B'];old={t:f'audit:old{i}:128gb' for i,t in enumerate(titles)}
  for i,t in enumerate(titles):d.save_or_update_product({'id':f'audit_{i}','shop':['Sulpak','DNS Казахстан'][i],'city':'Алматы','title':t,'price':1000+i,'canonical_key':old[t],'url':f'https://example.kz/{i}'})
  d.save_cached_canonical_keys_batch(old);d.save_title_fields_batch({t:fields(128) for t in titles},'k2','local','fake')
  asyncio.run(mig.run_migration(str(d.DB_PATH),'http://unused','fake',rescore_only=True,check_pairs=True))
  with d.get_connection() as c:
   legacy=[tuple(r) for r in c.execute('SELECT title,canonical_key FROM products ORDER BY id')]
   v2=[tuple(r) for r in c.execute('SELECT o.external_sku,p.canonical_key FROM offers o JOIN canonical_products p ON p.id=o.product_id ORDER BY o.external_sku')]
  emit('v2_migration',legacy=legacy,v2=v2)
  self.assertEqual({r[1] for r in v2},{r[1] for r in legacy},'V2 canonical identity must agree after migration')
 def test_C02_verified_union_updates_v2(self):
  titles=['Verified pair A','Verified pair B']
  for i,t in enumerate(titles):d.save_or_update_product({'id':f'verified_{i}','shop':['Sulpak','DNS Казахстан'][i],'city':'Алматы','title':t,'price':1000+i,'canonical_key':f'audit:before{i}:128gb','url':f'https://example.kz/{i}'})
  d.unify_canonical_keys(*titles,'k2:samsung:sms911b:128gb')
  with d.get_connection() as c:
   legacy=[r[0] for r in c.execute('SELECT canonical_key FROM products ORDER BY id')]
   v2=[r[0] for r in c.execute('SELECT p.canonical_key FROM offers o JOIN canonical_products p ON p.id=o.product_id')]
  emit('verified_union',legacy=legacy,v2=v2)
  self.assertEqual(set(v2),set(legacy),'same verdict must not leave V2 identities unchanged')
 def test_C03_empty_searches_consume_daily_quota(self):
  calls=[]
  async def search(*a,**kw):calls.append(a[1]);return []
  async def ask(prompt):raise AssertionError('no results should not call model')
  async def run():
   for i in range(3):
    await pv.verify_pair_cached(f'A{i}',fields(None,'Model123'),f'B{i}',fields(None,'Model456'),'http://fake',ask,daily_limit=1,min_interval=0)
  with patch.object(pv,'searxng_search',search):asyncio.run(run())
  emit('search_limit',network_attempts=len(calls),daily_counter=d.web_searches_today(),limit=1)
  self.assertLessEqual(len(calls),1,'empty responses still count as real search attempts')
 def test_C04_temporary_search_failure_is_retried(self):
  d.save_title_fields_batch({'A':fields(128),'B':fields(256)},'k2','local','fake');d.enqueue_disputed_pair('A','B','audit')
  async def search(*a,**kw):return None
  async def ask(p):return {'same':False,'confidence':1,'evidence':['https://example.kz']}
  with patch.object(pv,'searxng_search',search):first=asyncio.run(pv.process_disputed_queue_tick('http://fake',ask,min_interval=0))
  requeued=d.enqueue_disputed_pair('A','B','again');pending=d.get_pending_disputed_pairs()
  with d.get_connection() as c:status=c.execute('SELECT status FROM disputed_product_pairs').fetchone()[0]
  emit('queue_retry',first=first['decision'],status=status,requeue_result=requeued,pending=len(pending))
  self.assertTrue(pending,'temporary failure must remain retryable')
 def test_C05_verifier_respects_local_off(self):
  (self.data/'ai_mode.json').write_text('{"mode":"off"}');calls=[]
  async def provider(*a,**kw):calls.append(1);return {'same':True}
  with patch.object(ai_service,'_call_local_api',provider):reply=asyncio.run(pv.make_local_ask({'local_api_base':'http://fake/v1','local_model':'fake','ai_search_enabled':False})('prompt'))
  emit('verifier_pause',paused=bool(local_ai.pause_reason()),outgoing_calls=len(calls),reply=reply)
  self.assertEqual(calls,[],'verification must not bypass local off/global AI switch')
 def test_C06_rescore_rebuilds_existing_k2(self):
  title='Audit existing k2';d.save_cached_canonical_keys_batch({title:'k2:samsung:wrong'});d.save_title_fields_batch({title:fields(128)},'k2','local','fake')
  result=asyncio.run(mig.run_migration(str(d.DB_PATH),'http://unused','fake',rescore_only=True))
  key=d.get_cached_canonical_keys_batch([title])[title]
  emit('rescore',result=result,key=key,expected=pk.build_key(fields(128),title))
  self.assertEqual(key,pk.build_key(fields(128),title),'rescore must rebuild already-k2 titles from saved fields')
 def test_C07_pairs_include_equal_multishop_sets(self):
  res=mig.check_pairs_comparison({'A':'old:group','B':'old:group'},{'A':'k2:a:1','B':'k2:a:2'},{'A':['shop1','shop2'],'B':['shop1','shop2']})
  emit('pair_metric',result=res)
  self.assertGreater(res['dropped_pairs_count'],0,'cross-shop pairs exist even with identical multishop sets')
if __name__=='__main__':unittest.main(verbosity=2)
