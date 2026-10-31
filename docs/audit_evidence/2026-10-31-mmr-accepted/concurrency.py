"""Independent acceptance: real SQLite lock and competing operator, temporary DB only."""
import runpy
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
base=runpy.run_path(str(Path(__file__).resolve().parents[1]/'2026-10-31-mm-ext-reaccept/residuals.py'))
db,pk=base['db'],base['pk']

class Acceptance(base['Residuals']):
    def setup_pair(self):
        a,b='Apple EarPods Lightning','Apple EarPods USB-C'
        fields={'brand':'Apple','model':'EarPods','sku':None,'variants':[],'unbranded':False}
        old=pk.build_key(fields,a)
        self.seed({a:old,b:old})
        db.save_title_fields_batch({a:fields,b:fields},'k2','local','audit')
        db.enqueue_disputed_pair(a,b,'operator after batch pin read')
        return a,b,old

    def assert_consistent(self,b,expected):
        with db.get_connection() as conn:
            row=conn.execute("SELECT p.canonical_key,cp.canonical_key FROM products p JOIN offers o ON o.id='off_'||p.id JOIN canonical_products cp ON cp.id=o.product_id WHERE p.id='p1'").fetchone()
        self.assertEqual(tuple(row),(expected,expected))
        self.assertEqual(db.title_key_pins([b])[b],expected)
        self.assertEqual(db.get_cached_canonical_keys_batch([b])[b],expected)
        print('CONSISTENT_PIN_CACHE_LEGACY_V2',expected)

    def test_operator_after_read_waits_until_batch_commit(self):
        a,b,old=self.setup_pair()
        trying,finished=threading.Event(),threading.Event()
        result,errors={},[]
        real_conn=db.get_connection
        def connect():
            conn=real_conn()
            if threading.current_thread().name=='audit-operator':
                conn.set_trace_callback(lambda sql: trying.set() if sql.upper().startswith('BEGIN IMMEDIATE') else None)
            return conn
        def operator():
            try: result.update(db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit'))
            except BaseException as err: errors.append(repr(err))
            finally: finished.set()
        worker=threading.Thread(target=operator,name='audit-operator')
        real_apply=db._apply_title_pins_conn
        def interleave(conn,items):
            real_apply(conn,items)
            self.assertTrue(conn.in_transaction)
            with sqlite3.connect(db.DB_PATH,timeout=0) as other:
                with self.assertRaisesRegex(sqlite3.OperationalError,'locked'):
                    other.execute('BEGIN IMMEDIATE')
            worker.start()
            self.assertTrue(trying.wait(3),'operator reached BEGIN IMMEDIATE')
            self.assertFalse(finished.is_set(),'operator must not finish while batch holds writer lock')
            print('ACTUAL_WRITER_LOCK_AND_COMPETING_BEGIN_CONFIRMED')
        try:
            with patch.object(db,'get_connection',connect),patch.object(db,'_apply_title_pins_conn',interleave):
                db.save_or_update_products_batch([self.product(1,b,old,900)])
        finally:
            if worker.ident is not None: worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertTrue(result['ok'])
        self.assert_consistent(b,result['split']['new_key'])

    def test_retry_reloads_pin_after_failed_attempt(self):
        a,b,old=self.setup_pair()
        original=db._prepare_products_chunk
        def prepare(chunk):
            prepared=original(chunk)
            result=db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit')
            self.expected=result['split']['new_key']
            return prepared
        apply=db._apply_title_pins_conn
        calls=[]
        def fail_once(conn,items):
            self.assertTrue(conn.in_transaction)
            calls.append(1)
            apply(conn,items)
            if len(calls)==1: raise sqlite3.OperationalError('database is locked')
        with patch.object(db,'_prepare_products_chunk',prepare),patch.object(db,'_apply_title_pins_conn',fail_once):
            db.save_or_update_products_batch([self.product(1,b,old,900)])
        self.assertEqual(len(calls),2)
        self.assert_consistent(b,self.expected)

if __name__=='__main__': unittest.main(verbosity=2)
