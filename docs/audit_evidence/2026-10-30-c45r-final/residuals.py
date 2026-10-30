"""Independent residual checks for all-member guards and migration-ledger compatibility.
Only temporary SQLite; no real AI/search/notifications.
"""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
import product_key
import product_verify
from scripts import migrate_to_k2 as mig


class Residuals(_DbCase):
    def test_member_without_fields_retains_capacity_guard(self):
        a, b, hidden = ('Samsung Galaxy S24 128GB black', 'Samsung Galaxy S24 128GB white',
                         'Samsung Galaxy S24 256GB blue')
        keys = {a: 'samsung:s24-a', b: 'samsung:s24-b', hidden: 'samsung:s24-b'}
        database.save_cached_canonical_keys_batch(keys)
        for i, t in enumerate(keys):
            database.save_or_update_product({'id': f'p{i}', 'title': t, 'canonical_key': keys[t],
                                             'shop': f'shop{i}', 'city': 'Алматы', 'price': 1000,
                                             'url': f'https://example.kz/p{i}'})
        f = {'brand': 'Samsung', 'model': 'Galaxy S24', 'sku': None, 'edition': None,
             'variants': [{'value': 128, 'unit': 'gb'}], 'unbranded': False}
        # The actual pair has fields; a third member from an old group does not, but its title says 256GB.
        database.save_title_fields_batch({a: f, b: f}, 'k2', 'gemini', 'fake')
        database.enqueue_disputed_pair(a, b, 'shadow_mismatch:missed')
        async def same(*args, **kwargs):
            return {'decision': product_verify.SAME}
        with patch.object(product_verify, 'verify_pair_cached', same):
            result = asyncio.run(product_verify.process_disputed_queue_tick('http://unused', lambda _: None))
        actual = database.get_cached_canonical_keys_batch(keys)
        with database.get_connection() as conn:
            offers = [tuple(r) for r in conn.execute('SELECT id, product_id FROM offers ORDER BY id')]
        print('MEMBER_256_NO_FIELDS', 'status=', result['status'], 'keys=', actual, 'offers=', offers)
        print('CAPACITY_FROM_TITLE', product_key.title_variants(hidden))
        self.assertEqual(result['status'], 'unresolved', 'known 256GB member moved into new 128GB group')
        self.assertEqual(actual, keys)

    def test_previous_ledger_schema_can_resume(self):
        title = 'Samsung Galaxy S24 256GB'
        database.save_cached_canonical_keys_batch({title: 'samsung:galaxy s24:256gb'})
        # Exact ledger schema introduced by ae9dcf9; baseline and cumulative counts must survive the fix.
        with database.get_connection() as conn:
            conn.execute('''CREATE TABLE k2_migration_runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, api_calls INTEGER NOT NULL DEFAULT 0,
                api_failures INTEGER NOT NULL DEFAULT 0, failed_titles INTEGER NOT NULL DEFAULT 0,
                missing_titles INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)''')
            conn.execute('''CREATE TABLE k2_migration_baseline (
                run_id TEXT NOT NULL, title TEXT NOT NULL, old_key TEXT NOT NULL, PRIMARY KEY(run_id,title))''')
            conn.execute('INSERT INTO k2_migration_runs VALUES (?,?,?,?,?,?,?)', ('old', '2026-10-30T15:00:00Z', 1, 1, 1, 0, 1.0))
            conn.execute('INSERT INTO k2_migration_baseline VALUES (?,?,?)', ('old', title, 'samsung:galaxy s24:256gb'))
            conn.commit()
        error = None
        report = None
        try:
            report = asyncio.run(mig.run_migration(str(database.DB_PATH), '', '', rescore_only=True,
                                                  check_pairs=True, run_id='old'))
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        print('OLD_LEDGER_RESUME', 'error=', error, 'report=', report)
        self.assertIsNone(error, 'CREATE TABLE IF NOT EXISTS does not upgrade existing ledger')
        self.assertEqual(report['run']['baseline_titles'], 1)
        self.assertEqual(report['run']['cumulative']['api_calls'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
