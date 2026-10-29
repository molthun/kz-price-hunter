"""A queued local call must respect off before issuing HTTP; loopback fake provider only."""
import sys,asyncio,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from tests.ai import test_local_ai as fixtures
from aiohttp import web
from aiohttp.test_utils import TestServer
import ai_service,ai_router,config
case=fixtures._DbCase();case.setUp()
async def scenario():
 entered=asyncio.Event();release=asyncio.Event();http=[];paid=[]
 async def handler(request):
  body=await request.json();http.append(body['messages'][-1]['content'])
  if len(http)==1:entered.set();await release.wait()
  return web.json_response({'choices':[{'message':{'content':'{"ok": true}'}}]})
 async def gemini(*a,**kw):paid.append('gemini');return {'ok':True}
 app=web.Application();app.router.add_post('/v1/chat/completions',handler)
 async with TestServer(app) as server:
  cfg={'enabled':True,'has_ai':True,'has_local':True,'ai_provider':'auto','local_api_base':str(server.make_url('/v1')),'local_model':'synthetic','gemini_api_key':'dummy','gemini_model_mode':'manual','gemini_model':'synthetic-paid'}
  with patch.object(ai_service,'call_gemini_api',gemini):
   first=asyncio.create_task(ai_router.run('query_parse','first',config=cfg));await entered.wait()
   second=asyncio.create_task(ai_router.run('query_parse','queued',config=cfg))
   for _ in range(100):
    if ai_service._local_slot()._waiters:break
    await asyncio.sleep(.01)
   assert ai_service._local_slot()._waiters,'second call did not queue'
   (case.data/'ai_mode.json').write_text(json.dumps({'mode':'off','by':'button'}));release.set()
   replies=await asyncio.gather(first,second)
   result={'mode_now':json.loads((case.data/'ai_mode.json').read_text())['mode'],'http_prompts':http,'paid_calls':len(paid),'providers':[r['provider'] for r in replies]}
   print(json.dumps(result,ensure_ascii=False,indent=2))
   assert http==['first'] and len(paid)==1,'Queued request reached local provider after off instead of falling back'
try:asyncio.run(scenario())
finally:case.tearDown()
