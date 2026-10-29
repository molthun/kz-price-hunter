"""Independent aliases scope across split and chain, genuine SQLite/watches only."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.ai.test_local_ai import _DbCase
import database as d,watches as w
case=_DbCase();case.setUp()
try:
 old='audit:shared';ka='k2:samsung:phone:128gb';kb='k2:samsung:phone:256gb';nexta='k2:samsung:phone128:128gb'
 for pid,title,key in (('a','A',old),('b','B',old),('c','C','audit:unrelated')):
  d.save_or_update_product({'id':pid,'title':title,'shop':'Sulpak','city':'Алматы','price':1000,'canonical_key':key,'url':'https://example.kz/'+pid})
 d.upsert_telegram_user({'id':707,'first_name':'Synthetic'})
 d.upsert_telegram_user({'id':808,'first_name':'Synthetic'})
 oldpid=d.watched_offers(['a'])[0]['canonical_product_id']
 for kind,target in (('model',old),('product',oldpid)):d.create_watch(707,{'kind':kind,'target':target,'condition':'any_drop'})
 d.rekey_titles({'A':ka,'B':kb})
 private=d.create_watch(808,{'kind':'model','target':ka,'condition':'any_drop'})
 d.rekey_titles({'A':nexta});d.rekey_titles({'A':nexta})
 offers={o['external_sku']:o for o in d.watched_offers(['a','b','c'])}
 broad={watch['kind']:{pid:w.matches(watch,offers[pid]) for pid in ('a','b','c')} for watch in d.list_watches(707)}
 narrow={pid:w.matches(private,offers[pid]) for pid in ('a','b','c')}
 print(json.dumps({'old_watches_after_split_and_chain':broad,'other_user_watch_to_128':narrow},ensure_ascii=False,indent=2))
 assert all(v=={'a':True,'b':True,'c':False} for v in broad.values())
 assert narrow=={'a':True,'b':False,'c':False}
finally:case.tearDown()
