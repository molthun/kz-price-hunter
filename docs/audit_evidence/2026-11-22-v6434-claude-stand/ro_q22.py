import sqlite3,sys
since=sys.argv[1] if len(sys.argv)>1 else ''
c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True); c.execute('PRAGMA query_only=ON')
for r in c.execute("""select finished_at,shop_key,category,kind,quality,received,valid,baseline,baseline_basis,accepted,substr(reason,1,120)
   from source_scans where (shop_key='ispace' or (shop_key='kari' and category like '%обувь%')) and finished_at > ? order by id""",(since,)):
    print(r)
