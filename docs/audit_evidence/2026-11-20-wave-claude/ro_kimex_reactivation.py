import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
print(c.execute("""select substr(id, instr(id,'@')+1) suf, is_active, count(*) from products where shop='KIMEX' group by 1,2""").fetchall())
print('kz mirror retired since restart', c.execute("""select count(*) from offers o join products p on o.id='off_'||p.id where p.shop='KIMEX' and p.id like '%@kz' and o.is_active=0 and julianday(o.updated_at)>=julianday('2026-11-20T19:00:35')""").fetchone())
print('unknown history points', c.execute("""select count(*) from price_observations where product_id like 'kimex_%@unknown'""").fetchone())
