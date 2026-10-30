"""Fault injection between adding the ledger column and initializing its values.
Only a temporary SQLite database; no AI/search calls.
"""
import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
from scripts import migrate_to_k2 as mig


class Boundary(_DbCase):
    def test_interrupted_ledger_upgrade_preserves_consistent_counters(self):
        title, key = 'Samsung Galaxy S24 256GB', 'samsung:galaxy s24:256gb'
        database.save_cached_canonical_keys_batch({title: key})
        with database.get_connection() as conn:
            conn.execute('''CREATE TABLE k2_migration_runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, api_calls INTEGER NOT NULL DEFAULT 0,
                api_failures INTEGER NOT NULL DEFAULT 0, failed_titles INTEGER NOT NULL DEFAULT 0,
                missing_titles INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0)''')
            conn.execute('''CREATE TABLE k2_migration_baseline (
                run_id TEXT NOT NULL, title TEXT NOT NULL, old_key TEXT NOT NULL, PRIMARY KEY(run_id,title))''')
            conn.execute("INSERT INTO k2_migration_runs VALUES ('old','2026-10-30T15:00:00Z',3,1,2,1,7.0)")
            conn.execute('INSERT INTO k2_migration_baseline VALUES (?,?,?)', ('old', title, key))
            conn.commit()
        real = mig.get_db_connection(str(database.DB_PATH))

        class InterruptedConnection:
            def execute(self, sql, *args):
                if sql.startswith('UPDATE k2_migration_runs SET api_started = api_calls'):
                    raise RuntimeError('audit interruption before initializing api_started')
                return real.execute(sql, *args)
            def __getattr__(self, name):
                return getattr(real, name)

        try:
            with self.assertRaises(RuntimeError):
                mig.load_baseline(InterruptedConnection(), 'old', read_only=False)
        finally:
            real.close()
        report = asyncio.run(mig.run_migration(str(database.DB_PATH), '', '', run_id='old',
                                              rescore_only=True, check_pairs=True))
        counts = report['run']['cumulative']
        print('AFTER_INTERRUPTED_UPGRADE', counts)
        self.assertEqual(counts['api_started'], counts['api_calls'])
        self.assertEqual(counts['api_unfinished'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
