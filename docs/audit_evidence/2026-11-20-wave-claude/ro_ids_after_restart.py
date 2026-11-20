import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
print(c.execute("""select shop, substr(id, instr(id,'@')+1) suf, count(*) from products
 where (city='Алматы / Казахстан' or shop='Белый Ветер') and julianday(updated_at) >= julianday('2026-11-20T19:00:35')
 group by 1,2 order by 1,2""").fetchall())
