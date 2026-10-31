"""Independent deterministic batch interleaving; temporary SQLite only."""
import runpy
from pathlib import Path
from unittest.mock import patch
import unittest
base = runpy.run_path(str(Path(__file__).resolve().parents[1] / '2026-10-31-mm-ext-reaccept/residuals.py'))
db, pk = base['db'], base['pk']

class RaceAfterRead(base['Residuals']):
    def test_operator_after_pin_read_before_batch_write(self):
        a,b='Apple EarPods Lightning','Apple EarPods USB-C'
        fields={'brand':'Apple','model':'EarPods','sku':None,'variants':[],'unbranded':False}
        old=pk.build_key(fields,a)
        self.seed({a:old,b:old})
        db.save_title_fields_batch({a:fields,b:fields},'k2','local','audit')
        db.enqueue_disputed_pair(a,b,'operator after batch pin read')
        real=db._apply_title_pins_conn
        split={}
        def interleave(conn,items):
            real(conn,items)
            print('BATCH_IN_TRANSACTION_AFTER_PIN_READ',conn.in_transaction)
            self.assertFalse(conn.in_transaction)
            result=db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit')
            self.assertTrue(result['ok'])
            split.update(result['split'])
        with patch.object(db,'_apply_title_pins_conn',interleave):
            db.save_or_update_products_batch([self.product(1,b,old,900)])
        with db.get_connection() as conn:
            row=conn.execute("SELECT p.canonical_key, cp.canonical_key FROM products p JOIN offers o ON o.id='off_'||p.id JOIN canonical_products cp ON cp.id=o.product_id WHERE p.id='p1'").fetchone()
        print('PIN',db.title_key_pins([b]),'CACHE',db.get_cached_canonical_keys_batch([b]),'LEGACY_V2',tuple(row))
        self.assertEqual(tuple(row),(split['new_key'],split['new_key']))

if __name__=='__main__':
    unittest.main(verbosity=2)
