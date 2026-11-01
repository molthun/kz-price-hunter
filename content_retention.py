"""Source prose is transient. Structured product facts and price history remain intact."""
from __future__ import annotations

import json
import sqlite3


PROSE_KEYS = {'description','full_description','short_description','html_description'}
JSON_STORES = (('product_queue_spool','payload'), ('offers','raw_payload_json'), ('canonical_products','attributes_json'))


def source_free(value):
    if isinstance(value, dict):
        return {k:source_free(v) for k,v in value.items() if str(k).casefold() not in PROSE_KEYS}
    if isinstance(value, list):
        return [source_free(v) for v in value]
    return value


def dirty_json(conn):
    for table, column in JSON_STORES:
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if column not in columns:
            continue
        for row_id, raw in conn.execute(f"SELECT rowid,{column} FROM {table} WHERE lower({column}) LIKE '%description%'"):
            data = json.loads(raw)
            clean = source_free(data)
            if clean != data:
                yield table, column, row_id, json.dumps(clean,ensure_ascii=False)


def description_counts(conn):
    result = {}
    for table in ('products', 'canonical_products'):
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        if 'description' in columns:
            result[table] = conn.execute(f"SELECT count(*) FROM {table} WHERE coalesce(description,'')!=''").fetchone()[0]
    for table, _column, _row_id, _clean in dirty_json(conn):
        result[table] = result.get(table, 0) + 1
    return result


def clear_descriptions(conn):
    counts = description_counts(conn)
    conn.execute('PRAGMA secure_delete=ON')
    for table in ('products','canonical_products'):
        if table not in counts:
            continue
        conn.execute(f"UPDATE {table} SET description='' WHERE coalesce(description,'')!=''")
    for table, column, row_id, clean in list(dirty_json(conn)):
        conn.execute(f'UPDATE {table} SET {column}=? WHERE rowid=?',(clean,row_id))
    conn.commit()
    return counts


def sanitized_backup(source, destination):
    """Use RAM for the unsanitized snapshot; only sanitized pages reach destination."""
    from scripts.scrub_social_content import scrub
    with sqlite3.connect(':memory:') as clean:
        clean.execute('PRAGMA temp_store=MEMORY')
        source.backup(clean)
        clear_descriptions(clean)
        if clean.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_observations'").fetchone():
            result = scrub(clean, apply=True)
            if not result.physical_complete:
                raise RuntimeError('Cannot sanitize social snapshot')
        else:
            clean.execute('VACUUM')
        clean.backup(destination)
