"""Independent real temporary SQLite checks for manual matching moderation; no AI calls."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
import product_key as pk
from scripts import migrate_to_k2 as migration

class Residuals(_DbCase):
    def product(self,i,title,key):
        database.save_or_update_product({'id':f'p{i}','title':title,'canonical_key':key,'shop':f'shop{i}',
            'city':'Алматы','price':1000,'url':f'https://example.kz/{i}'})

    def state(self):
        with database.get_connection() as c:
            return {t:[tuple(r) for r in c.execute(f'SELECT * FROM {t} ORDER BY 1,2')]
                for t in ('title_canonical_cache','products','offers','offer_price_history','identity_aliases',
                          'product_pair_verdicts','disputed_product_pairs')}

    def test_valid_same_pair_does_not_move_conflicting_third_member(self):
        a,b,hidden='Samsung Galaxy S24 128GB black','Samsung Galaxy S24 128GB white','Samsung Galaxy S24 256GB blue'
        keys={a:'samsung:s24-a',b:'samsung:s24-b',hidden:'samsung:s24-b'}
        f={'brand':'Samsung','model':'Galaxy S24','sku':None,'variants':[],'unbranded':False}
        database.save_cached_canonical_keys_batch(keys)
        database.save_title_fields_batch({a:f,b:f,hidden:f},'k2','local','audit')
        for i,t in enumerate(keys):self.product(i,t,keys[t])
        database.enqueue_disputed_pair(a,b,'human pair')
        before=self.state()
        result=database.resolve_disputed_pair_human(pk.pair_hash(a,b),'same','auditor')
        after=database.get_cached_canonical_keys_batch(keys)
        print('HUMAN_SAME_WITH_HIDDEN_256',result,after)
        self.assertFalse(result['ok'],'SAME of the 128GB pair must not move the unseen 256GB member')
        self.assertEqual(self.state(),before)

    def test_human_different_survives_real_rescore(self):
        a,b='Apple EarPods USB-C','Apple EarPods Lightning'
        f={'brand':'Apple','model':'EarPods','sku':None,'variants':[],'unbranded':False}
        old=pk.build_key(f,a)
        database.save_cached_canonical_keys_batch({a:old,b:old})
        database.save_title_fields_batch({a:f,b:f},'k2','local','audit')
        for i,t in enumerate((a,b)):self.product(i,t,old)
        database.enqueue_disputed_pair(a,b,'test an operator decision')
        ph=pk.pair_hash(a,b)
        result=database.resolve_disputed_pair_human(ph,'different','auditor')
        before=database.get_cached_canonical_keys_batch([a,b])
        self.assertNotEqual(before[a],before[b])
        self.assertEqual(database.get_pair_verdict(ph)['decision'],'different')
        asyncio.run(migration.run_migration(str(database.DB_PATH),'','',rescore_only=True,batch_size=2,run_id='human-audit'))
        after=database.get_cached_canonical_keys_batch([a,b])
        with database.get_connection() as c:offers=[tuple(r) for r in c.execute('SELECT id,product_id FROM offers ORDER BY id')]
        print('HUMAN_DIFFERENT_AFTER_RESCORE',before,after,offers,database.get_pair_verdict(ph)['decision'])
        self.assertNotEqual(after[a],after[b],'persisted human DIFFERENT was undone by a normal rescore')

    def test_verdict_write_failure_rolls_back_identity_changes(self):
        a,b='Samsung Galaxy S24 128GB black','Samsung Galaxy S24 128GB white'
        keys={a:'samsung:a:128gb',b:'samsung:b:128gb'}
        database.save_cached_canonical_keys_batch(keys)
        for i,t in enumerate(keys):self.product(i,t,keys[t])
        database.enqueue_disputed_pair(a,b,'failure injection')
        before=self.state()
        with patch.object(database,'save_pair_verdict',side_effect=RuntimeError('audit failure saving verdict')):
            with self.assertRaises(RuntimeError):
                database.resolve_disputed_pair_human(pk.pair_hash(a,b),'same','auditor')
        after=database.get_cached_canonical_keys_batch(keys)
        print('VERDICT_FAILURE_ALREADY_CHANGED_CATALOG',after)
        self.assertEqual(self.state(),before,'catalog changes committed without the matching verdict/status')

if __name__=='__main__':unittest.main(verbosity=2)
