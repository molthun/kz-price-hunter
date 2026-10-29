"""Exercise the unchanged deployment shell against disposable commands/data; no production access."""
import os,sys,tempfile,pathlib,subprocess,json,textwrap,sqlite3,shutil
SCRIPT=pathlib.Path('deploy/prod-host/backup_to_nas.sh').resolve()
DISPATCH='''#!/usr/bin/env python3
import os,sys,pathlib,sqlite3,shutil,re,json
name=pathlib.Path(sys.argv[0]).name;root=pathlib.Path(os.environ['CASE_ROOT']);args=sys.argv[1:]
if name=='docker':
 if 'python3' in args:
  p=root/'data/backups/prices-nightly-20990101.db';p.parent.mkdir(parents=True,exist_ok=True)
  c=sqlite3.connect(p);c.execute('CREATE TABLE products(id TEXT)');c.execute("INSERT INTO products VALUES('synthetic')");c.commit();c.close()
  (root/'local_backup_heartbeat').write_text('nightly: ok');print('/app/data/backups/'+p.name)
 else:pass
elif name=='rsync':
 src=pathlib.Path(args[-2]);dst=pathlib.Path(args[-1])/src.name
 mode=os.environ.get('FAULT','')
 if mode=='transfer_failure':sys.exit(23)
 if mode=='corrupt_destination':dst.write_bytes(b'broken transfer')
 else:shutil.copy2(src,dst)
elif name=='stat':
 print(pathlib.Path(args[-1]).stat().st_size)
'''
results=[]
for fault in ('healthy','transfer_failure','corrupt_destination'):
 with tempfile.TemporaryDirectory(prefix='kzph-p17e-fault-') as temp:
  root=pathlib.Path(temp);bin=root/'bin';bin.mkdir();dest=root/'destination';dest.mkdir()
  shutil.copy2(SCRIPT,root/'backup_to_nas.sh')
  (root/'backup.conf').write_text('CONTAINER=fake\nBACKUP_TARGET='+str(dest)+'\nKEEP_LOCAL=2\nKEEP_REMOTE=14\n')
  for cmd in ('docker','rsync','stat'):
   p=bin/cmd;p.write_text(DISPATCH);p.chmod(0o755)
  env=dict(os.environ,CASE_ROOT=str(root),FAULT=fault,PATH=str(bin)+os.pathsep+os.environ['PATH'])
  r=subprocess.run(['bash',str(root/'backup_to_nas.sh')],env=env,capture_output=True,text=True)
  files=list(dest.glob('*.db'));valid=False
  if files:
   try:
    c=sqlite3.connect('file:'+str(files[0])+'?mode=ro',uri=True);valid=c.execute('PRAGMA quick_check').fetchone()[0]=='ok';c.close()
   except sqlite3.Error:pass
  result={'case':fault,'script_exit':r.returncode,'target_copies':len(files),'target_quick_check_ok':valid,'local_backup_heartbeat':(root/'local_backup_heartbeat').read_text(),'source_and_target_same_device':os.stat(root).st_dev==os.stat(dest).st_dev,'reported_done':'готово:' in r.stdout}
  results.append(result)
  if fault=='healthy':assert r.returncode==0 and valid
  if fault=='transfer_failure':assert r.returncode==23 and not files and result['local_backup_heartbeat']=='nightly: ok'
  if fault=='corrupt_destination':assert r.returncode==0 and not valid and result['reported_done']
print(json.dumps(results,indent=2,ensure_ascii=False))
