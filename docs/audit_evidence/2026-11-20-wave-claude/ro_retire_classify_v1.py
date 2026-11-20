import sqlite3, json
from collections import Counter, defaultdict
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
W0,W1='2026-11-19T18:08:10','2026-11-20T18:12:33'
active_raw=defaultdict(set)
for pid,shop in c.execute("select id, shop from products where is_active=1"):
    active_raw[shop].add(pid.split('@')[0])
rows=c.execute("""select p.id, p.shop, p.city, o.updated_at from offers o join products p on o.id='off_'||p.id
 where o.is_active=0 and p.is_active=0 and julianday(o.updated_at) between julianday(?) and julianday(?)""",(W0,W1)).fetchall()
agg=defaultdict(lambda: Counter())
true_r1=defaultdict(list)
for pid,shop,city,ts in rows:
    rek = pid.split('@')[0] in active_raw[shop]
    agg[shop]['rekeyed' if rek else 'gone']+=1
    agg[shop]['city:'+str(city)]+=1
    if not rek and shop in ('Sulpak','Технодом','Kaspi Магазин','Магнум','12 Месяцев','Мечта'):
        true_r1[shop].append(pid)
for shop,cnt in sorted(agg.items(), key=lambda x:-sum(v for k,v in x[1].items() if not k.startswith('city'))):
    print(shop, dict(cnt))
print('startup-minute (18:08-18:12Z 19.11) count', sum(1 for r in rows if r[3]<'2026-11-19T18:13'))
json.dump(true_r1, open('/tmp/r1_gone.json','w'))
print({k:len(v) for k,v in true_r1.items()})
