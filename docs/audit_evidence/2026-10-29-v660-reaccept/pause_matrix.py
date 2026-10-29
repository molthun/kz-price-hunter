"""Independent queued-pause policy cases, loopback fake provider and temporary DB only."""
import sys,json,asyncio
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from tests.ai import test_local_ai as fixtures
from aiohttp import web
from aiohttp.test_utils import TestServer
import ai_service,ai_router
async def scenario(case,selection,budget):
 entered=asyncio.Event();release=asyncio.Event();http=[];paid=[]
 async def model(request):
  p=(await request.json())['messages'][-1]['content'];http.append(p)
  if p=='first':entered.set();await release.wait()
  return web.json_response({'choices':[{'message':{'content':'{"ok": true}'}}]})
 async def gemini(*a,**kw):paid.append(1);return {'ok':True}
 app=web.Application();app.router.add_post('/v1/chat/completions',model)
 async with TestServer(app) as server:
  cfg={'enabled':True,'has_ai':True,'has_local':True,'ai_provider':selection,'local_api_base':str(server.make_url('/v1')),'local_model':'fake','gemini_api_key':'dummy','gemini_model_mode':'manual','gemini_model':'fake-paid'}
  with patch.object(ai_service,'call_gemini_api',gemini),patch.object(ai_router,'budget_allows',return_value=budget):
   first=asyncio.create_task(ai_router.run('query_parse','first',config=cfg));await entered.wait()
   second=asyncio.create_task(ai_router.run('query_parse','queued',config=cfg))
   for _ in range(100):
    if ai_service._local_slot()._waiters:break
    await asyncio.sleep(.01)
   assert ai_service._local_slot()._waiters
   (case.data/'ai_mode.json').write_text('{"mode":"off"}');release.set()
   r=await asyncio.gather(first,second,return_exceptions=True)
   assert http==['first']
   fallback=selection=='auto' and budget
   if fallback:assert r[1]['provider']=='gemini' and len(paid)==1
   else:assert isinstance(r[1],ai_router.AIUnavailable) and not paid
   (case.data/'ai_mode.json').write_text('{"mode":"on"}')
   resumed=await ai_router.run('query_parse','resumed',config=cfg)
   assert resumed['provider']=='local' and http==['first','resumed']
   return {'selection':selection,'paid_budget':budget,'queued_outcome':'gemini' if fallback else 'AIUnavailable','http_before_resume':1,'paid_calls':len(paid),'resume':'local'}
rows=[]
for selection,budget in (('auto',True),('local',True),('auto',False)):
 case=fixtures._DbCase();case.setUp()
 try:rows.append(asyncio.run(scenario(case,selection,budget)))
 finally:case.tearDown()
print(json.dumps(rows,ensure_ascii=False,indent=2))
