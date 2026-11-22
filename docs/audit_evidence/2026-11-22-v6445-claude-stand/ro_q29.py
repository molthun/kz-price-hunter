import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
print('## source_scans iSpace с 09:40Z')
for r in c.execute("""select started_at,finished_at,category,kind,quality,received,valid,rejected,duplicates,baseline,baseline_basis,accepted,reason
  from source_scans where shop_key='ispace' and finished_at>='2026-11-22T09:40' order by id"""): print(r)
print('## shop_scans iSpace')
cols=[r[1] for r in c.execute("pragma table_info(shop_scans)")]
print(dict(zip(cols,c.execute("select * from shop_scans where shop_key='ispace'").fetchone())))
print('## HTTP ispace.kz поминутно с 09:40Z')
for r in c.execute("""select bucket_start,shop,total_requests,status_429,cooldown_rejections,status_codes from telemetry_http_aggregates
  where bucket_type='minute' and host='ispace.kz' and bucket_start>='2026-11-22T09:40' order by bucket_start"""): print(r)
print('## итого 09:40–10:10:', c.execute("""select sum(total_requests),sum(status_429),sum(cooldown_rejections) from telemetry_http_aggregates
  where bucket_type='minute' and host='ispace.kz' and bucket_start>='2026-11-22T09:40' and bucket_start<'2026-11-22T10:10'""").fetchone())
print('## product_sources iSpace: активные/снятые по разделам')
for r in c.execute("""select source_url, sum(active), sum(1-active) from product_sources where shop_key='ispace' group by source_url"""): print(r)
