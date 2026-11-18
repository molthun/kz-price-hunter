from tests import test_support
import tempfile,sqlite3,json
from unittest.mock import patch
from tests.search.test_attribute_facets_filter import create_test_db
from tests.search.test_card_attributes_consistency import seed,TITLE
from repositories.category_repo import CategoryRepository
from domain.attribute_indexing import index_product_facts
import local_search
with tempfile.TemporaryDirectory() as tmp:
 path=tmp+'/audit.db';conn=create_test_db();CategoryRepository(conn).seed_defaults_if_empty();seed(conn,attrs={'storage':'128 ГБ','ram':'6 ГБ'})
 index_product_facts(conn,'p1',[{'name':'Оперативная память','value':'6 ГБ'}],TITLE);conn.commit();disk=sqlite3.connect(path);conn.backup(disk);disk.close()
 cats=local_search.catalog_context(conn);conn.close();fields=[{'key':'ram','label':'Оперативная память'}]
 results=[]
 for attrs in [{},{'ram':'16 ГБ'}]:
  answer={'category_id':'phones','category_name':'Смартфоны','confidence':.99,'search_query':'iPhone 16','fields':fields,'attributes':attrs}
  plan=local_search.clean_plan(answer,'iPhone 16',cats)
  with patch('database.get_connection',side_effect=lambda:sqlite3.connect(path)):
   result=local_search.search_sync({'query':'iPhone 16','city':'Алматы','limit':20},{'status':'ok','plan':plan},{})
  results.append({'model_attributes':attrs,'accepted_attributes':plan['attributes'],'total':result['total_products'],'selected_attributes':result.get('selected_attributes'),'watch_target':result.get('search_help',{}).get('watch_target')})
 print(json.dumps(results,ensure_ascii=False,indent=2))
