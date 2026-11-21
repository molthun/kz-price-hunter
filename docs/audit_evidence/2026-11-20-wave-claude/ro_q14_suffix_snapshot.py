import sqlite3, sys
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
T='2026-11-20T19:00:35'
done=all(c.execute("select count(*) from source_scans where shop_key=? and julianday(started_at)>=julianday(?)",(k,T)).fetchone()[0] for k in ('kari','shopkz'))
if sys.argv[-1]=='probe': print('READY' if done else 'WAIT'); sys.exit()
for shop in ('Kari','Белый Ветер'):
    print(shop, 'state', c.execute("select substr(id, instr(id,'@')+1), is_active, count(*) from products where shop=? and instr(id,'@')>0 group by 1,2 order by 1,2",(shop,)).fetchall())
    print(shop, 'written since restart', c.execute("select substr(id, instr(id,'@')+1), count(*) from products where shop=? and julianday(updated_at)>=julianday(?) group by 1",(shop,T)).fetchall())
    print(shop, 'mirror retired since restart', c.execute("select substr(p.id, instr(p.id,'@')+1), count(*) from offers o join products p on o.id='off_'||p.id where p.shop=? and o.is_active=0 and julianday(o.updated_at)>=julianday(?) group by 1",(shop,T)).fetchall())
for k in ('kari','shopkz'):
    print(k, 'scans since restart', c.execute("select kind, quality, count(*), sum(valid) from source_scans where shop_key=? and julianday(started_at)>=julianday(?) group by 1,2",(k,T)).fetchall())
