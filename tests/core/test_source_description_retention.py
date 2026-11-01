from tests import test_support  # noqa: F401
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database
from content_retention import clear_descriptions, description_counts, sanitized_backup
from scripts.scrub_social_content import _finish_physically
from scripts.scrub_product_descriptions import protected_fingerprint
from domain.models import CanonicalProduct
from repositories import ProductRepository, OfferRepository


class RetentionTests(unittest.TestCase):
    def test_ingestion_all_paths_discard_prose_and_backup_sanitizes_legacy_data(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(database,'DB_PATH',Path(tmp)/'prices.db'), \
             patch('config.DATA_DIR',Path(tmp)),patch.dict(os.environ,{'DUAL_WRITE_V2':'1'}):
            with contextlib.redirect_stdout(io.StringIO()):database.init_db()
            marker='FORBIDDEN_FULL_SOURCE_DESCRIPTION_' * 30
            item=dict(id='retention',title='Apple iPhone 16 128GB',shop='Kaspi',city='Астана',price=100000,
                      url='https://example.com/item',category='Смартфоны',description=marker)
            database.save_or_update_products_batch([item])
            database.save_or_update_product({**item,'id':'single'})
            self.assertEqual(database.spool_admit('retention-task','kaspi','Kaspi',None,{'prods':[item]}),'accepted')
            with database.get_connection() as conn:
                offer_id=conn.execute('SELECT id FROM offers LIMIT 1').fetchone()[0]
                offer=OfferRepository(conn).get_by_id(offer_id)
                offer.raw_payload={'description':marker,'facts':{'width':'205','full_description':marker}}
                OfferRepository(conn).save_or_update(offer)
                ProductRepository(conn).save_or_update(CanonicalProduct(id='direct',canonical_key='direct',title='Audit',category='Смартфоны',description=marker))
                conn.commit()
                self.assertFalse(any(description_counts(conn).values()))
                # Simulate old, previously retained data before cleanup.
                conn.execute('UPDATE products SET description=?',(marker,));conn.execute('UPDATE canonical_products SET description=?',(marker,));conn.commit()
                conn.execute('UPDATE product_queue_spool SET payload=?',(json.dumps({'prods':[item]}),))
                conn.execute('UPDATE offers SET raw_payload_json=?',(json.dumps({'description':marker,'facts':{'width':'205'}}),))
                conn.commit()
                prices=conn.execute('SELECT id,current_price FROM products ORDER BY id').fetchall()
            backup=database.backup_database('retention-test')
            self.assertNotIn(marker.encode(),Path(backup).read_bytes())
            with sqlite3.connect(backup) as conn:
                self.assertFalse(any(description_counts(conn).values()))
                self.assertEqual([tuple(r) for r in conn.execute('SELECT id,current_price FROM products ORDER BY id')],[tuple(r) for r in prices])
            with database.get_connection() as conn:
                self.assertTrue(any(description_counts(conn).values())) # backup does not mutate active source
                protected=protected_fingerprint(conn)
                clear_descriptions(conn)
                self.assertEqual(protected_fingerprint(conn),protected)
                self.assertEqual(_finish_physically(conn),'')
                self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertNotIn(marker.encode(),(Path(tmp)/'prices.db').read_bytes())

    def test_sanitized_backup_keeps_history_and_watches_and_removes_social_prose(self):
        with sqlite3.connect(':memory:') as src,sqlite3.connect(':memory:') as dst:
            src.executescript("""CREATE TABLE products(id TEXT,description TEXT,current_price REAL);
                CREATE TABLE watches(id INTEGER,target TEXT);
                CREATE TABLE price_observations(price REAL);
                CREATE TABLE content_observations(id TEXT,text TEXT,media_urls TEXT,raw_metadata TEXT);
                INSERT INTO products VALUES('p','foreign description',100);
                INSERT INTO watches VALUES(1,'search target');INSERT INTO price_observations VALUES(99);
                INSERT INTO content_observations VALUES('p','foreign post','["image"]','{}');""")
            sanitized_backup(src,dst)
            self.assertEqual(dst.execute('SELECT description FROM products').fetchone()[0],'')
            self.assertEqual(dst.execute('SELECT text,media_urls FROM content_observations').fetchone(),('','[]'))
            self.assertEqual(dst.execute('SELECT * FROM watches').fetchone(),(1,'search target'))
            self.assertEqual(dst.execute('SELECT price FROM price_observations').fetchone()[0],99)
