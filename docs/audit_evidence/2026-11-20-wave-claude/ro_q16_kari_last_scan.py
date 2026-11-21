import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
rows=c.execute("""select ps.source_url, count(distinct ps.product_id), (select max(started_at) from source_scans s where s.shop_key='kari' and s.source_url=ps.source_url),
 (select count(*) from source_scans s where s.shop_key='kari' and s.source_url=ps.source_url) from product_sources ps where shop_key='kari' and active=1 group by 1 order by 3""").fetchall()
for r in rows: print(r[0].replace('https://kz.kari.com/catalog/',''), r[1], r[2], r[3])
print('distinct kari urls in source_scans', c.execute("select count(distinct source_url) from source_scans where shop_key='kari'").fetchone())
