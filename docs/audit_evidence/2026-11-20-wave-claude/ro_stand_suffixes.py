import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
r=c.execute("""select shop, city, substr(id, instr(id,'@')+1) suf, is_active, count(*) from products
 where city='Алматы / Казахстан' or (shop='Белый Ветер') group by 1,2,3,4 order by 1,2,3,4""").fetchall()
for x in r: print(x)
