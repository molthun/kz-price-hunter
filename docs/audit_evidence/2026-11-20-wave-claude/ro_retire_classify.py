import sqlite3, json
from collections import Counter, defaultdict
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
W0,W1='2026-11-19T18:08:10','2026-11-20T18:12:33'
active=set()
for pid,shop,city in c.execute("select id, shop, city from products where is_active=1"):
    active.add((shop, pid.split('@')[0], city))
rows=c.execute("""select p.id, p.shop, p.city, o.updated_at from offers o join products p on o.id='off_'||p.id
 where o.is_active=0 and p.is_active=0 and julianday(o.updated_at) between julianday(?) and julianday(?)""",(W0,W1)).fetchall()
agg=defaultdict(Counter); gone=defaultdict(list)
for pid,shop,city,ts in rows:
    k='rekeyed' if (shop,pid.split('@')[0],city) in active else 'gone'
    agg[shop][k]+=1
    if k=='gone': gone[shop].append([pid,city,ts])
tot=Counter()
for shop,cnt in sorted(agg.items(), key=lambda x:-sum(x[1].values())):
    print(shop, dict(cnt)); tot.update(cnt)
print('TOTAL', dict(tot), len(rows))
json.dump(gone, open('/tmp/gone_all.json','w'), ensure_ascii=False)
