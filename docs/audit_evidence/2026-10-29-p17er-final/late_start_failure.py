"""Restore lifecycle fault after compose up; real script, disposable synthetic DB and command doubles."""
import sys,json,sqlite3,shutil
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.deploy import test_prod_host_scripts as support
case=support.RestoreScriptTest('test_documented_prepare_then_start_flow');case.setUp()
try:
 target=case.root/'restored'
 r=case.restore('--to',str(target));assert r.returncode==0,r.stderr
 docker=case.bin/'docker';src=docker.read_text()
 src=src.replace('if args[0] == "compose":','if args[0] == "compose":\n        if "up" in args:(root/"app_running").write_text("running")')
 src=src.replace('print("3 5 0 1")','print("0 0 0 1")')
 docker.write_text(src)
 r=case.restore('--to',str(target),'--start')
 assert r.returncode!=0 and (case.root/'app_running').exists(),r.stdout+r.stderr
 state_after_failure=(target/'.restore-state').read_text().splitlines()[0]
 # The application has started and may keep writing even though post-start acceptance failed.
 c=sqlite3.connect(target/'data/prices.db');c.execute('CREATE TABLE runtime_only(x TEXT)');c.execute("INSERT INTO runtime_only VALUES('new runtime data')");c.commit();c.close()
 fresh=case.root/'manual-fresh.db';shutil.copy2(case.db,fresh)
 retry=case.restore('--to',str(target),'--db',str(fresh))
 c=sqlite3.connect(target/'data/prices.db');kept=c.execute("SELECT count(*) FROM sqlite_master WHERE name='runtime_only'").fetchone()[0]>0;c.close()
 result={'start_exit':r.returncode,'compose_up_ran':True,'state_after_poststart_failure':state_after_failure,'replacement_exit':retry.returncode,'runtime_data_preserved':kept,'app_was_stopped':False}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 assert retry.returncode!=0 and kept,'Post-start failure leaves prepared state and permits replacing a database already used by the app'
finally:case.tearDown()
