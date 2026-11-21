"""Только чтение: метаданные волн, Forte по городам, повторы Магнума/MasterOK после 6.42.8."""
import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
T='2026-11-21T09:14:33'
print('wave meta', c.execute("select name, value from schema_metadata where name in ('wave_index','wave_cycle','wave_last_run_at','wave_last_cycle_completed_at')").fetchall())
print('forte active by suffix', c.execute("select substr(id,instr(id,'@')+1), count(*) from products where shop='Forte Market' and is_active=1 group by 1").fetchall())
print('forte written since 6.42.8 by suffix', c.execute("select substr(id,instr(id,'@')+1), count(*) from products where shop='Forte Market' and julianday(updated_at)>=julianday(?) group by 1",(T,)).fetchall())
print('forte scans since 6.42.8', c.execute("select kind, quality, count(*), sum(valid) from source_scans where shop_key='fortemarket' and julianday(started_at)>=julianday(?) group by 1,2",(T,)).fetchall())
print('forte bad reasons', c.execute("select category, quality, substr(reason,1,120) from source_scans where shop_key='fortemarket' and julianday(started_at)>=julianday(?) and quality in ('failed','degraded') order by id desc limit 5",(T,)).fetchall())
for k in ('magnum','masterok'):
    print(k, 'attempts', [r[0][11:19] for r in c.execute("select started_at from source_scans where shop_key=? and julianday(started_at)>=julianday(?) group by scan_id order by started_at",(k,T))])
    print(k, 'shop_scans', c.execute("select status, failure_count, next_retry_at, substr(last_error,1,150) from shop_scans where shop_key=?",(k,)).fetchall())
