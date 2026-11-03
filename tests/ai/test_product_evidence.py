from tests import test_support  # noqa: F401
import asyncio,json,sqlite3,tempfile,time,unittest
from unittest.mock import patch
import product_evidence as pe
import product_classification as pc
from tests.ai.test_product_classification_tags import seed,body
from repositories.classification_repo import ClassificationRepository
from product_details import RateLimiter

URL='https://itmag.kz/p/example/'
TITLE='Оперативная память Kingston KF560C30BBEAK2-64'
def page(name=TITLE):
 return '<h1>'+name+'</h1><script type="application/ld+json">'+json.dumps({'@type':'Product','name':name,'sku':'KF560C30BBEAK2-64' if name==TITLE else 'OTHER9000','description':'Source prose must stay only in RAM.','image':['/one.jpg?w=100','/one.jpg?w=400','/two.jpg'],'additionalProperty':[{'name':'Тип памяти','value':'DDR5'},{'name':'Объём','value':'64 ГБ'},{'name':'Внимание!','value':'Do not copy this notice'}]})+'</script>'

class EvidenceParserTests(unittest.TestCase):
 def test_identity_specs_gallery_and_no_duplicate_sizes(self):
  r=pe.extract(page(),URL,TITLE)
  self.assertEqual(r['status'],'ready');self.assertEqual(len(r['images']),2)
  self.assertTrue(any(x['name']=='Тип памяти' and x['value']=='DDR5' for x in r['facts']))
  self.assertFalse(any(x['name']=='Внимание!' for x in r['facts']))
  self.assertFalse(any(x['name']=='Название в магазине' for x in r['facts']))
  self.assertTrue(all(x['source_url']==URL for x in r['facts']))
 def test_sanitize_facts_html_entities_and_excluded_names(self):
  raw=[
   {'name':'Название в магазине','value':'Смартфон Apple NanoSIM&#43;eSIM'},
   {'name':'SIM-карта&#43;','value':'NanoSIM&#43;eSIM бордовый'},
   {'name':'Экран &amp; матрица','value':'OLED &quot;Super Retina&quot;'},
   {'name':'Цена','value':'100 000'},
   {'name':'Внимание!','value':'текст'},
  ]
  cleaned=pe.sanitize_facts(raw)
  self.assertEqual(len(cleaned),2)
  self.assertEqual(cleaned[0]['name'],'SIM-карта+')
  self.assertEqual(cleaned[0]['value'],'NanoSIM+eSIM бордовый')
  self.assertEqual(cleaned[1]['name'],'Экран & матрица')
  self.assertEqual(cleaned[1]['value'],'OLED "Super Retina"')
 def test_other_product_and_recommendations_are_not_accepted(self):
  r=pe.extract(page('SSD Samsung OTHER9000'),URL,TITLE)
  self.assertEqual(r['status'],'identity_unconfirmed');self.assertEqual(r['images'],[])
  r=pe.extract('<h1>Другой товар</h1><script type="application/ld+json">'+json.dumps({'@type':'ItemList','itemListElement':[{'@type':'Product','name':TITLE}]})+'</script>',URL,TITLE)
  self.assertEqual(r['status'],'identity_unconfirmed')
 def test_capacity_and_colour_variants_are_not_the_same_product(self):
  self.assertFalse(pe.identity('Apple iPhone 16 128GB Black','Apple iPhone 16 256GB Black'))
  self.assertFalse(pe.identity('Apple iPhone 16 128GB Black','Apple iPhone 16 128GB White'))
 def test_main_heading_wins_over_matching_recommendation_product(self):
  html=page().replace('<h1>'+TITLE+'</h1>','<h1>SSD Samsung OTHER9000</h1>')
  self.assertEqual(pe.extract(html,URL,TITLE)['status'],'identity_unconfirmed')
 def test_unrelated_itemprop_images_not_added_to_gallery(self):
  html=page()+'<aside><img itemprop="image" src="/unrelated.jpg"></aside>'
  self.assertEqual(len(pe.extract(html,URL,TITLE)['images']),2)
 def test_bare_sku_matches_exact_source_sku_and_itmag_gallery(self):
  html=page()+''.join('<a class="detail-gallery-big__link" href="/'+x+'.jpg"></a>' for x in ['three','four'])
  r=pe.extract(html,URL,'(KF560C30BBEAK2-64)')
  self.assertEqual(r['status'],'ready');self.assertEqual(len(r['images']),4)
 def test_unsafe_urls_and_limits(self):
  for u in ['javascript:alert(1)','data:image/png,xx','https://user:pass@itmag.kz/p','https://itmag.kz:123/a','https://[oops']:
   self.assertEqual(pe.safe_url(u),'')
  self.assertEqual(pe.safe_url('/img.jpg',URL),'https://itmag.kz/img.jpg')
 def test_source_type_contradiction_cannot_auto_apply_and_laptop_is_not_memory_module(self):
  facts=[dict(name='Тип памяти',value='DDR5'),dict(name='Форм-фактор памяти',value='DIMM')]
  self.assertEqual(pc.source_constraint(facts),'ram')
  decision=pc.decision(dict(category_id='storage',confidence=1,reason='wrong'),[dict(id='storage',name='SSD',parent_id='e'),dict(id='ram',name='Оперативная память',parent_id='e')],'SSD',pc.source_constraint(facts))
  self.assertFalse(decision['apply'])
  self.assertIsNone(pc.source_constraint(facts+[dict(name='Процессор',value='Intel Core i5')]))
 def test_no_photo_is_invented(self):
  r=pe.extract('<h1>'+TITLE+'</h1>',URL,TITLE)
  self.assertEqual(r['images'],[])

class EvidencePersistenceTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=self.tmp.name+'/test.db'
  c=self.conn();seed(c,title=TITLE);c.execute('UPDATE offers SET url=?',(URL,));c.commit();c.close()
  pe._cache.clear();pe._inflight.clear()
 async def asyncTearDown(self):self.tmp.cleanup()
 def conn(self):return sqlite3.connect(self.path,timeout=30)
 async def test_singleflight_prose_not_stored_facts_version_requeues_manual_protected(self):
  c=self.conn();repo=ClassificationRepository(c)
  with c:item=repo.detail('p');repo.act('p',body(item),'v1')
  title=c.execute('SELECT title FROM canonical_products').fetchone()[0];price=c.execute('SELECT price FROM offers').fetchone()[0];c.close()
  with patch('product_details.fetch_html',return_value=page()) as fetch,patch('product_details._fetch_rate',RateLimiter(30,60)):
   a,b=await asyncio.gather(pe.enrich(self.conn,'p',True),pe.enrich(self.conn,'p',True))
  self.assertEqual(fetch.call_count,1);self.assertEqual(a,b);self.assertIn('Source prose',a['description'])
  c=self.conn();raw=' '.join(str(v) for row in c.execute('SELECT * FROM product_source_evidence') for v in row)
  self.assertNotIn('Source prose',raw)
  detail=ClassificationRepository(c).detail('p');self.assertTrue(detail['tag']['locked']);self.assertEqual(detail['job']['status'],'review')
  self.assertEqual(c.execute('SELECT title FROM canonical_products').fetchone()[0],title)
  self.assertEqual(c.execute('SELECT price FROM offers').fetchone()[0],price);c.close()
 async def test_old_source_result_rejected_after_title_change(self):
  def fetch(url):
   c=self.conn();c.execute("UPDATE canonical_products SET title='SSD Samsung OTHER9000'");c.commit();c.close();return page()
  with patch('product_details.fetch_html',side_effect=fetch),patch('product_details._fetch_rate',RateLimiter(30,60)):
   self.assertIsNone(await pe.enrich(self.conn,'p'))
  c=self.conn();self.assertEqual(c.execute('SELECT count(*) FROM product_source_evidence').fetchone()[0],0);c.close()
 async def test_rate_limit_deferred_without_fake_failed_verification(self):
  limit=RateLimiter(1,60);limit.retry_after('details')
  with patch('product_details._fetch_rate',limit),patch('product_details.fetch_html') as fetch:
   r=await pe.enrich(self.conn,'p');self.assertEqual(r['status'],'deferred');fetch.assert_not_called()
  c=self.conn();self.assertEqual(c.execute('SELECT count(*) FROM product_source_evidence').fetchone()[0],0);c.close()
 async def test_failure_keeps_previous_facts_and_original_check_time(self):
  with patch('product_details.fetch_html',return_value=page()),patch('product_details._fetch_rate',RateLimiter(30,60)):
   first=await pe.enrich(self.conn,'p')
  pe._cache.clear();c=self.conn();before=c.execute('SELECT checked_at FROM product_source_evidence').fetchone()[0];c.close()
  with patch('product_details.fetch_html',return_value=None),patch('product_details._fetch_rate',RateLimiter(30,60)):
   result=await pe.enrich(self.conn,'p',True)
  self.assertEqual(result['facts'],first['facts']);self.assertEqual(result['checked_at'],before)
  c=self.conn();self.assertEqual(c.execute('SELECT checked_at FROM product_source_evidence').fetchone()[0],before);c.close()
 async def test_new_facts_invalidate_old_classification_claim(self):
  c=self.conn();repo=ClassificationRepository(c)
  with c:repo.touch(['p'])
  request=repo.claim('v1');c.close()
  with patch('product_details.fetch_html',return_value=page()),patch('product_details._fetch_rate',RateLimiter(30,60)):
   await pe.enrich(self.conn,'p')
  c=self.conn()
  with c:self.assertFalse(ClassificationRepository(c).finish(request,dict(apply=True,category_id='storage',reason='old'),'qwen'))
  c.close()

class EvidenceHttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_details_require_same_origin_header_and_ignore_client_source_urls(self):
  from aiohttp import web
  from aiohttp.test_utils import TestClient,TestServer
  from unittest.mock import AsyncMock
  from web.server import product_source_details_handler
  import auth
  app=web.Application();app.router.add_get('/api/v2/products/{product_id}/details',product_source_details_handler)
  result=dict(status='ready',facts=[dict(name='Тип',value='Память')],images=[],description='live only')
  with patch.dict('os.environ',{'PRODUCT_ENRICHMENT_ENABLED':'1'}),patch.object(auth,'PUBLIC_ORIGIN',''),patch('product_evidence.enrich',new=AsyncMock(return_value=result)) as enrich:
   async with TestClient(TestServer(app)) as client:
    self.assertEqual((await client.get('/api/v2/products/p/details')).status,403)
    self.assertEqual((await client.get('/api/v2/products/p/details',headers={'X-KZPH-Request':'1','Origin':'https://evil.invalid'})).status,403)
    response=await client.get('/api/v2/products/p/details?url=http://127.0.0.1/private',headers={'X-KZPH-Request':'1'})
    self.assertEqual(response.status,200);self.assertEqual((await response.json())['description'],'live only')
    self.assertEqual(enrich.await_count,1);self.assertEqual(enrich.call_args.args[1],'p')
