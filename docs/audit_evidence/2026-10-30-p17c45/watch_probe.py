"""Control aliases/matches from real migration rows; not a delivery test.
watched_offers preserves legacy input ID via external_sku (database.py); reproduce that input shape.
"""
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests import test_support
import database
import watches

p = json.loads((Path(__file__).parent / 'watch-input.json').read_text())
c = sqlite3.connect(':memory:')
c.execute('CREATE TABLE identity_aliases(kind TEXT,old_id TEXT,new_id TEXT)')
c.executemany('INSERT INTO identity_aliases VALUES (?,?,?)', p['aliases'])
o = p['offer']
o['id'] = o['external_sku']
o['city'] = 'Астана'
o['former_keys'] = database._former_identities(c, 'key', [o['canonical_key']]).get(o['canonical_key'], [])
o['former_product_ids'] = database._former_identities(c, 'product', [o['canonical_product_id']]).get(o['canonical_product_id'], [])
for w in p['watches']:
    for k in ['shops', 'sellers', 'channels', 'item_conditions']:
        w[k] = json.loads(w[k])
    got = watches.matches(w, o)
    print('control', w['id'], w['kind'], got)
    assert got == (w['id'] != 4)
print('PASS 4 control identity matches; alias lookup and matches are real application functions.')
print('Not checked: complete watched_offers query, delivery, original watch_state notifications.')
