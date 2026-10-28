import os,sys,tempfile,json,sqlite3,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph-full644-')
for k in ('TELEGRAM_BOT_TOKEN','GEMINI_API_KEY','OPENAI_API_KEY'):os.environ[k]=''
import database as db
from repositories import init_schema_v2,ProductRepository,SellerRepository,ChannelRepository,OfferRepository,SearchRepository
from domain.models import Seller,Channel,CanonicalProduct,Offer
from domain.search_core import SearchCore
out=[]
def record(name,**kw):out.append(dict(name=name,**kw))
db.init_db()
c=sqlite3.connect(':memory:');init_schema_v2(c)
SellerRepository(c).save_or_update(Seller(id='s',slug='audit',name='Audit'))
ChannelRepository(c).save_or_update(Channel(id='c',seller_id='s',channel_type='website',name='Web'))
now=datetime.datetime.now(datetime.timezone.utc)
for n,price in enumerate((1,100)):
 ProductRepository(c).save_or_update(CanonicalProduct(id=f'p{n}',canonical_key=f'k{n}',title='Audit Laptop',category='laptops'))
 OfferRepository(c).save_or_update(Offer(id=f'o{n}',product_id=f'p{n}',seller_id='s',channel_id='c',external_sku=str(n),url='https://example.invalid',price=price,observed_at=now.isoformat()))
c.execute("UPDATE offers SET observed_at='',updated_at='2020-01-01T00:00:00+00:00' WHERE id='o0'");c.commit()
core=SearchCore(SearchRepository(c))
record('null_observed_stale_updated',limit1=[{'id':x.product.id,'min':x.min_price} for x in core.search('Audit Laptop',sort_by='price_asc',limit=1,now=now).items],limit50=[{'id':x.product.id,'min':x.min_price} for x in core.search('Audit Laptop',sort_by='price_asc',limit=50,now=now).items])
# Accepted old task fails, newer task succeeds, then administrator retries the old task.
def product(price):return {'id':'retry-old','title':'Audit Laptop','shop':'Audit','price':price,'url':'https://example.invalid','category':'laptops'}
for ident,price in [('old',100),('new',200)]:db.spool_admit(ident,'audit','Audit',ident,{'prods':[product(price)]})
old=db.spool_claim('audit-worker');db.spool_nack('old',old['claim_token'],'synthetic',max_retries=0)
new=db.spool_claim('audit-worker');db.spool_apply_chunk('new',new['claim_token'],0,[product(200)]);db.spool_finish_core('new',new['claim_token'],['retry-old'],None,None);db.spool_ack('new',new['claim_token'])
db.spool_resolve_failures('retry',['old']);retry=db.spool_claim('audit-worker');db.spool_apply_chunk('old',retry['claim_token'],0,[product(100)])
with db.get_connection() as con:price=con.execute("SELECT current_price FROM products WHERE id='retry-old'").fetchone()[0]
record('old_task_retry_overwrites_new',expected_latest=200,actual=price)
# Export must include the full user's event history, not only the UI page limit.
db.upsert_telegram_user({'id':987654321,'first_name':'Synthetic'})
w=db.create_watch(987654321,{'kind':'search','target':'Audit','condition':'any_drop','quiet_from':'','quiet_to':''})
with db.get_connection() as con:
 for n in range(1001):
  con.execute("INSERT INTO watch_events(watch_id,product_id,price,reason,created_at) VALUES (?,?,?,?,?)",(w['id'],f'synthetic-{n}',100,'audit',now.isoformat()))
 con.commit()
record('privacy_export_truncated',stored=1001,exported=len(db.export_user_data(987654321)['watch_events']))
Path(__file__).with_name('results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(out,ensure_ascii=False))
