import sqlite3
from collections import Counter
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
act={}; ina={}
for pid,city,a,up,cr in c.execute("select id, city, is_active, updated_at, created_at from products where shop='Kari'"):
    raw=pid.split('@')[0]; (act if a else ina)[pid]=(raw,city,up,cr)
print('suffix active', Counter(p.split('@')[-1] if '@' in p else '-' for p in act).most_common(4))
print('suffix inactive', Counter(p.split('@')[-1] if '@' in p else '-' for p in ina).most_common(4))
print('city active', Counter(v[1] for v in act.values()).most_common(3), 'city inactive', Counter(v[1] for v in ina.values()).most_common(3))
araw={v[0] for v in act.values()}
print('inactive whose raw id is active under another id', sum(v[0] in araw for v in ina.values()), 'of', len(ina))
print('active created_at min/max', min(v[3] for v in act.values()), max(v[3] for v in act.values()))
print('sample', list(ina.items())[:2], [p for p in act if p.split('@')[0]==list(ina.values())[0][0]][:2])
