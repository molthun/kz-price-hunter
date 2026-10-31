"""Residual moderation checks: shadow fields, refreshed extraction and scan/moderator interleaving.
Only temporary SQLite. No real AI or services.
"""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database as db
import product_key as pk
from scripts import migrate_to_k2 as migration

class Residuals(_DbCase):
    def product(self,i,title,key,price=1000):
        return {'id':f'p{i}','title':title,'canonical_key':key,'shop':f'shop{i}',
                'city':'Алматы','price':price,'url':f'https://example.kz/{i}'}
    def seed(self,keys):
        db.save_cached_canonical_keys_batch(keys)
        for i,(title,key) in enumerate(keys.items()):db.save_or_update_product(self.product(i,title,key))

    def test_same_uses_shadow_fields_of_third_member(self):
        a,b,c='Samsung Galaxy S24 black','Samsung Galaxy S24 white','Samsung Galaxy S24 blue'
        self.seed({a:'s:a',b:'s:b',c:'s:b'})
        f={'brand':'Samsung','model':'Galaxy S24','sku':None,'variants':[{'value':128,'unit':'gb'}],'unbranded':False}
        other={**f,'variants':[{'value':256,'unit':'gb'}]}
        db.save_title_fields_batch({a:f,b:f},'k2','local','audit')
        db.save_shadow_fields_batch({c:other},{c:pk.build_key(other,c)},'k2','local','audit')
        self.assertEqual(db.get_title_fields_with_source([c])[c]['fields']['variants'][0]['value'],256)
        db.enqueue_disputed_pair(a,b,'two 128GB titles')
        res=db.resolve_disputed_pair_human(pk.pair_hash(a,b),'same','audit')
        print('SHADOW_256_THIRD_MEMBER',res,db.get_cached_canonical_keys_batch([a,b,c]))
        self.assertFalse(res['ok'],'available shadow fields of the third member were ignored')

    def test_different_survives_refreshed_fields_with_unchanged_rules(self):
        a,b='Apple EarPods Lightning','Apple EarPods USB-C'
        fa={'brand':'Apple','model':'EarPods Lightning','sku':None,'variants':[],'unbranded':False}
        fb={**fa,'model':'EarPods USB-C'}
        ka,kb=pk.build_key(fa,a),pk.build_key(fb,b)
        self.seed({a:ka,b:kb})
        db.save_title_fields_batch({a:fa,b:fb},'k2','local','audit')
        db.enqueue_disputed_pair(a,b,'separate connector types')
        ph=pk.pair_hash(a,b)
        res=db.resolve_disputed_pair_human(ph,'different','audit')
        pins=db.title_key_pins([a,b])
        # A later extraction changes field placement; no key-rule/code change is involved.
        generic={**fa,'model':'EarPods'}
        db.save_title_fields_batch({a:generic,b:generic},'k2','local','refreshed-extraction')
        asyncio.run(migration.run_migration(str(db.DB_PATH),'','',rescore_only=True,batch_size=2,run_id='refresh-audit'))
        actual=db.get_cached_canonical_keys_batch([a,b])
        print('DIFFERENT_AFTER_FIELD_REFRESH',res,'pins=',pins,'keys=',actual,'verdict=',db.get_pair_verdict(ph)['decision'])
        self.assertNotEqual(actual[a],actual[b],'ordinary refreshed extraction undid the human different decision')

    def test_prepared_scan_does_not_overwrite_later_operator_pin(self):
        a,b='Apple EarPods Lightning','Apple EarPods USB-C'
        f={'brand':'Apple','model':'EarPods','sku':None,'variants':[],'unbranded':False}
        old=pk.build_key(f,a)
        self.seed({a:old,b:old})
        db.save_title_fields_batch({a:f,b:f},'k2','local','audit')
        db.enqueue_disputed_pair(a,b,'scan interleaving')
        original=db._prepare_products_chunk
        pinned={}
        def interleave(chunk):
            result=original(chunk)
            if not pinned:
                decision=db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit')
                pinned.update(decision['split'])
            return result
        with patch.object(db,'_prepare_products_chunk',interleave):
            db.save_or_update_products_batch([self.product(1,b,old,900)])
        with db.get_connection() as c:
            row=c.execute("SELECT p.canonical_key,cp.canonical_key FROM products p JOIN offers o ON o.id='off_'||p.id JOIN canonical_products cp ON cp.id=o.product_id WHERE p.id='p1'").fetchone()
        print('PIN_AFTER_PREPARATION',pinned,'cache=',db.get_cached_canonical_keys_batch([b]),'legacy_v2=',tuple(row))
        self.assertEqual(row[0],pinned['new_key'],'scan prepared before the operator decision overwrote the pinned legacy key')
        self.assertEqual(row[1],pinned['new_key'])

if __name__=='__main__':unittest.main(verbosity=2)
