import urllib.request,urllib.parse,time,json,pathlib
out=[]
for q in ['iPhone 15','айфон 15','iphnoe 15','зимняя резина 205/55 R16','ноутбук до 300000','несуществующийтоварzxq771']:
 start=time.monotonic();url='https://shdev.molthun.ru/api/v2/search?'+urllib.parse.urlencode({'q':q,'city':'Астана','limit':3})
 try:
  d=json.load(urllib.request.urlopen(url,timeout=45));out.append({'q':q,'seconds':round(time.monotonic()-start,2),'total':d.get('total_products'),'outcome':d.get('outcome'),'local_ai':d.get('local_ai'),'search_help':d.get('search_help'),'applied_filters':d.get('applied_filters'),'titles':[i.get('product',{}).get('title') for i in d.get('items',[])],'attribute_facets':d.get('attribute_facets',[])[:2]})
 except Exception as e:out.append({'q':q,'seconds':round(time.monotonic()-start,2),'error':str(e)})
 pathlib.Path('/tmp/kzph-audit-20261118/live-search.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out[-1],ensure_ascii=False),flush=True)
