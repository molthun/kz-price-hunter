"""API acceptance on a temporary SQLite file, using the existing isolated harness."""
import json
import sqlite3
import unittest

from tests.web.test_seller_discovery_api import TestSellerDiscoveryApi
from domain.social_content import ContentObservation, ExtractedOfferCandidate
from domain.social_pipeline import SocialOfferPipeline


class AcceptanceApi(TestSellerDiscoveryApi):
    async def test_review_endpoint_rights_and_state_preservation(self):
        route='/api/v2/admin/discovery/candidates/cand_apple_city/review'
        denied=await self.anon_client.post(route)
        self.assertIn(denied.status,(401,403))
        response=await self.admin_client.post(route)
        self.assertEqual(response.status,200)
        self.assertEqual((await response.json())['candidate']['status'],'REVIEW_REQUIRED')
        repeated=await self.admin_client.post(route)
        self.assertEqual(repeated.status,409)
        approved=await self.admin_client.post('/api/v2/admin/onboarding/cand_apple_city/decision',json={'decision':'APPROVED'})
        self.assertEqual(approved.status,200)
        self.assertEqual((await approved.json())['candidate']['status'],'INDEXED')
        profile=await self.admin_client.post('/api/v2/admin/discovery/candidates/cand_apple_city/profile')
        self.assertEqual((await profile.json())['candidate']['status'],'INDEXED')

    async def test_domain_filter_total_and_empty_page(self):
        response=await self.admin_client.get('/api/v2/admin/discovery/candidates?domain=applecity.kz&limit=1&offset=0')
        data=await response.json()
        self.assertEqual((data['total'],len(data['candidates'])),(1,1))
        response=await self.admin_client.get('/api/v2/admin/discovery/candidates?domain=applecity.kz&limit=1&offset=1')
        data=await response.json()
        self.assertEqual((data['total'],data['candidates']),(1,[]))

    async def test_social_history_visible_via_real_api(self):
        c=sqlite3.connect(self.db_path)
        try:
            pipeline=SocialOfferPipeline(c)
            saved=None
            for post,price,date in [('1',100000,'2026-10-30T10:00:00+00:00'),('2',90000,'2026-10-31T10:00:00+00:00')]:
                obs=ContentObservation(post_id=post,published_at=date,raw_metadata={'platform':'telegram','handle':'audit_history'})
                saved=pipeline.process_candidates(obs,[ExtractedOfferCandidate(raw_title='Apple iPhone 15 128GB',price=price,city='Алматы')])[0]
            c.commit()
        finally:c.close()
        response=await self.anon_client.get(f'/api/v2/products/{saved.product_id}/history')
        data=await response.json()
        print('SOCIAL_HISTORY_API='+json.dumps(data,ensure_ascii=False))
        self.assertEqual(response.status,200)
        self.assertTrue(data['ok'])
        encoded=json.dumps(data)
        self.assertIn('100000',encoded)
        self.assertIn('90000',encoded)
        self.assertIn('Алматы',json.dumps(data,ensure_ascii=False))


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(AcceptanceApi)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
