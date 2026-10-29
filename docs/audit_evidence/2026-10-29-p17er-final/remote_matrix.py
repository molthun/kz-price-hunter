"""Remote backup outcomes on disposable target, no real SSH/Docker/NAS."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()))
from tests.deploy import test_prod_host_scripts as support
rows=[]
for fault in ('','corrupt','remote_no_python'):
 case=support.RemoteBackupDeliveryTest('test_healthy_remote_runs_quick_check_and_config_check_on_target');case.setUp()
 try:
  r=case.run_backup(fault);last=case.deliveries()[-1] if case.deliveries() else []
  row={'fault':fault or 'healthy','exit':r.returncode,'outcome':last[0] if last else None,'old_copies':case.old_copies(),'remote_quick_check_invoked':any('quick_check' in c[-1] for c in case.ssh_calls())}
  rows.append(row)
  if not fault:assert r.returncode==0 and last[0]=='ok'
  else:assert r.returncode!=0 and last[0]=='failed' and case.old_copies()==16
 finally:case.tearDown()
print(json.dumps(rows,ensure_ascii=False,indent=2))
