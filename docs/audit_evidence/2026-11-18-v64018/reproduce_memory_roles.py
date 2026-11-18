from tests import test_support
import tempfile,sqlite3,json
from unittest.mock import patch
from tests.search.test_attribute_facets_filter import create_test_db
from tests.search.test_card_attributes_consistency import seed
from repositories.category_repo import CategoryRepository
from domain.attribute_indexing import index_product_facts
import local_search
cases=[{'query':'ноутбук оперативная память 64 ГБ','title':'Ноутбук AuditPro оперативная память 64 ГБ SSD 512 ГБ','category':'Ноутбуки','cid':'laptops','brand':'Audit','facts':[{'name':'Оперативная память','value':'64 ГБ'},{'name':'Встроенная память','value':'512 ГБ'}],'answers':[{}, {'ram':'64 ГБ'}, {'ram':'64 ГБ','storage':'64 ГБ'}]}, {'query':'Xiaomi 14 8/256','title':'Смартфон Xiaomi 14 8/256GB','category':'Смартфоны','cid':'phones','brand':'Xiaomi','facts':[{'name':'Оперативная память','value':'8 ГБ'},{'name':'Встроенная память','value':'256 ГБ'}],'answers':[{}, {'ram':'8 ГБ','storage':'256 ГБ'}, {'ram':'8 ТБ','storage':'256 ТБ'}]}]
out=[]
for case in cases:
 with tempfile.TemporaryDirectory() as tmp:
  path=tmp+'/audit.db';c=create_test_db();CategoryRepository(c).seed_defaults_if_empty();seed(c,title=case['title'],category=case['category'],brand=case['brand']);index_product_facts(c,'p1',case['facts'],case['title']);c.commit();disk=sqlite3.connect(path);c.backup(disk);disk.close();cats=local_search.catalog_context(c);c.close()
  for attrs in case['answers']:
   fields=[{'key':'ram','label':'Оперативная память'},{'key':'storage','label':'Встроенная память'}]
   a={'category_id':case['cid'],'category_name':case['category'],'confidence':.99,'search_query':case['query'],'fields':fields,'attributes':attrs}
   plan=local_search.clean_plan(a,case['query'],cats)
   with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
    d=local_search.search_sync({'query':case['query'],'city':'Алматы','limit':20},{'status':'ok','plan':plan},{})
   out.append({'query':case['query'],'model_attributes':attrs,'accepted_attributes':plan['attributes'],'total':d['total_products'],'selected_attributes':d.get('selected_attributes'),'watch_target':d.get('search_help',{}).get('watch_target')})
print(json.dumps(out,ensure_ascii=False,indent=2))
