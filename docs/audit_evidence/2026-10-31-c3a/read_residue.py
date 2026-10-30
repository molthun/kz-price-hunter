"""Только чтение: все названия копии C3 с прежним ключом. Открывает лишь prices_k2_c3.db, mode=ro, query_only=ON."""
import json
import sqlite3
from pathlib import Path

db = Path('/home/molthun/kzph-stand/stand-data/prices_k2_c3.db')
c = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
rows = c.execute('''SELECT t.title, t.canonical_key, f.fields_json FROM title_canonical_cache t
 LEFT JOIN title_fields f ON f.title=t.title
 WHERE t.canonical_key!='' AND t.canonical_key NOT LIKE 'k2:%' ORDER BY t.title''').fetchall()
print(json.dumps([{'title': t, 'key': k, 'fields': json.loads(f) if f else None} for t, k, f in rows], ensure_ascii=False))
c.close()
