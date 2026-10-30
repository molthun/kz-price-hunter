"""Read-only audit of immutable migration snapshot and finished migration copy.
Run via SSH python3 stdin. Does not import application or open working databases.
"""
import collections
import json
import sqlite3
from pathlib import Path

base = Path('/home/molthun/kzph-stand/stand-data')
paths = [base / 'snapshot_before_k2.db', base / 'prices_k2.db']
dbs = [sqlite3.connect(p.as_uri() + '?mode=ro', uri=True) for p in paths]
out = {'paths': [str(p) for p in paths]}
out['integrity'] = [c.execute('PRAGMA integrity_check').fetchone()[0] for c in dbs]
tables = ['products', 'offers', 'offer_price_history', 'canonical_products',
          'title_canonical_cache', 'title_fields', 'title_articles', 'identity_aliases']
out['counts'] = []
for c in dbs:
    available = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    out['counts'].append({t: c.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                          if t in available else None for t in tables})
keys = [dict(c.execute('SELECT title,canonical_key FROM title_canonical_cache')) for c in dbs]
shops = collections.defaultdict(set)
for t, s in dbs[0].execute('SELECT DISTINCT title,shop FROM products'):
    if s:
        shops[t].add(s)

def pairs(mapping):
    groups = collections.defaultdict(list)
    for t, k in mapping.items():
        if k and shops[t]:
            groups[k].append(t)
    result = set()
    for group in groups.values():
        ts = sorted(group)
        for i, a in enumerate(ts):
            for b in ts[i + 1:]:
                if len(shops[a] | shops[b]) > 1:
                    result.add((a, b))
    return result

before, after = map(pairs, keys)
out['pairs'] = {'before': len(before), 'after': len(after), 'retained': len(before & after),
                'dropped': len(before - after), 'added': len(after - before)}
out['cache_k2'] = sum(k.startswith('k2:') for k in keys[1].values())
out['remaining_non_k2'] = collections.Counter()
out['remaining_examples'] = []
all_fields = {t: json.loads(f) for t, f in dbs[1].execute('SELECT title,fields_json FROM title_fields')}
for title, key in keys[1].items():
    if not key.startswith('k2:'):
        f = all_fields.get(title)
        kind = 'no_fields' if f is None else 'has_fields'
        out['remaining_non_k2'][kind] += 1
        if len(out['remaining_examples']) < 30:
            out['remaining_examples'].append({'title': title, 'key': key, 'fields': f})
for name, pairs_set in [('dropped', before - after), ('added', after - before)]:
    out[name] = [{'a': a, 'b': b, 'old_a': keys[0].get(a), 'old_b': keys[0].get(b),
                  'new_a': keys[1].get(a), 'new_b': keys[1].get(b),
                  'fields_a': all_fields.get(a), 'fields_b': all_fields.get(b)}
                 for a, b in sorted(pairs_set)]
out['identity_checks'] = {}
c = dbs[1]
out['identity_checks']['missing_products'] = dbs[0].execute('SELECT count(*) FROM products').fetchone()[0] - c.execute('SELECT count(*) FROM products').fetchone()[0]
out['identity_checks']['orphan_offers'] = c.execute('SELECT count(*) FROM offers o LEFT JOIN canonical_products p ON p.id=o.product_id WHERE p.id IS NULL').fetchone()[0]
out['identity_checks']['dangling_title_owner'] = c.execute('SELECT count(*) FROM canonical_products p LEFT JOIN offers o ON o.id=p.title_offer_id WHERE p.title_offer_id IS NOT NULL AND (o.id IS NULL OR o.product_id != p.id)').fetchone()[0]
out['stored_report'] = json.loads((base / 'migration_report_full.json').read_text())
print(json.dumps(out, ensure_ascii=False, indent=2))
for c in dbs:
    c.close()
