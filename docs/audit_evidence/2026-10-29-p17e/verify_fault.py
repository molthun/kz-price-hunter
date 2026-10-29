"""Unmodified verify_public.sh must reject HTTP 500 on search; isolated command doubles only."""
import tempfile,pathlib,os,subprocess,json,shutil
script=pathlib.Path('deploy/prod-host/verify_public.sh').resolve()
wrapper='''#!/usr/bin/env python3
import pathlib,sys
cmd=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
if cmd=='sleep':pass
elif cmd=='curl':
 url=args[-1]
 if '/api/products?' in url:print('0.01',end='');sys.exit(22)
 if '/api/version' in url:print('{"version":"6.5.2"}')
 elif '/api/health' in url:print('{"status": "ok"}')
elif cmd=='docker':
 if args[0]=='exec':print('192.168.28.10')
 elif args[0]=='ps':print('Up 1 hour')
'''
with tempfile.TemporaryDirectory(prefix='kzph-p17e-verify-') as tmp:
 root=pathlib.Path(tmp)
 for cmd in ('docker','curl','sleep'):
  p=root/cmd;p.write_text(wrapper);p.chmod(0o755)
 env=dict(os.environ,PATH=str(root)+os.pathsep+os.environ['PATH'])
 r=subprocess.run(['bash',str(script)],env=env,text=True,capture_output=True)
 result={'search_http_500_simulated':True,'script_exit':r.returncode,'reported_success':'ИТОГ: прод на новой машине работает' in r.stdout,'stdout':r.stdout}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 assert r.returncode==0 and result['reported_success']
