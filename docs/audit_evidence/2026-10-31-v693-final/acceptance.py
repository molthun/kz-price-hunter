"""Independent acceptance of the allowed 'physical incomplete' result and a safe retry.
Temporary SQLite only. No working data, actual models or external requests.
"""
import base64
import hashlib
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from domain.vision_extraction import VisionExtractionEngine
from repositories import init_schema_v2
from repositories.product_repo import ProductRepository
from scripts.scrub_social_content import scrub

NOTES={}


class Acceptance(unittest.TestCase):
    TITLE='Samsung Galaxy S24'
    MARKER='SYNTHETIC_INDEPENDENT_OVERFLOW_POST'
    IMAGE_MARKER='SYNTHETIC_INDEPENDENT_IMAGE_BYTES'

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='kzph-693-accept-');self.addCleanup(self.tmp.cleanup)
        self.path=pathlib.Path(self.tmp.name)/'prices.db'
        self.c=sqlite3.connect(self.path,timeout=.05);self.addCleanup(self.c.close)
        self.c.execute('PRAGMA journal_mode=WAL');init_schema_v2(self.c)
        self.product=ProductRepository(self.c).get_or_create_canonical('audit:kept',self.TITLE,'smartphones')
        self.image=base64.b64encode((self.IMAGE_MARKER*1000).encode()).decode()
        for oid,text,meta in [('long','A'*10000+self.MARKER+'B'*10000,{'post_url':'https://t.me/audit/1'}),
                              ('duplicate',self.TITLE,{'post_url':'https://t.me/audit/1'}),
                              ('image','',{'post_url':'https://t.me/audit/1','image_bytes':self.image})]:
            self.c.execute("INSERT INTO content_observations(id,seller_id,channel_id,text,media_urls,observed_at,raw_metadata) VALUES (?,'s','c',?,'[]','2026-01-01',?)",(oid,text,json.dumps(meta)))
        self.c.commit();self.c.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def markers(self):
        return [p.name for p in (self.path,pathlib.Path(str(self.path)+'-wal')) if p.exists()
                and any(m.encode() in p.read_bytes() for m in (self.MARKER,self.image[:200]))]

    def verify_logical_and_catalog(self):
        fresh=sqlite3.connect(self.path)
        try:
            self.assertEqual(fresh.execute('SELECT title FROM canonical_products WHERE id=?',(self.product.id,)).fetchone()[0],self.TITLE)
            rows=fresh.execute('SELECT text,media_urls,raw_metadata FROM content_observations').fetchall()
            self.assertEqual(len(rows),3)
            for text,media,meta in rows:
                self.assertEqual((text,media),('','[]'))
                self.assertEqual(json.loads(meta),{'post_url':'https://t.me/audit/1'})
            self.assertEqual(fresh.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(fresh.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:fresh.close()

    def test_offline_cleanup_preserves_allowed_values_and_backup(self):
        result=scrub(self.c,True)
        self.assertEqual((int(result),result.physical_complete,result.reason),(3,True,''))
        self.verify_logical_and_catalog();self.assertFalse(self.markers())
        backup=sqlite3.connect(':memory:')
        try:
            self.c.backup(backup);dump='\n'.join(backup.iterdump())
            self.assertNotIn(self.MARKER,dump);self.assertNotIn(self.image[:200],dump);self.assertIn(self.TITLE,dump)
        finally:backup.close()
        NOTES['offline']={'rows':3,'physical_complete':True,'catalog_preserved':True,'backup_clean':True}

    def test_wal_incomplete_then_retry_with_zero_dirty_rows(self):
        reader=sqlite3.connect(self.path);reader.execute('BEGIN');reader.execute('SELECT text FROM content_observations').fetchall()
        try:
            result=scrub(self.c,True)
            self.assertEqual(int(result),3);self.assertIs(result.physical_complete,False);self.assertEqual(result.reason,'wal_busy')
            self.verify_logical_and_catalog();self.assertTrue(self.markers(),'Pinned old pages are preserved, not overwritten behind SQLite')
            reader.rollback()
            finished=scrub(self.c,True)
            self.assertEqual((int(finished),finished.physical_complete,finished.reason),(0,True,''))
            self.assertFalse(self.markers());self.verify_logical_and_catalog()
            NOTES['wal_retry']={'initial_reason':result.reason,'initial_complete':False,'retry_rows':0,'retry_complete':True}
        finally:reader.rollback();reader.close()

    def test_dry_run_does_not_mutate_data_or_file(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=scrub(self.c,False)
        self.assertEqual(int(result),3);self.assertIsNone(result.physical_complete)
        self.assertEqual(before,hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertTrue(self.markers())

    def test_cli_exit_3_then_zero_after_reader_released(self):
        self.c.close()
        reader=sqlite3.connect(self.path);reader.execute('BEGIN');reader.execute('SELECT text FROM content_observations').fetchall()
        code="""import sqlite3,sys
from scripts.scrub_social_content import main
real=sqlite3.connect
sqlite3.connect=lambda path:real(path,timeout=.05)
sys.argv=['scrub',sys.argv[1],'--apply']
raise SystemExit(main())
"""
        try:
            first=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,text=True)
            self.assertEqual(first.returncode,3,first.stderr);self.assertIn('НЕ завершена',first.stdout)
            reader.rollback()
            second=subprocess.run([sys.executable,'-c',code,str(self.path)],capture_output=True,text=True)
            self.assertEqual(second.returncode,0,second.stderr);self.assertIn('завершена.',second.stdout)
            self.verify_logical_and_catalog();self.assertFalse(self.markers())
            NOTES['cli']={'busy_exit':3,'retry_exit':0}
        finally:reader.rollback();reader.close()

    def test_ocr_keeps_small_gram_basis_and_phone_generation(self):
        for text,quantity in [('Шафран 2000 ₸/1g',1),('Шафран 6000 ₸/5g',5),('Шафран 900 ₸/0.5g',.5)]:
            rows=VisionExtractionEngine.extract_from_ocr_text(text)
            self.assertEqual(len(rows),1,text);self.assertTrue(rows[0].publishable,text)
            self.assertEqual((rows[0].quantity,rows[0].unit),(quantity,'g'),text)
        row=VisionExtractionEngine.extract_from_ocr_text('Samsung Galaxy S24/5G 300000 ₸')[0]
        self.assertFalse(row.is_unit_price);self.assertIn('5G',row.raw_title)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Acceptance))
    print('ACCEPTANCE_NOTES='+json.dumps(NOTES,ensure_ascii=False))
    raise SystemExit(not result.wasSuccessful())
