"""Synthetic local HTTP/queue smoke; temporary DB, no providers or external sources."""
import os,sys,tempfile,asyncio,time,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph-load645-')
for k in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN','PUBLIC_ORIGIN'):os.environ[k]=''
from aiohttp.test_utils import TestServer,TestClient
from web import server
from queue_service import product_queue as q
import database
async def main():
 app=server.create_app();app.cleanup_ctx.clear();client=TestClient(TestServer(app));await client.start_server()
 lat={'/api/live':[],'/api/stats':[]};errors=[];ids=[]
 with patch.object(server,'check_market_arbitrage',return_value=None):
  worker=asyncio.create_task(server.single_db_writer_worker());await asyncio.sleep(0)
  async def producer():
   for batch in range(8):
    prods=[{'id':f'load-{batch}-{n}','title':f'Audit Widget {batch}-{n}','shop':'Audit','category':'general','price':100,'url':'https://example.invalid'} for n in range(500)]
    receipt=await q.enqueue(prods,'Audit',{},shop_key='load');ids.append(receipt.task_id)
  async def probe():
   start=time.monotonic()
   while time.monotonic()-start<8:
    for path in lat:
     t=time.monotonic()
     try:
      r=await asyncio.wait_for(client.get(path),3);await r.read()
      if r.status!=200:errors.append({'path':path,'status':r.status})
     except Exception as e:errors.append({'path':path,'error':type(e).__name__})
     lat[path].append(time.monotonic()-t)
    await asyncio.sleep(.1)
  await asyncio.gather(producer(),probe());drained=await q.wait_drained(ids,timeout=120)
  worker.cancel();await asyncio.gather(worker,return_exceptions=True)
 await client.close()
 with database.get_connection() as c:count=c.execute("SELECT count(*) FROM products WHERE id LIKE 'load-%'").fetchone()[0]
 result={'profile':'8 batches x 500 synthetic products; 8s HTTP probes; thresholds live p95<250ms, stats p95<2s','drained':drained,'stored':count,'errors':errors}
 for path,values in lat.items():
  values.sort();result[path]={'requests':len(values),'p95_seconds':round(values[min(len(values)-1,int(len(values)*.95))],4),'max_seconds':round(max(values),4)}
 print('LOAD_RESULT '+json.dumps(result),flush=True)
asyncio.run(main())
