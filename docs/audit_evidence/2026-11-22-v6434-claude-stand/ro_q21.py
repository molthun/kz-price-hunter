import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
def show(title, sql, args=()):
    print('##', title)
    for r in c.execute(sql, args): print(r)
q="""select finished_at,shop_key,category,kind,quality,received,valid,baseline,baseline_basis,accepted,substr(reason,1,90)
     from source_scans where {} order by id desc limit {}"""
show('iSpace последние 12', q.format("shop_key='ispace'", 12))
show('Kari обувь последние 8', q.format("shop_key='kari' and category like '%обувь%'", 8))
show('Forte смартфоны последние 4', q.format("category like '%Forte%Смартфон%'", 4))
show('rebased за сутки', q.format("baseline_basis in ('rebased','confirmed') and finished_at > datetime('now','-1 day')", 20))
show('подтверждённые нормы', "select name, value from schema_metadata where name like 'source_norm_confirmed:%'")
