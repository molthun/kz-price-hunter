"""Independent remote-target fault: same-size damaged DB, no real SSH/Docker/data."""
import os,sys,json,sqlite3,subprocess
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.deploy import test_prod_host_scripts as support
case=support.BackupDeliveryScriptTest('test_healthy_delivery_verifies_target_and_records_ok');case.setUp()
try:
 (case.prod/'backup.conf').write_text(f'CONTAINER=kz-price-hunter\nBACKUP_TARGET=fake@nas:{case.target}\nKEEP_LOCAL=2\nKEEP_REMOTE=14\n')
 # Execute remote commands locally against the disposable target; retain genuine shell flow.
 ssh=case.bin/'ssh';ssh.write_text('#!'+sys.executable+'\nimport subprocess,sys\nraise SystemExit(subprocess.call(sys.argv[-1],shell=True))\n');ssh.chmod(0o755)
 rsync=case.bin/'rsync';text=rsync.read_text();text=text.replace('args = sys.argv[1:]','args = sys.argv[1:]\nif name == "rsync" and ":" in args[-1]:args[-1]=args[-1].split(":",1)[1]');rsync.write_text(text)
 r=case.run_backup('corrupt');new=case.target/'prices-nightly-20991231T000000000000Z-abc123.db'
 valid=False
 try:
  c=sqlite3.connect('file:'+str(new)+'?mode=ro',uri=True);valid=c.execute('PRAGMA quick_check').fetchone()[0]=='ok';c.close()
 except sqlite3.Error:pass
 calls=[json.loads(x) for x in (case.root/'calls.jsonl').read_text().splitlines()]
 result={'exit':r.returncode,'target_quick_check_ok':valid,'last_delivery_outcome':case.deliveries()[-1][0] if case.deliveries() else None,'old_backups_remaining':case.old_copies(),'reported_done':'готово:' in r.stdout,'remote_quick_check_called':any(x[0]=='ssh' and 'quick_check' in x[-1] for x in calls)}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 assert r.returncode!=0 and result['last_delivery_outcome']=='failed','Remote delivery accepted corrupted same-size database and recorded success'
finally:case.tearDown()
