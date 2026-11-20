import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
for r in c.execute("""select source_url, started_at, kind, quality, received, valid, duplicates, baseline from source_scans
 where shop_key='sulpak' and julianday(started_at) between julianday('2026-11-19T18:08:10') and julianday('2026-11-20T18:12:33') order by source_url, started_at"""):
    print(r[0].split('/f/')[-1][:22], r[1][:16], r[2], r[3], 'recv',r[4],'valid',r[5],'dup',r[6],'base',r[7])
