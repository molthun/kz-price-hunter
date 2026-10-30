"""Read-only checks restricted to the submitted C3 copy and its immutable baseline."""
import collections
import json
import sqlite3
from pathlib import Path

root = Path('/home/molthun/kzph-stand/stand-data')
c = sqlite3.connect((root / 'prices_k2_c3.db').as_uri() + '?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
out = {}
out['k2_products_cache_mismatch'] = c.execute('''SELECT count(*) FROM products p
 JOIN title_canonical_cache t ON t.title=p.title
 WHERE t.canonical_key LIKE 'k2:%' AND p.canonical_key IS NOT t.canonical_key''').fetchone()[0]
out['k2_offers_canonical_mismatch'] = c.execute('''SELECT count(*) FROM products p
 JOIN title_canonical_cache t ON t.title=p.title
 JOIN offers o ON o.id='off_'||p.id LEFT JOIN canonical_products cp ON cp.id=o.product_id
 WHERE t.canonical_key LIKE 'k2:%' AND cp.canonical_key IS NOT t.canonical_key''').fetchone()[0]
out['legacy_field_status'] = dict(collections.Counter(
    'missing' if raw is None else 'unbranded' if json.loads(raw).get('unbranded') is True else 'fields_present'
    for (raw,) in c.execute('''SELECT f.fields_json FROM title_canonical_cache t
    LEFT JOIN title_fields f ON f.title=t.title
    WHERE t.canonical_key!='' AND t.canonical_key NOT LIKE 'k2:%' ''')))
out['legacy_examples'] = [dict(zip(('title','key','fields'), row)) for row in c.execute('''
 SELECT t.title,t.canonical_key,f.fields_json FROM title_canonical_cache t
 LEFT JOIN title_fields f ON f.title=t.title
 WHERE t.canonical_key!='' AND t.canonical_key NOT LIKE 'k2:%'
 AND (t.title LIKE '%KC600%' OR t.title LIKE '%AOC 27G42E%') LIMIT 4''')]
out['ryzen7600_box'] = [dict(zip(('title','key','fields'), row)) for row in c.execute('''
 SELECT t.title,t.canonical_key,f.fields_json FROM title_canonical_cache t
 LEFT JOIN title_fields f ON f.title=t.title
 WHERE t.title IN ('AMD Ryzen 5 7600X BOX','Процессор AMD Ryzen 5 7600X box')''')]
print(json.dumps(out, ensure_ascii=False, indent=2))
c.close()
