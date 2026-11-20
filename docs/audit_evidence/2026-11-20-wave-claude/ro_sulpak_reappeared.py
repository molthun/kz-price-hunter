import sqlite3, json, sys
from collections import Counter
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
pres=json.load(open('/tmp/sp_pres.json'))
by=Counter(); det=[]
for pid,city,ts in pres:
    srcs=c.execute("select source_url, active from product_sources where product_id=?",(pid,)).fetchall()
    cat=c.execute("select category, updated_at from products where id=?",(pid,)).fetchone()
    for u,a in srcs:
        sc=c.execute("select started_at, kind, quality, received, valid, baseline from source_scans where shop_key='sulpak' and source_url=? and julianday(started_at)<=julianday(?) order by started_at desc limit 1",(u,ts)).fetchone()
        by[(u,city)]+=1
        if len(det)<200: det.append((pid,city,ts,u,a,cat,sc))
for k,v in by.most_common(): print(v,k)
seen=set()
for d in det:
    if (d[3],d[1]) in seen: continue
    seen.add((d[3],d[1])); print(d)
