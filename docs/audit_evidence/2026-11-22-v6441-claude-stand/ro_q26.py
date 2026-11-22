import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
for r in c.execute("""select shop, host, count(*), sum(total_requests), sum(status_429), max(retry_after_max_s) from telemetry_http_aggregates
  where host like '%ispace%' and bucket_type='minute' and bucket_start>='2026-11-22T06:00' group by shop, host"""): print(r)
print('## 429 minutes over last 7 days, ispace')
for r in c.execute("""select bucket_start, shop, total_requests, status_429, cooldown_rejections from telemetry_http_aggregates
  where host like '%ispace%' and bucket_type='hour' and status_429>0 and bucket_start>='2026-11-15' order by bucket_start"""): print(r)
