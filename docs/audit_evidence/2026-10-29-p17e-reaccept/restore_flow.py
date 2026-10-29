"""Independent reproduction of documented prepare -> start flow; temporary synthetic data only."""
import os,sys,json,tarfile,subprocess,unittest,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.deploy.test_prod_host_scripts import _ScriptCase,DEPLOY
class Audit(_ScriptCase):
 def setUp(self):
  super().setUp();self.backups=self.root/'backups';self.backups.mkdir();self.target=self.root/'restored'
  self.db=self.backups/'prices-nightly-20990101.db'
  c=sqlite3.connect(self.db)
  for table,n in (('products',3),('price_observations',5),('watches',0)):
   c.execute('CREATE TABLE '+table+'(id TEXT)');c.executemany('INSERT INTO '+table+' VALUES(?)',[(str(i),) for i in range(n)])
  c.commit();c.close()
  self.bundle=self.root/'bundle';self.bundle.mkdir()
  (self.bundle/'compose.yaml').write_text('services: {}\n')
  (self.bundle/'.env').write_text('TELEGRAM_BOT_TOKEN=synthetic\n')
  (self.bundle/'settings.json').write_text(json.dumps({'enabled_shops':['kaspi']}))
  self.archive=self.backups/'kzph-config-20990101.tar'
  with tarfile.open(self.archive,'w') as t:
   for n in ('compose.yaml','.env','settings.json'):t.add(self.bundle/n,arcname=n)
 def run_restore(self,*args):
  return self.run_script(DEPLOY/'restore_prod.sh','--from',str(self.backups),'--to',str(self.target),*args)
 def test_documented_prepare_then_start(self):
  prepared=self.run_restore();self.assertEqual(prepared.returncode,0,prepared.stderr)
  started=self.run_restore('--start')
  print(json.dumps({'case':'documented_prepare_then_start','prepare_exit':prepared.returncode,'start_exit':started.returncode,'start_stderr':started.stderr.strip(),'docker_compose_called':'"compose"' in (self.root/'calls.jsonl').read_text()},ensure_ascii=False),flush=True)
  self.assertEqual(started.returncode,0,'Documented second step cannot start the prepared restoration')
if __name__=='__main__':unittest.main(verbosity=2)
