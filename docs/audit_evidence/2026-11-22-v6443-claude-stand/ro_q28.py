import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
print('## offer_sizes по продавцам (активные предложения)')
for r in c.execute("""select s.name, count(distinct os.offer_id), count(*), min(os.updated_at), max(os.updated_at)
  from offer_sizes os join offers o on o.id=os.offer_id join sellers s on s.id=o.seller_id where o.is_active=1 group by s.name"""): print(r)
print('## примеры: одна модель с разными размерами у разных предложений')
for r in c.execute("""select o.product_id, count(distinct o.id) offers, group_concat(distinct o.city) cities
  from offer_sizes os join offers o on o.id=os.offer_id where o.is_active=1 group by o.product_id having offers>1 limit 5"""): print(r)
print('## размер 42 у активных предложений: города')
for r in c.execute("""select o.city, count(distinct o.id) from offer_sizes os join offers o on o.id=os.offer_id
  where o.is_active=1 and os.size='42' group by o.city order by 2 desc limit 10"""): print(r)
print('## дробные размеры')
print(c.execute("select count(*), group_concat(distinct size) from offer_sizes where size like '%.%'").fetchone())
