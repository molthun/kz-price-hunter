import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
print('shop_notices rows', c.execute("select count(*) from shop_notices").fetchone(), [r[0] for r in c.execute("select shop_key from shop_notices")])
for r in c.execute("""select shop, sum(total_requests) from telemetry_http_aggregates where bucket_type='hour' and shop='' and bucket_start>='2026-11-21T07' group by shop"""): print('unlabeled last 24h', r)
for r in c.execute("""select host, sum(total_requests), sum(status_429) from telemetry_http_aggregates where bucket_type='hour' and shop='' and bucket_start>='2026-11-21T07' group by host order by 2 desc limit 40"""): print(r)
