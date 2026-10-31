"""Desired-behavior checks for scrub. All files are synthetic temporary SQLite databases.
Never run against working data. No raw file mutations by this probe: only the audited scrub does that.
"""
import json
import pathlib
import sqlite3
import tempfile
import unittest

from repositories import init_schema_v2
from repositories.product_repo import ProductRepository
from scripts.scrub_social_content import scrub

NOTES={}


class ScrubIntegrity(unittest.TestCase):
    def test_cleanup_preserves_allowed_catalog_title(self):
        with tempfile.TemporaryDirectory(prefix='kzph-scrub-preserve-') as tmp:
            path=pathlib.Path(tmp)/'prices.db'
            c=sqlite3.connect(path);init_schema_v2(c)
            title='Samsung Galaxy S24'
            prod=ProductRepository(c).get_or_create_canonical('audit:kept',title,'smartphones')
            c.execute("INSERT INTO content_observations(id,seller_id,channel_id,text,media_urls,observed_at,raw_metadata) VALUES ('old','s','c',?,'[]','2026-01-01','{}')",(title,));c.commit()
            result=scrub(c,apply=True);c.close()
            fresh=sqlite3.connect(path)
            try:
                actual=fresh.execute('SELECT title FROM canonical_products WHERE id=?',(prod.id,)).fetchone()[0]
                integrity=fresh.execute('PRAGMA integrity_check').fetchone()[0]
                NOTES['preserve_catalog']={'changed':result,'expected_title':title,'actual_title_repr':repr(actual),'integrity_check':integrity}
                self.assertEqual(actual,title,'Scrubbing original observations must not overwrite the permitted catalog title')
            finally:fresh.close()

    def test_long_post_cannot_leave_marker_with_old_wal_reader(self):
        with tempfile.TemporaryDirectory(prefix='kzph-scrub-overflow-') as tmp:
            path=pathlib.Path(tmp)/'prices.db'
            c=sqlite3.connect(path,timeout=.05);c.execute('PRAGMA journal_mode=WAL');init_schema_v2(c)
            marker='SYNTHETIC_OLD_POST_OVERFLOW_MARKER'
            text='A'*10000+marker+'B'*10000
            c.execute("INSERT INTO content_observations(id,seller_id,channel_id,text,media_urls,observed_at,raw_metadata) VALUES ('old','s','c',?,'[]','2026-01-01','{}')",(text,));c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            reader=sqlite3.connect(path);reader.execute('BEGIN');reader.execute('SELECT text FROM content_observations').fetchone()
            try:
                result=scrub(c,apply=True)
                files=[p.name for p in (path,pathlib.Path(str(path)+'-wal')) if p.exists() and marker.encode() in p.read_bytes()]
                logical=c.execute('SELECT text FROM content_observations').fetchone()[0]
                NOTES['overflow_wal']={'changed':result,'logical_text':logical,'marker_files':files,'post_bytes':len(text)}
                self.assertFalse(files,'A successful physical cleanup must not retain overflow-page portions of the original post')
            finally:reader.rollback();reader.close();c.close()


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ScrubIntegrity))
    print('SCRUB_INTEGRITY='+json.dumps(NOTES,ensure_ascii=False))
    raise SystemExit(not result.wasSuccessful())
