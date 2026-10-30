"""Independent read-only/repair/reopen checks for several persisted ledger states.
No network or working data; snapshots are temporary SQLite.
"""
import hashlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
from scripts import migrate_to_k2 as mig

case = _DbCase()
case.setUp()
try:
    states = [('zero_backfill', 3, 0, 3), ('partial_backfill', 3, 1, 3),
              ('real_unfinished', 3, 5, 5), ('zero_calls', 0, 0, 0)]
    with database.get_connection() as conn:
        conn.execute('''CREATE TABLE k2_migration_runs (
            run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, api_calls INTEGER NOT NULL DEFAULT 0,
            api_failures INTEGER NOT NULL DEFAULT 0, failed_titles INTEGER NOT NULL DEFAULT 0,
            missing_titles INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0,
            api_started INTEGER NOT NULL DEFAULT 0)''')
        conn.execute('''CREATE TABLE k2_migration_baseline (
            run_id TEXT NOT NULL, title TEXT NOT NULL, old_key TEXT NOT NULL, PRIMARY KEY(run_id,title))''')
        for name, calls, started, expected in states:
            conn.execute('INSERT INTO k2_migration_runs VALUES (?,?,?,?,?,?,?,?)',
                         (name, '2026-10-30T15:00:00Z', calls, 1, 2, 1, 7.0, started))
            conn.execute('INSERT INTO k2_migration_baseline VALUES (?,?,?)', (name, 'Synthetic title', 'brand:model'))
        conn.commit()
    def digest():
        return hashlib.sha256(Path(database.DB_PATH).read_bytes()).hexdigest()
    before = digest()
    conn = mig.get_db_connection(str(database.DB_PATH), read_only=True)
    for name, calls, started, expected in states:
        baseline, counts = mig.load_baseline(conn, name, read_only=True)
        assert baseline == {'Synthetic title': 'brand:model'}
        assert counts['api_calls'] == calls and counts['api_started'] == expected
        assert (counts['api_failures'], counts['failed_titles'], counts['missing_titles'], counts['seconds']) == (1, 2, 1, 7.0)
    conn.close()
    assert digest() == before, 'read-only load changed the persisted database'
    print('PASS dry-run matrix: inferred counters valid; DB hash unchanged')
    # First write-mode open repairs all historical impossible states, preserving real unfinished calls.
    for repetition in range(2):
        conn = mig.get_db_connection(str(database.DB_PATH))
        for name, calls, started, expected in states:
            baseline, counts = mig.load_baseline(conn, name, read_only=False)
            assert baseline == {'Synthetic title': 'brand:model'}
            assert counts['api_calls'] == calls and counts['api_started'] == expected
            assert (counts['api_failures'], counts['failed_titles'], counts['missing_titles'], counts['seconds']) == (1, 2, 1, 7.0)
            print('PASS', repetition, name, 'calls=', calls, 'started=', counts['api_started'],
                  'unfinished=', counts['api_started'] - calls)
        conn.close()
    print('PASS repair and repeated reopen: baseline/other counters preserved in all four cases')
finally:
    case.tearDown()
