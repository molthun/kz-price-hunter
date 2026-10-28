import asyncio,json,subprocess,tempfile,os,time
from queue_service import ProductQueueManager, MiniRedisClient, QUEUE_KEY
port=16389
folder=tempfile.mkdtemp(prefix='audit-redis-')
def start():
 p=subprocess.Popen(['redis-server','--port',str(port),'--bind','127.0.0.1','--save','','--appendonly','no','--dir',folder],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 time.sleep(.2)
 return p
async def main():
 p=start()
 try:
  client=MiniRedisClient(port=port);await client.connect()
  qm=ProductQueueManager();qm.redis_client=client;qm.use_redis=True
  await qm.enqueue([{'id':'in-flight'}],'Synthetic',{})
  item=await qm.dequeue()
  replacement=ProductQueueManager();replacement.redis_client=client;replacement.use_redis=True
  print(json.dumps({'name':'redis_destructive_pop','retrieved':bool(item),'new_worker_next':await replacement.dequeue(.01),'original_in_flight':len(qm._in_flight)}),flush=True)
  await qm.enqueue([{'id':'pending-at-restart'}],'Synthetic',{})
  before=await client.llen(QUEUE_KEY)
  await client.close();p.terminate();p.wait(timeout=5);p=start()
  other=MiniRedisClient(port=port);await other.connect()
  print(json.dumps({'name':'redis_restart_pending_loss','before':before,'after':await other.llen(QUEUE_KEY)}),flush=True)
  await other.close()
 finally:
  p.terminate();p.wait(timeout=5)
asyncio.run(main())
