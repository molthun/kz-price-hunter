from tests import test_support  # noqa: F401
import sqlite3,tempfile,unittest
from unittest.mock import patch
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from tests.search.test_local_catalog_growth import seed,PLAN
from repositories.catalog_expansion_repo import CatalogExpansionRepository

class ExpansionDeleteTests(unittest.TestCase):
 def setUp(self):
  self.conn=sqlite3.connect(':memory:');seed(self.conn);self.repo=CatalogExpansionRepository(self.conn)
  self.item=self.repo.propose('Ненужный запрос','Все',PLAN)
 def tearDown(self):self.conn.close()
 def test_delete_cancels_claim_and_keeps_products_categories(self):
  self.repo.review(self.item['id'],True);claim=self.repo.claim()
  self.repo.set_candidates(claim['id'],'x',['winter']);self.repo.add_match(claim['id'],'winter')
  products=self.conn.execute('SELECT * FROM canonical_products ORDER BY id').fetchall()
  categories=self.conn.execute('SELECT * FROM categories ORDER BY id').fetchall()
  self.assertTrue(self.repo.delete(claim['id']));self.assertEqual(self.repo.list(),[])
  with self.repo.lease(claim) as live:self.assertFalse(live)
  self.repo.finish(claim['id'],'complete',1,'old',generation=claim['generation'])
  self.repo.finish(claim['id'],'complete',1,'old without generation')
  self.assertEqual(self.repo.get(claim['id'])['status'],'deleted');self.assertIsNone(self.repo.claim())
  self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_expansion_matches').fetchone()[0],0)
  self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_expansion_candidates').fetchone()[0],0)
  self.assertEqual(self.conn.execute('SELECT * FROM canonical_products ORDER BY id').fetchall(),products)
  self.assertEqual(self.conn.execute('SELECT * FROM categories ORDER BY id').fetchall(),categories)
 def test_new_demand_monotonic_generation_prevents_old_worker_aba(self):
  self.repo.review(self.item['id'],True);old=self.repo.claim();self.repo.delete(old['id'])
  again=self.repo.propose('Ненужный запрос','Все',PLAN)
  self.assertEqual(again['status'],'proposed');self.assertEqual(again['demand'],1)
  self.repo.review(again['id'],True);new=self.repo.claim();self.assertGreater(new['generation'],old['generation'])
  with self.repo.lease(old) as live:self.assertFalse(live)
  with self.repo.lease(new) as live:self.assertTrue(live)
 def test_corrupt_plan_can_still_be_removed(self):
  self.conn.execute("UPDATE catalog_expansions SET plan_json='not json' WHERE id=?",(self.item['id'],));self.conn.commit()
  self.assertTrue(self.repo.delete(self.item['id']));self.assertEqual(self.repo.list(),[])
 def test_idempotent_delete_and_deleted_request_cannot_be_reviewed(self):
  self.assertFalse(self.repo.delete('unknown'));self.repo.delete(self.item['id'])
  generation=self.repo.get(self.item['id'])['generation'];self.repo.delete(self.item['id'])
  self.assertEqual(self.repo.get(self.item['id'])['generation'],generation)
  with self.assertRaises(ValueError):self.repo.review(self.item['id'],True)

class ExpansionDeleteHttpTests(unittest.IsolatedAsyncioTestCase):
 async def test_only_admin_can_delete(self):
  from web.server import delete_catalog_expansion_handler
  from auth import auth_middleware
  import auth
  with tempfile.TemporaryDirectory() as directory:
   path=directory+'/db';c=sqlite3.connect(path);seed(c);item=CatalogExpansionRepository(c).propose('Удалить','Все',PLAN);c.close()
   @web.middleware
   async def role(req,handler):
    if req.headers.get('X-Test-Role'):req['user']={'id':1,'is_admin':req.headers['X-Test-Role']=='admin'}
    return await handler(req)
   app=web.Application(middlewares=[role]);app.router.add_delete('/api/admin/catalog-expansions/{id}',delete_catalog_expansion_handler)
   with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)),patch('auth.is_admin',side_effect=lambda u:bool(u and u.get('is_admin'))):
    async with TestClient(TestServer(app)) as client:
     url='/api/admin/catalog-expansions/'+item['id']
     self.assertEqual((await client.delete(url)).status,401)
     self.assertEqual((await client.delete(url,headers={'X-Test-Role':'user'})).status,403)
     self.assertEqual((await client.delete(url,headers={'X-Test-Role':'admin'})).status,200)
     self.assertEqual((await client.delete('/api/admin/catalog-expansions/unknown',headers={'X-Test-Role':'admin'})).status,404)
