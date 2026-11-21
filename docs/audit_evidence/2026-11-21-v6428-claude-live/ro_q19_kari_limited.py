"""Только чтение: в каких источниках Kari остались активные @kz и чем закончился их последний обход."""
import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
for r in c.execute("""select ps.source_url, sum(ps.product_id like '%@kz' and p.is_active=1), sum(ps.product_id like '%@unknown' and p.is_active=1),
  (select kind||'/'||quality||'/'||valid||'/'||coalesce(substr(reason,1,90),'')||'/'||started_at from source_scans s where s.shop_key='kari' and s.source_url=ps.source_url order by id desc limit 1)
  from product_sources ps join products p on p.id=ps.product_id where ps.shop_key='kari' and ps.active=1 group by 1 having sum(ps.product_id like '%@kz' and p.is_active=1)>0"""):
    print(r)
print('kz retired since 6.42.8 without active unknown twin', c.execute("""select count(*) from offers o join products p on o.id='off_'||p.id
  left join products u on u.id=substr(p.id,1,instr(p.id,'@'))||'unknown'
  where p.shop='Kari' and p.id like '%@kz' and o.is_active=0 and julianday(o.updated_at)>=julianday('2026-11-20T19:00:35') and coalesce(u.is_active,0)=0""").fetchone())
print('price history on reactivated unknown (active, written since restart)', c.execute("""select count(distinct p.id), count(*) from products p join price_observations h on h.product_id=p.id
  where p.shop='Kari' and p.id like '%@unknown' and p.is_active=1 and julianday(p.updated_at)>=julianday('2026-11-20T19:00:35')""").fetchone())
