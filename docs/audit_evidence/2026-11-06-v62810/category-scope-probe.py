import sqlite3,json
from repositories.search_repo import SearchRepository
from repositories.category_repo import CategoryRepository
from domain.search_core import SearchCore
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True)
c.execute('PRAGMA query_only=ON')
c.execute('BEGIN')
rows=[]
for category in [None,'gpus','ai_706f8863ee306bd0']:
    cat=CategoryRepository(c).get_by_id(category) if category else None
    res=SearchCore(SearchRepository(c)).search(query='RX 9070 GRE',category=category,city='Астана')
    rows.append({'category':category,'category_name':cat.name if cat else None,'total_products':res.total_products,'outcome':res.outcome})
print(json.dumps(rows,ensure_ascii=False,indent=2))
c.rollback()
c.close()
