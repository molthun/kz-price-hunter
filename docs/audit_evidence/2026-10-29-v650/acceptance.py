import os,sys,tempfile,unittest,datetime,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()));os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph650-independent-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[key]=''
from tests.core.test_search_platform_v2_complete import TestDatabasePlatformV2Complete
from domain.models import Seller,Condition
from repositories.seller_repo import SellerRepository
from repositories.candidate_repo import CandidateRepository
from domain.seller_onboarding import SellerOnboardingSubmission
from repositories.pricing_repo import PricingRepository
from domain.monitoring_v2 import build_monitoring_overview_v2
from domain.freshness_v2 import evaluate_offer_freshness
from domain.purchasability import offer_purchasable
from domain.search_assistant import SearchAssistant
from domain.search_core import SearchCore
from repositories.search_repo import SearchRepository

class Audit(TestDatabasePlatformV2Complete):
 def test_onboarding_domain_prefix_collision_preserves_existing_seller(self):
  sr=SellerRepository(self.conn);sr.save_or_update(Seller(id='seller_example',slug='example',name='Original retailer',domain='example.kz'))
  repo=CandidateRepository(self.conn)
  submitted=repo.submit_self_registration(SellerOnboardingSubmission(name='Unrelated Outlet',city='Алматы',website_url='https://example.com'))
  print({'case':'onboarding','matched':submitted.matched_seller_id},flush=True)
  approved=repo.approve_onboarding_candidate(submitted.candidate.id)
  original=sr.get_by_id('seller_example')
  print({'approved_id':approved.id,'original_after':original.name,'domain':original.domain},flush=True)
  self.assertEqual(original.name,'Original retailer','unmatched registration overwrote an existing seller')
 def test_history_retains_original_condition_after_offer_changes(self):
  self.conn.execute("UPDATE offer_price_history SET observed_at='2026-01-01T00:00:00+00:00' WHERE offer_id='off_4m_direct_iphone16pro'")
  self.conn.execute("UPDATE offers SET condition='USED',price=400000 WHERE id='off_4m_direct_iphone16pro'")
  self.conn.execute("INSERT INTO offer_price_history(offer_id,price,observed_at) VALUES('off_4m_direct_iphone16pro',400000,'2099-01-01T00:00:00+00:00')")
  report=PricingRepository(self.conn).get_product_price_history('prod_iphone16pro_256').to_dict()
  old=[s['condition'] for s in report['series'] if any(p['price']==499000 for p in s['points'])]
  print({'case':'history','condition_of_original_499000':old},flush=True)
  self.assertIn('NEW',old,'historical NEW price was relabelled as USED')
 def test_expired_food_is_not_purchasable(self):
  now=datetime.datetime.now(datetime.timezone.utc);seen=(now-datetime.timedelta(hours=30)).isoformat()
  row={'price':1000,'availability':'in_stock','is_active':1,'observed_at':seen,'channel_type':'food_aggregator'}
  f=evaluate_offer_freshness(seen,channel_type='food_aggregator',now=now)
  old=offer_purchasable(row,now=now)
  print({'case':'freshness','v2':f.state,'purchasable':old},flush=True)
  self.assertTrue(f.is_expired)
  self.assertFalse(old,'expired V2 food remains purchasable in real shared policy')
 def test_monitoring_total_is_not_silently_capped(self):
  base=dict(self.conn.execute("SELECT * FROM offers WHERE id='off_4m_direct_iphone16pro'").fetchone())
  for i in range(5001):
   row=dict(base,id=f'audit-bulk-{i}',external_sku=f'audit-bulk-{i}')
   self.conn.execute('INSERT INTO offers ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',tuple(row.values()))
  actual=self.conn.execute('SELECT COUNT(*) FROM offers WHERE is_active=1').fetchone()[0]
  shown=build_monitoring_overview_v2(self.conn).offer_level['total_active_offers']
  print({'case':'monitoring','actual':actual,'shown':shown},flush=True)
  self.assertEqual(shown,actual,'monitoring reports sample size as total')
 def test_assistant_does_not_invent_installment_terms(self):
  a=SearchAssistant(SearchCore(SearchRepository(self.conn)))
  r=a.ask('iPhone в рассрочку',city='Алматы')
  print({'case':'installment','best':r.best_installment_offer,'text':r.explanation},flush=True)
  self.assertIsNone(r.best_installment_offer,'channel type alone is not proof of installment availability')
if __name__=='__main__':
 suite=unittest.TestSuite(Audit(name) for name in sorted(vars(Audit)) if name.startswith('test_'))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(0 if result.wasSuccessful() else 1)
