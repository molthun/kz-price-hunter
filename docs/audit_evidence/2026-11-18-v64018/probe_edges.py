from tests import test_support
import json
from types import SimpleNamespace
import local_search
from domain.product_options import title_color,offer_options,build_options
cases=[('ноутбук оперативная память 64 ГБ',{'ram':'64 ГБ','storage':'64 ГБ'}),('ноутбук оперативная память 16 ГБ',{'ram':'16 ГБ'}),('iPhone 16 128GB',{'storage':'128GB'}),('Xiaomi 14 8/256',{'ram':'8 ТБ','storage':'256 ТБ'}),('ноутбук ОЗУ 32GB SSD 1TB',{'ram':'32 ГБ','storage':'1 ТБ'})]
colors=['Black Shark GT3 Neo 51 мм (Black)','Black Shark GT3 Neo 51 мм (Burgundy)','Black Shark GT3 Neo 51 мм (Glacier)','Смартфон APPLE iPhone 18 PRO 256GB (Black)','Смартфон APPLE iPhone 18 PRO 256GB (Burgundy)','Смартфон APPLE iPhone 18 PRO 256GB (Glacier)']
res={'grounding':[{'query':q,'answer':attrs,'accepted':local_search.grounded_attributes(attrs,q)} for q,attrs in cases],'colors':[{'title':t,'color':title_color(t)} for t in colors]}
offers=[SimpleNamespace(options=offer_options(t),price=100+i,is_purchasable=True) for i,t in enumerate(colors[:3])]
res['color_options']=build_options(offers)
print(json.dumps(res,ensure_ascii=False,indent=2))
