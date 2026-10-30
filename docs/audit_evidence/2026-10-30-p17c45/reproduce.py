"""Audit expectations against real SQLite/application paths; failures are findings.
Run from repository root with project Python. No external AI/search calls.
"""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tests.ai.test_local_ai import _DbCase
import ai_service
import database
import monitoring
import product_key
import product_verify
from scripts import migrate_to_k2

CREDS = {'local_api_base': 'http://unused/v1', 'local_model': 'fake', 'has_local': True}

def fields(model='Galaxy S24'):
    return {'brand': 'Samsung', 'model': model, 'sku': None, 'edition': None,
            'variants': [{'value': 256, 'unit': 'gb'}], 'unbranded': False}

def shadow(titles, primary, items, standard='k2'):
    async def reply(*args, **kwargs):
        return None if items is None else {'items': items}
    with patch.object(ai_service, 'call_local_api', reply):
        asyncio.run(ai_service._run_shadow_normalize(titles, CREDS, primary, standard))


class Residuals(_DbCase):
    def test_shadow_preserves_primary_fields(self):
        title = 'Samsung Galaxy S24 256GB'
        primary_fields, local_fields = fields(), fields('Galaxy S24 Ultra')
        primary = product_key.build_key(primary_fields, title)
        database.save_cached_canonical_keys_batch({title: primary})
        database.save_title_fields_batch({title: primary_fields}, 'k2', 'gemini', 'primary')
        shadow([title], {title: primary}, [{'title': title, **local_fields}])
        saved = database.get_title_fields_for_titles([title])[title]
        print('PRIMARY_FIELDS after shadow:', saved['model'], 'cached:', primary,
              'rescore:', product_key.build_key(saved, title))
        self.assertEqual(saved, primary_fields, 'shadow overwrote authoritative fields')

    def test_v1_format_is_not_semantic_mismatch(self):
        title = 'Samsung Galaxy S24 256GB'
        shadow([title], {title: 'samsung:galaxy s24:256gb'}, [{'title': title, **fields()}], 'v1')
        summary = database.normalize_shadow_summary()
        print('SAME_MODEL v1/k2:', summary)
        self.assertEqual(summary['matched'], 1, 'same fields differ only by v1 formatting')

    def test_shadow_discovers_new_cross_group_pair(self):
        a, b = 'Samsung Galaxy S24 256GB black', 'Samsung Galaxy S24 256GB white'
        primary = {a: 'samsung:sm-s921b:256gb', b: 'samsung:galaxy s24:256gb'}
        database.save_cached_canonical_keys_batch(primary)
        shadow([a, b], primary, [{'title': t, **fields()} for t in [a, b]], 'v1')
        pending = database.get_pending_disputed_pairs()
        print('NEW_CROSS_GROUP_PAIR pending:', pending)
        self.assertEqual(len(pending), 1, 'shared shadow key across two primary groups never enqueued')

    def test_metrics_include_all_rows(self):
        for i in range(200):
            database.record_normalize_shadow('matched', f'p{i}', f's{i}', f't{i}')
            database.record_normalize_shadow('matched', f'p{i}', f's{i}', f't{i}')
        for i in range(201):
            database.record_normalize_shadow('mismatch', f'x{i}', f'y{i}', f'u{i}')
        summary = database.normalize_shadow_summary()
        print('ALL_ROWS actual:', {k: summary[k] for k in ['total', 'matched', 'mismatch', 'match_rate']},
              'expected total=601 matched=400 mismatch=201')
        self.assertEqual(summary['total'], 601, 'top-200 samples truncated aggregate')

    def test_enqueued_v1_neighbor_can_reach_verification(self):
        a, b = 'Samsung Galaxy S24 256GB black', 'Samsung Galaxy S24 256GB white'
        primary = {a: 'samsung:galaxy s24:256gb', b: 'samsung:galaxy s24:256gb'}
        database.save_cached_canonical_keys_batch(primary)
        shadow([a], primary, [{'title': a, **fields()}], 'v1')
        self.assertEqual(len(database.get_pending_disputed_pairs()), 1)
        async def ask(prompt):
            raise AssertionError('No external model should be needed to detect missing fields')
        result = asyncio.run(product_verify.process_disputed_queue_tick('http://unused', ask, daily_limit=10))
        print('ENQUEUED_V1_NEIGHBOR:', result)
        self.assertNotEqual(result.get('status'), 'skipped', 'queued neighbor lacks fields; permanently skipped')

    def test_failed_shadow_is_visible_in_health(self):
        a, b = 'Samsung Galaxy S24 256GB', 'Samsung Galaxy S25 256GB'
        shadow([a], {a: product_key.build_key(fields(), a)}, [{'title': a, **fields()}])
        shadow([b], {b: product_key.build_key(fields('Galaxy S25'), b)}, None)
        data = monitoring.normalize_shadow_section()
        print('AFTER_FAILED_SHADOW:', {k: data[k] for k in ['status', 'total', 'match_rate']})
        self.assertEqual(data['total'], 2, 'failed request omitted from comparison coverage')

    def test_unbranded_migration_drops_old_automatic_key(self):
        title, old = 'Чечевица красная кг', 'unknown:chechevitsa krasnaya:1kg'
        database.save_cached_canonical_keys_batch({title: old})
        database.save_title_fields_batch({title: {'brand': None, 'model': 'красная', 'sku': None,
             'unbranded': True, 'variants': [{'value': 1000, 'unit': 'g'}]}}, 'k2', 'local', 'fake')
        report = asyncio.run(migrate_to_k2.run_migration(str(database.DB_PATH), '', '', rescore_only=True))
        actual = database.get_cached_canonical_keys_batch([title]).get(title)
        print('UNBRANDED after migration:', actual, 'report:', report)
        self.assertFalse(actual, 'unbranded still automatically comparable under legacy key')

    def test_revision_is_part_of_identity(self):
        a, b = '23.8" Sanc M2442PH V2 черный', 'Монитор 23.8" Sanc M2442PH V3, Black'
        fa = {'brand': 'Sanc', 'model': 'M2442PH', 'sku': None, 'edition': 'V2',
              'variants': [], 'unbranded': False}
        fb = {**fa, 'edition': 'V3'}
        ka, kb = product_key.build_key(fa, a), product_key.build_key(fb, b)
        print('REVISIONS keys:', ka, kb)
        self.assertNotEqual(ka, kb, 'explicitly different revisions collapsed')

    def test_component_and_system_are_separate(self):
        evidence = json.loads((Path(__file__).parent / 'stand-quality.json').read_text())
        p = next(p for p in evidence['added'] if 'Ryzen 5 5600GT BOX' in p['a'] and 'RAM 8GB' in p['b'])
        resolved = product_key.resolve_keys_with_articles({p['a']: p['fields_a'], p['b']: p['fields_b']})
        print('CPU_AND_SYSTEM resolved:', resolved)
        self.assertNotEqual(resolved[p['a']], resolved[p['b']], 'bare processor merged with full computer')


if __name__ == '__main__':
    unittest.main(verbosity=2)
