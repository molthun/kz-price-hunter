import sqlite3, datetime
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
print('## http aggregates ispace.kz, minute buckets since 07:30Z')
for r in c.execute("""select bucket_type,bucket_start,total_requests,status_2xx,status_4xx,status_429,cooldown_rejections,retry_after_max_s,status_codes
   from telemetry_http_aggregates where host like '%ispace%' and bucket_start >= '2026-11-21' order by bucket_start"""
   ):
    print(r[0], r[1], r[2:])
print('## events ispace since 07:30Z')
for r in c.execute("""select timestamp,type,severity,category,substr(message,1,200) from telemetry_events
   where shop like '%iSpace%' and created_at >= '2026-11-22T07:30' order by id limit 30"""): print(r)
