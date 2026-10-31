"""Independent acceptance edges. Temporary SQLite only; assertions describe desired behavior."""
import json
import pathlib
import sqlite3
import tempfile
import unittest

import database as db
from domain.social_connector import FetchedPost
from domain.social_content import ContentObservation, ExtractedOfferCandidate
from domain.social_pipeline import SocialOfferPipeline
from domain.search_core import SearchCore
from repositories import init_schema_v2
from repositories.search_repo import SearchRepository
from repositories.social_source_repo import SocialSourceRepository
from scripts.scrub_social_content import scrub
from social_ingest import ingest_source
from tests.sellers import test_fa69r_fixes as fixtures

NOTES={}


class OperatorEdges(fixtures.OperatorDecisionTests):
    def test_same_keeps_newer_numeric_post_at_equal_time(self):
        db.save_cached_canonical_keys_batch({fixtures.B:self.common+'.other'})
        db.rekey_titles({fixtures.B:self.common+'.other'})
        first=self.pipeline.process_candidates(self.observation('10'),[fixtures.cand(1000,fixtures.A)])[0]
        second=self.pipeline.process_candidates(self.observation('9'),[fixtures.cand(1500,fixtures.B)])[0]
        self.c.commit()
        self.decide('same')
        active=self.c.execute("SELECT price,raw_payload_json FROM offers WHERE external_sku LIKE 'SOC-%' AND is_active=1").fetchall()
        NOTES['same_equal_time']=[{'price':r[0],'post':json.loads(r[1])['source_post_id']} for r in active]
        self.assertEqual([r[0] for r in active],[1000],'Merging cards must preserve Telegram post 10 over post 9')


class UnitAndCleanup(unittest.TestCase):
    def test_slash_quantity_preserves_price_basis(self):
        c=sqlite3.connect(':memory:');init_schema_v2(c)
        for handle,caption in [('audit100','Говядина 1000 ₸/100 г'),('auditkg','Говядина 6000 ₸/1 кг')]:
            src=SocialSourceRepository(c).add_source('telegram',handle)
            posts=[FetchedPost(post_id='1',text=caption,published_at='2030-01-01T00:00:00+00:00')]
            connector=type('Fixture',(),{'fetch':lambda self,h,posts=posts:posts})()
            result=ingest_source(src,connector=connector,conn=c)
            self.assertEqual(result.status,'OK')
        offers=[o for i in SearchCore(SearchRepository(c)).search('говядина').items for o in i.offers]
        NOTES['slash_quantity']=[{'price':o.price,'unit':o.unit,'quantity':o.quantity,'product_id':o.product_id} for o in offers]
        self.assertTrue(all(o.unit for o in offers),'A price explicitly per 100 g must not become a unitless offer')

    def test_scrub_wal_has_no_residual_original_content(self):
        with tempfile.TemporaryDirectory(prefix='kzph-scrub-wal-') as tmp:
            path=pathlib.Path(tmp)/'prices.db'
            c=sqlite3.connect(path)
            c.execute('PRAGMA journal_mode=WAL')
            init_schema_v2(c)
            marker='SYNTHETIC_FOREIGN_DESCRIPTION_FOR_WAL_AUDIT'
            c.execute("INSERT INTO content_observations(id,seller_id,channel_id,text,media_urls,observed_at,raw_metadata) VALUES ('old','s','c',?,'[]','2026-01-01','{}')",(marker,))
            c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            reader=sqlite3.connect(path)
            reader.execute('BEGIN')
            reader.execute('SELECT text FROM content_observations').fetchone()
            try:
                changed=scrub(c,apply=True)
                logical=c.execute('SELECT text FROM content_observations').fetchone()[0]
                files=[p.name for p in (path,pathlib.Path(str(path)+'-wal')) if p.exists() and marker.encode() in p.read_bytes()]
                NOTES['scrub_wal']={'changed':changed,'logical_text':logical,'marker_in_files':files}
                self.assertFalse(files,'A successful physical scrub must not leave the original in DB/WAL files')
            finally:reader.rollback();reader.close();c.close()


if __name__=='__main__':
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (OperatorEdges,UnitAndCleanup)])
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print('EDGE_NOTES='+json.dumps(NOTES,ensure_ascii=False))
    raise SystemExit(not result.wasSuccessful())
