from tests import test_support  # noqa: F401
import asyncio,json,sqlite3,tempfile,unittest
from unittest.mock import patch,Mock
from tests.search.test_local_catalog_growth import seed
from domain.models import Offer,Seller,Channel
from repositories import OfferRepository,SellerRepository,ChannelRepository
import offer_details as od
from product_details import RateLimiter

class OfferDetailsTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/db';c=self.conn();seed(c)
  SellerRepository(c).save_or_update(Seller(id='s2',name='Second shop',slug='s2'))
  ChannelRepository(c).save_or_update(Channel(id='c2',seller_id='s2',channel_type='direct',name='Second channel'))
  original=c.execute("SELECT id FROM offers WHERE product_id='winter' LIMIT 1").fetchone()[0]
  self.original=original
  c.execute("UPDATE offers SET url='https://itmag.kz/p/original',image_url='https://itmag.kz/one.jpg' WHERE id=?",(original,))
  OfferRepository(c).save_or_update(Offer(id='other',product_id='winter',seller_id='s2',channel_id='c2',external_sku='other',url='https://shop.kz/p/other',image_url='https://shop.kz/two.jpg',price=20000,city='Алматы'))
  c.commit();c.close();od._cache.clear();od._inflight.clear()
 async def asyncTearDown(self):self.tmp.cleanup()
 def conn(self):return sqlite3.connect(self.path)
 async def test_context_only_selected_offer_own_image_price_history(self):
  c=self.conn();data=od.context(c,'other');self.assertIsNotNone(data)
  self.assertEqual([o['id'] for o in data['offers']],['other']);self.assertEqual(data['product']['image_url'],'https://shop.kz/two.jpg')
  self.assertEqual(data['product']['attributes'],{});self.assertEqual(data['product']['description'],'')
  self.assertEqual(data['sellers_count'],1);self.assertEqual(data['min_price'],20000)
  self.assertTrue(all(s['offer_id']=='other' for s in data['history']['series']))
  self.assertIsNone(od.context(c,'missing'));c.execute("UPDATE offers SET is_active=0 WHERE id='other'");c.commit();self.assertIsNone(od.context(c,'other'));c.close()
 async def test_each_offer_fetches_only_its_source_and_failure_never_borrows(self):
  calls=[]
  def fetch(source):
   calls.append(source['url'])
   return dict(status='ready',facts=[dict(name='Продавец',value=source['seller_name'],source_url=source['url'])],images=[source['image_url']],description='Own '+source['seller_name'])
  with patch.object(od,'fetch_source',side_effect=fetch),patch('product_details._fetch_rate',RateLimiter(30,60)):
   a,b=await asyncio.gather(od.evidence(self.conn,self.original),od.evidence(self.conn,'other'))
  self.assertEqual(len(calls),2);self.assertNotEqual(a['images'],b['images']);self.assertNotEqual(a['description'],b['description'])
  od._cache.clear()
  with patch.object(od,'fetch_source',return_value=None),patch('product_details._fetch_rate',RateLimiter(30,60)):
   result=await od.evidence(self.conn,'other')
  self.assertEqual(result['images'],['https://shop.kz/two.jpg']);self.assertEqual(result['description'],'')
 async def test_changed_url_invalidates_result_and_no_prose_is_persisted(self):
  def fetch(source):
   c=self.conn();c.execute("UPDATE offers SET url='https://shop.kz/changed' WHERE id='other'");c.commit();c.close()
   return dict(status='ready',facts=[],images=[],description='private source prose')
  with patch.object(od,'fetch_source',side_effect=fetch),patch('product_details._fetch_rate',RateLimiter(30,60)):
   self.assertIsNone(await od.evidence(self.conn,'other'))
  c=self.conn();self.assertEqual(c.execute('SELECT count(*) FROM product_source_evidence').fetchone()[0],0);c.close()
 async def test_redirect_to_another_store_is_not_used_as_seller_evidence(self):
  from product_details import UnsafeUrl
  def fetch(url,resolve):
   with self.assertRaises(UnsafeUrl):resolve('shop.kz',443)
   self.assertEqual(resolve('itmag.kz',443),['93.184.216.34'])
   return '<h1>Смартфон Samsung Galaxy S24</h1>'
  with patch('product_details.fetch_html',side_effect=fetch),patch('product_details._resolve_public',return_value=['93.184.216.34']):
   self.assertEqual(od.fetch_source(dict(url='https://itmag.kz/p/phone',source_title='Смартфон Samsung Galaxy S24',seller_name='ITMag'))['status'],'ready')
 async def test_forte_exact_id_not_first_search_hit(self):
  source=dict(external_sku='forte_123@Астана',source_title='Стиральная машина',url='https://market.forte.kz/items/123')
  response=Mock(status_code=200,content=b'{}');response.json.return_value={'hits':[{'objectID':'wrong','Picture':'https://market.forte.kz/wrong.jpg','ParamMap':{'Модель':'WRONG'}},{'objectID':'123','Picture':'https://market.forte.kz/right.jpg','ParamMap':{'Модель':'RIGHT'}}]}
  with patch('scrapers.http.post',return_value=response):r=od.forte(source)
  self.assertEqual(r['images'],['https://market.forte.kz/right.jpg']);self.assertNotIn('WRONG',r['description'])
  response.json.return_value={'hits':[{'objectID':'wrong'}]}
  with patch('scrapers.http.post',return_value=response):self.assertIsNone(od.forte(source))

class OfferHttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_real_handlers_closed_gate_same_origin_and_closing_connection(self):
  from aiohttp.test_utils import TestClient,TestServer
  from web import server
  import auth
  from database import get_connection
  with tempfile.TemporaryDirectory() as directory,patch('database.DB_PATH',__import__('pathlib').Path(directory)/'db'):
   c=get_connection();seed(c);ident=c.execute("SELECT id FROM offers WHERE product_id='winter' LIMIT 1").fetchone()[0];c.close()
   app=server.create_app();app.cleanup_ctx.clear()
   with patch.object(auth,'MAINTENANCE_DEV_MODE',True),patch.object(auth,'PUBLIC_ORIGIN',''),patch.object(auth,'get_session_user',return_value={'id':77112,'settings':{'is_approved':True}}),patch.object(od,'fetch_source',return_value=dict(status='ready',facts=[],images=[],description='Only this source')):
    async with TestClient(TestServer(app)) as client:
     self.assertEqual((await client.get('/api/v2/offers/'+ident)).status,403)
     self.assertIn('id="closedLogin"',await (await client.get('/offer/'+ident)).text())
     headers={'Cookie':'kzph_session=synthetic-only'}
     r=await client.get('/api/v2/offers/'+ident,headers=headers);self.assertEqual(r.status,200)
     self.assertEqual((await r.json())['selected_offer']['id'],ident)
     self.assertEqual((await client.get('/api/v2/offers/'+ident+'/details',headers=headers)).status,403)
     od._cache.clear()
     with patch('product_details._fetch_rate',RateLimiter(30,60)):
      r=await client.get('/api/v2/offers/'+ident+'/details',headers={**headers,'X-KZPH-Request':'1'})
     self.assertEqual(r.status,200);self.assertEqual((await r.json())['description'],'Only this source')
