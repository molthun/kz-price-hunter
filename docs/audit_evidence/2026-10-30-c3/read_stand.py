"""Read only the immutable baseline and the finished C3 copy. DATASET is injected by local launcher.
No application import, no working databases, no secrets or personal data.
"""
import collections
import hashlib
import json
from pathlib import Path
import sqlite3

root = Path('/home/molthun/kzph-stand/stand-data')
paths = [root / 'snapshot_before_k2.db', root / 'prices_k2_c3.db']
dbs = [sqlite3.connect(p.as_uri() + '?mode=ro', uri=True) for p in paths]
out = {'paths': list(map(str, paths)), 'integrity': [c.execute('PRAGMA integrity_check').fetchone()[0] for c in dbs]}
keys = [dict(c.execute('SELECT title,canonical_key FROM title_canonical_cache')) for c in dbs]
shops = collections.defaultdict(set)
for t, s in dbs[0].execute('SELECT DISTINCT title,shop FROM products'):
    if s:
        shops[t].add(s)

def pairs(mapping):
    groups = collections.defaultdict(list)
    for title, key in mapping.items():
        if key and shops[title]:
            groups[key].append(title)
    result = set()
    for group in groups.values():
        ts = sorted(group)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if len(shops[a] | shops[b]) >= 2:
                    result.add((a, b))
    return result

before, after = map(pairs, keys)
out['pairs'] = {'before': len(before), 'after': len(after), 'retained': len(before & after),
                'dropped': len(before - after), 'added': len(after - before)}
out['counts'] = []
for c in dbs:
    available = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out['counts'].append({t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0] if t in available else None
                          for t in ['products','offers','offer_price_history','title_canonical_cache','title_fields',
                                    'title_articles','identity_aliases']})
out['key_counts'] = {'k2': sum(k.startswith('k2:') for k in keys[1].values()),
                     'empty': sum(not k for k in keys[1].values()),
                     'legacy': sum(bool(k) and not k.startswith('k2:') for k in keys[1].values())}
all_fields = {t: json.loads(f) for t, f in dbs[1].execute('SELECT title,fields_json FROM title_fields')}
out['eval_inputs'] = {t: {'old': keys[0].get(t), 'new': keys[1].get(t), 'fields': all_fields.get(t),
                         'shops': sorted(shops[t])} for t in {p[k] for p in DATASET['pairs'] for k in ('a','b')}}
out['dropped'] = [{'a': a, 'b': b, 'old': keys[0].get(a), 'new_a': keys[1].get(a), 'new_b': keys[1].get(b),
                   'fields_a': all_fields.get(a), 'fields_b': all_fields.get(b)} for a, b in sorted(before - after)]
out['added'] = [{'a': a, 'b': b, 'new': keys[1].get(a), 'old_a': keys[0].get(a), 'old_b': keys[0].get(b),
                 'fields_a': all_fields.get(a), 'fields_b': all_fields.get(b)} for a, b in sorted(after - before)]
out['retained'] = [{'a': a, 'b': b, 'old': keys[0].get(a), 'new': keys[1].get(a),
                    'fields_a': all_fields.get(a), 'fields_b': all_fields.get(b)} for a, b in sorted(before & after)]
out['run_schema'] = [r[1] for r in dbs[1].execute('PRAGMA table_info(k2_migration_runs)')]
out['run_rows'] = dbs[1].execute('SELECT * FROM k2_migration_runs').fetchall()
out['baseline_rows'] = dbs[1].execute('SELECT count(*) FROM k2_migration_baseline WHERE run_id=?', ('c3-rules',)).fetchone()[0]
out['baseline_mismatch'] = sum(keys[0].get(t) != k for t, k in dbs[1].execute('SELECT title,old_key FROM k2_migration_baseline WHERE run_id=?', ('c3-rules',)))
# Compare persisted rows, allowing only the explicitly expected identity changes.
out['invariants'] = {}
dbs[1].execute('ATTACH DATABASE ? AS baseline', (paths[0].as_uri() + '?mode=ro',))
for table, exclude in [('products', {'canonical_key'}), ('offers', {'product_id','updated_at'}), ('offer_price_history', {'product_id'})]:
    cols = [r[1] for r in dbs[1].execute('PRAGMA table_info(' + table + ')') if r[1] not in exclude]
    select = ','.join('"' + col + '"' for col in cols)
    out['invariants'][table] = {a + '_minus_' + b: dbs[1].execute('SELECT count(*) FROM (SELECT ' + select + ' FROM ' + a + '.' + table +
                                ' EXCEPT SELECT ' + select + ' FROM ' + b + '.' + table + ')').fetchone()[0]
                               for a, b in [('main','baseline'), ('baseline','main')]}
out['focus_v2'] = [dict(zip(('title','key','product_id','v2_key'), row)) for row in dbs[1].execute(
    'SELECT DISTINCT p.title,p.canonical_key,o.product_id,cp.canonical_key FROM products p '
    'JOIN offers o ON o.id="off_"||p.id JOIN canonical_products cp ON cp.id=o.product_id '
    'WHERE p.title IN (SELECT value FROM json_each(?))', (json.dumps(list(out['eval_inputs']), ensure_ascii=False),))]
print(json.dumps(out, ensure_ascii=False, indent=2))
for c in dbs:
    c.close()
