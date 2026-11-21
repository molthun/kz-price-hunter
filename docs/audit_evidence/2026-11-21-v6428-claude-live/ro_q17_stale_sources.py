import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
r=c.execute("""with last as (select shop_key, source_url, max(started_at) t from source_scans group by 1,2),
act as (select shop_key, source_url, count(*) n from product_sources where active=1 group by 1,2)
select l.shop_key, count(*), sum(julianday('now')-julianday(l.t)>1), sum(case when julianday('now')-julianday(l.t)>1 then coalesce(a.n,0) end)
from last l left join act a using(shop_key, source_url) group by 1 having sum(julianday('now')-julianday(l.t)>1)>0 order by 4 desc""").fetchall()
print('shop, sources, sources >24h unscanned, active products in them'); [print(x) for x in r]
print('total', len(r), sum(x[3] or 0 for x in r))
