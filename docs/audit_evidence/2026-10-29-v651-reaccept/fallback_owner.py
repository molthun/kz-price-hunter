"""V651-01: a failed offer must not permanently own a shared canonical title."""
import ast, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph651-fallback-owner-')
for k in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[k]=''
import database as d
import domain.adapter as adapter
src=subprocess.check_output(['git','show','1b33330:database.py'],text=True)
fn=next(x for x in ast.parse(src).body if isinstance(x,ast.FunctionDef) and x.name=='_save_products_chunk')
ns=dict(d.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),'<6.5.0>','exec'),ns)
old_adapter={'__name__':'audit_old_adapter'};exec(subprocess.check_output(['git','show','1b33330:domain/adapter.py'],text=True),old_adapter)
class Audit(unittest.TestCase):
 def check(self,old):
  path=Path(tempfile.mkdtemp(prefix='kzph651-owner-case-'))/'prices.db'
  with patch.object(d,'DB_PATH',path):
   d.init_db();d.reset_dual_write_stats()
   bad=dict(id='bad',shop='Sulpak',title='Bad failed offer title',category='Bad category',price=1000,url='https://example.kz/bad',city=None,canonical_key='audit-shared')
   good=dict(bad,id='good',title='Valid offer title',category='Valid category',city='Алматы',url='https://example.kz/good')
   with d.get_connection() as c:
    c.execute('BEGIN IMMEDIATE')
    if old:
     with patch.object(adapter,'sync_legacy_product_to_v2',old_adapter['sync_legacy_product_to_v2']):ns['_save_products_chunk'](c,[bad,good],'2026-10-29T09:00:00+00:00',1000)
    else:d._save_products_chunk(c,[bad,good],'2026-10-29T09:00:00+00:00',1000)
    c.commit()
   if not old:
    # A later successful scan of the valid offer still cannot repair the title.
    with d.get_connection() as c:
     c.execute('BEGIN IMMEDIATE');d._save_products_chunk(c,[good],'2026-10-29T10:00:00+00:00',2000);c.commit()
   with d.get_connection() as c:
    row=c.execute('SELECT title,category,title_offer_id FROM canonical_products').fetchone()
    offers=[r[0] for r in c.execute('SELECT id FROM offers ORDER BY id')]
    print({'version':'6.5.0' if old else '6.5.1','canonical':tuple(row),'offers':offers,'dual_write':d.get_dual_write_stats()},flush=True)
    self.assertEqual(offers,['off_good'])
    self.assertEqual(tuple(row[:2]),('Valid offer title','Valid category'),'failed V2 item permanently owns canonical title/category')
 def test_650_control(self):self.check(True)
 def test_651_failed_item_cannot_own_title(self):self.check(False)
if __name__=='__main__':unittest.main(verbosity=2)
