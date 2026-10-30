"""Independent boundary checks after C45 fixes; failed expectations document residuals.
No real AI, search or notifications; application SQLite paths are real and temporary.
"""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import ai_service
import database
import product_key
import product_verify
from scripts import migrate_to_k2 as mig


def samsung():
    return {'brand': 'Samsung', 'model': 'Galaxy S24', 'sku': None, 'edition': None,
            'variants': [{'value': 256, 'unit': 'gb'}], 'unbranded': False}


class Residuals(_DbCase):
    def product(self, pid, title, key, shop):
        database.save_or_update_product({'id': pid, 'title': title, 'canonical_key': key,
                                         'shop': shop, 'city': 'Алматы', 'price': 1000,
                                         'url': 'https://example.kz/' + pid})

    def test_same_verdict_respects_revisions_with_legacy_keys(self):
        a, b = 'Sanc M2442PH V2 23.8"', 'Sanc M2442PH V3 23.8"'
        keys = {a: 'sanc:m2442ph v2:23.8', b: 'sanc:m2442ph v3:23.8'}
        fields = {t: {'brand': 'Sanc', 'model': 'M2442PH', 'sku': None, 'edition': rev,
                       'variants': [], 'unbranded': False} for t, rev in [(a, 'V2'), (b, 'V3')]}
        database.save_cached_canonical_keys_batch(keys)
        database.save_shadow_fields_batch(fields, {t: product_key.build_key(f, t) for t, f in fields.items()}, 'k2', 'local', 'fake')
        self.product('v2', a, keys[a], 'shop1')
        self.product('v3', b, keys[b], 'shop2')
        database.enqueue_disputed_pair(a, b, 'shadow_mismatch:split')
        async def same(*args, **kwargs):
            return {'decision': product_verify.SAME}
        with patch.object(product_verify, 'verify_pair_cached', same):
            result = asyncio.run(product_verify.process_disputed_queue_tick('http://unused', lambda _: None))
        actual = database.get_cached_canonical_keys_batch([a, b])
        with database.get_connection() as conn:
            pids = [r[0] for r in conn.execute('SELECT product_id FROM offers ORDER BY id')]
        print('LEGACY_REVISIONS', result, actual, 'V2 product_ids', pids)
        self.assertEqual(len(set(actual.values())), 2, 'legacy cache bypasses new revision guard despite shadow fields')

    def test_incomplete_item_does_not_clear_known_identity(self):
        title, old = 'Samsung Galaxy S24 256GB', 'samsung:galaxy s24:256gb'
        database.save_cached_canonical_keys_batch({title: old})
        self.product('phone', title, old, 'shop1')
        async def incomplete(*args, **kwargs):
            return {'items': [{'title': title}]}
        with patch.object(ai_service, 'call_local_api', incomplete):
            report = asyncio.run(mig.run_migration(str(database.DB_PATH), 'http://unused', 'fake'))
        actual = database.get_cached_canonical_keys_batch([title]).get(title)
        print('INCOMPLETE_RESPONSE', 'key=', actual, 'no_identity=', report['no_identity_titles'],
              'incomplete=', report['incomplete_titles'])
        self.assertEqual(actual, old, 'missing schema fields treated as confirmed absence of brand')

    def test_compare_preserves_units(self):
        cases = [('kingston:memory:1gb', 'k2:kingston:memory:1tb'),
                 ('brand:food:85g', 'k2:brand:food:85ml')]
        actual = [product_key.compare_keys(a, b) for a, b in cases]
        print('UNITS', list(zip(cases, actual)))
        self.assertTrue(all(v != 'matched' for v in actual), 'numeric equality erased unit and capacity differences')

    def test_resume_keeps_counters_for_committed_batches(self):
        a, b = 'Samsung Galaxy S24 256GB A', 'Samsung Galaxy S24 256GB B'
        database.save_cached_canonical_keys_batch({a: 'samsung:a', b: 'samsung:b'})
        async def reply(titles, *args, **kwargs):
            return {t: samsung() for t in titles}, 0
        original = mig.save_migrated_batch
        def interrupt_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            raise KeyboardInterrupt('audit interruption after committed first batch')
        with patch.object(mig, 'fetch_fields_batch', reply), patch.object(mig, 'save_migrated_batch', interrupt_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                asyncio.run(mig.run_migration(str(database.DB_PATH), 'http://unused', 'fake', batch_size=1, run_id='resume'))
        with patch.object(mig, 'fetch_fields_batch', reply):
            report = asyncio.run(mig.run_migration(str(database.DB_PATH), 'http://unused', 'fake', batch_size=1, run_id='resume'))
        print('CRASH_RESUME', report['run'])
        self.assertEqual(report['run']['cumulative']['api_calls'], 2, 'first committed batch vanished from cumulative counters')

    def test_real_mismatch_neighbor_reaches_verdict(self):
        # Replacement for the original audit case whose premise was only a v1/k2 formatting difference.
        a, b = 'Samsung Galaxy S24 256GB black', 'Samsung Galaxy S24 256GB white'
        pk = 'samsung:galaxy s24:256gb'
        database.save_cached_canonical_keys_batch({a: pk, b: pk})
        async def fields_reply(prompt, *args, **kwargs):
            title = b if b in prompt and a not in prompt else a
            f = samsung()
            if title == a:
                f['model'] = 'Galaxy S24 Ultra'
            return {'items': [{'title': title, **f}]}
        creds = {'local_api_base': 'http://unused/v1', 'local_model': 'fake', 'has_local': True}
        with patch.object(ai_service, 'call_local_api', fields_reply):
            asyncio.run(ai_service._run_shadow_normalize([a], creds, {a: pk}, 'v1'))
        self.assertEqual(len(database.get_pending_disputed_pairs()), 1)
        async def different(*args, **kwargs):
            return {'decision': product_verify.DIFFERENT}
        with patch.object(product_verify, 'verify_pair_cached', different):
            result = asyncio.run(product_verify.process_disputed_queue_tick('http://unused', lambda _: None))
        print('VALID_MISMATCH_NEIGHBOR', result)
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['fields_source'], {'a': 'shadow', 'b': 'shadow'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
