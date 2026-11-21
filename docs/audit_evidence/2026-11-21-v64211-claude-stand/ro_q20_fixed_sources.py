"""Только чтение: обходы исправленных в 6.42.9-6.42.10 источников после запуска 6.42.11 (16:02:40Z)."""
import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
T='2026-11-21T16:02:40'
for k in ('dns','masterok','magnum','tgrad','marwin'):
    print('##', k)
    for r in c.execute("""select substr(started_at,12,8), category, kind, quality, valid, coalesce(substr(reason,1,110),'') from source_scans
        where shop_key=? and julianday(started_at)>=julianday(?) order by id""",(k,T)):
        print(' ', r)
print('marwin ids by prefix active', c.execute("select substr(id,1,instr(id,'_')+2), is_active, count(*) from products where shop='Меломан' group by 1,2").fetchall())
print('marwin written since 6.42.11', c.execute("select substr(id,1,10), count(*) from products where shop='Меломан' and julianday(updated_at)>=julianday(?) group by 1",(T,)).fetchall())
print('shop_scans', c.execute("select shop_key,status,failure_count,substr(last_error,1,200) from shop_scans where shop_key in ('dns','masterok','magnum','tgrad','marwin')").fetchall())
