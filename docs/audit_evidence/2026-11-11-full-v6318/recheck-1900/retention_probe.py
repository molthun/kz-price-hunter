import json,sqlite3,sys
path=sys.argv[1] if len(sys.argv)>1 else '/app/data/prices.db'
c=sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=3)
c.execute('PRAGMA query_only=ON')
out={}
for t in ('products','canonical_products'):
 if 'description' in {r[1] for r in c.execute(f'pragma table_info({t})')}:
  out[t+'_with_description']=c.execute(f"SELECT count(*) FROM {t} WHERE length(coalesce(description,''))>0").fetchone()[0]
for t,k in [('offers','raw_payload_json'),('canonical_products','attributes_json'),('product_queue_spool','payload')]:
 out[t+'_with_prose_keys']=c.execute(f"""
 SELECT count(*) FROM {t} x
 WHERE lower(x.{k}) LIKE '%description%'
 AND EXISTS (SELECT 1 FROM json_tree(CASE WHEN json_valid(x.{k}) THEN x.{k} ELSE 'null' END) node
             WHERE lower(node.key) IN ('description','full_description','short_description','html_description'))
 """).fetchone()[0]
print(json.dumps(out))
