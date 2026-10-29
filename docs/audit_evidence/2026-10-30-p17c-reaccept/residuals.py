"""Independent residual migration checks: unknown-first batches and persisted watch identity."""
import sys,json,asyncio,unittest
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.ai.test_local_ai import _DbCase
import database as d,product_key as pk,watches
from scripts import migrate_to_k2 as mig

def f(cap=None):return {'brand':'Samsung','model':'SM-S911B','sku':None,'variants':[] if cap is None else [{'value':cap,'unit':'gb'}],'unbranded':False}
def emit(case,**kw):print(json.dumps({'case':case,**kw},ensure_ascii=False),flush=True)
class Audit(_DbCase):
 def test_unknown_first_batches_keep_128_and_256_separate(self):
  fs={'Unknown capacity':f(),'Phone 128':f(128),'Phone 256':f(256)}
  for i,(title,fields) in enumerate(fs.items()):
   d.save_or_update_product({'id':f'phone_{i}','shop':'Sulpak','city':'Алматы','title':title,'price':1000,'url':f'https://example.kz/{i}','canonical_key':f'old:phone{i}'})
   d.save_cached_canonical_keys_batch({title:f'old:phone{i}'})
   with d.get_connection() as c:c.execute('UPDATE title_canonical_cache SET created_at=? WHERE title=?',(f'2026-01-01 00:00:0{i}',title));c.commit()
  d.save_title_fields_batch(fs,'k2','local','fake')
  asyncio.run(mig.run_migration(str(d.DB_PATH),'http://unused','fake',batch_size=1,rescore_only=True))
  keys=d.get_cached_canonical_keys_batch(list(fs));emit('unknown_first_migration',keys=keys)
  self.assertNotEqual(keys['Phone 128'],keys['Phone 256'],'unknown-first batch must not erase capacity and merge 128/256')
 def test_rekey_keeps_persisted_product_and_model_watches(self):
  title='Audit watched product';old='audit:device:128gb';new='k2:samsung:sms911b:128gb'
  d.save_or_update_product({'id':'watched_1','shop':'Sulpak','city':'Алматы','title':title,'price':1000,'url':'https://example.kz/watch','canonical_key':old})
  before=d.watched_offers(['watched_1'])[0];pid=before['canonical_product_id']
  d.upsert_telegram_user({'id':707,'first_name':'Synthetic'})
  product_watch=d.create_watch(707,{'kind':'product','target':pid,'condition':'any_drop','city':'Алматы'})
  model_watch=d.create_watch(707,{'kind':'model','target':old,'condition':'any_drop','city':'Алматы'})
  before_matches=[watches.matches(w,before) for w in (product_watch,model_watch)];self.assertEqual(before_matches,[True,True])
  d.rekey_titles({title:new})
  after=d.watched_offers(['watched_1'])[0];saved=d.list_watches(707);after_matches=[watches.matches(w,after) for w in saved]
  emit('watches_after_rekey',old_pid=pid,new_pid=after['canonical_product_id'],persisted_targets=[{'kind':w['kind'],'target':w['target']} for w in saved],before_matches=before_matches,after_matches=after_matches)
  self.assertEqual(after_matches,[True,True],'existing watches must follow rekey or resolve through a persistent alias')
if __name__=='__main__':unittest.main(verbosity=2)
