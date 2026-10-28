import os,sys,tempfile,asyncio,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph645-audit-')
for k in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[k]=''
import database as db
from web import server
from queue_service import ProductQueueManager
from domain.search_core import SearchCore
from repositories import SearchRepository
async def main():
 db.init_db();q=ProductQueueManager()
 def p(pid):return {'id':pid,'title':pid+' Laptop','shop':'Audit','price':100,'url':'https://example.invalid','category':'laptops'}
 for prods in [[p('remaining'),p('retired')],[p('remaining')]]:
  receipt=await q.enqueue(prods,'Audit',{'detect_market_arbitrage':False},shop_key='audit',reconcile={'key':'audit','url':'https://example.invalid/category','complete':True})
  item=await q.dequeue();await server._execute_queue_item(item);await q.ack(item)
 with db.get_connection() as c:
  legacy=c.execute("SELECT is_active FROM products WHERE id='retired'").fetchone()[0]
  v2=[dict(r) for r in c.execute("SELECT id,is_active,availability FROM offers WHERE external_sku='retired'")]
  response=SearchCore(SearchRepository(c)).search('retired Laptop')
 watch=db.watched_offers(['retired'])
 db.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
 db.create_watch(987654321,{'kind':'product','target':watch[0]['canonical_product_id'],'condition':'target_price','threshold':200,'quiet_from':'','quiet_to':''})
 events=db.evaluate_watches(watch)
 result={'legacy_active':legacy,'v2':v2,'search':[{'id':x.product.id,'purchasable':x.has_purchasable_offers,'min_price':x.min_price} for x in response.items],'watch_available':[r['is_available'] for r in watch],'notification_events':events}
 Path(__file__).with_name('results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
asyncio.run(main())
