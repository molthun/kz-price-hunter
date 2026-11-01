"""Offline description cleanup, including existing backup DBs; dry run by default.
Does not remove products, price history, offers, users or watches.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from content_retention import clear_descriptions, description_counts, source_free, JSON_STORES
from scripts.scrub_social_content import _finish_physically


def protected_fingerprint(conn):
    """Digest minimal comparison data and subscriptions without exposing their contents."""
    tables = ('products','canonical_products','offers','price_observations','price_history',
              'offer_price_history','watches','watch_state','watch_states','watch_outbox',
              'notification_outbox','product_queue_spool','watch_events','telegram_users','users','user_sessions','favorites')
    result = {}
    for table in tables:
        columns = [r[1] for r in conn.execute(f'PRAGMA table_info({table})') if r[1]!='description']
        if not columns:
            continue
        projection = ','.join('"'+name+'"' for name in columns)
        digest = hashlib.sha256()
        count = 0
        for row in conn.execute(f'SELECT {projection} FROM {table} ORDER BY rowid'):
            values=list(row)
            for json_table, json_column in JSON_STORES:
                if table == json_table and json_column in columns:
                    index=columns.index(json_column)
                    values[index]=source_free(json.loads(values[index]))
            digest.update(json.dumps(values,ensure_ascii=False,default=str).encode())
            digest.update(b'\n');count += 1
        result[table] = (count,digest.hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('db', nargs='+')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    for name in args.db:
        path = Path(name).resolve(strict=True)
        with sqlite3.connect(path.as_uri() + ('?mode=rw' if args.apply else '?mode=ro'), uri=True) as conn:
            counts = description_counts(conn)
            if args.apply:
                protected = protected_fingerprint(conn)
                clear_descriptions(conn)
                reason = _finish_physically(conn)
                if reason:
                    raise RuntimeError('Physical cleanup incomplete: ' + reason)
                assert not any(description_counts(conn).values())
                if protected_fingerprint(conn) != protected:
                    raise RuntimeError('Protected data changed; preserve sanitized recovery copy and investigate')
            print(json.dumps(dict(file=path.name, descriptions=counts, applied=args.apply, protected_data_unchanged=True if args.apply else None)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
