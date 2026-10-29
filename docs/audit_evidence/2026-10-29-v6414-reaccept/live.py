import os,sys,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path.cwd()));os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph6414-live-')
import config
from scrapers import http
from scrapers.evrika import EvrikaScraper
from scrapers.alser import AlserScraper
from html_soup import make_soup
out=[]
for cls,cat in [(EvrikaScraper,config.EVRIKA_CATEGORIES[0]),(AlserScraper,config.ALSER_CATEGORIES[2])]:
 s=cls();url=s._page_url(cat['url'],1) if hasattr(s,'_page_url') else cat['url']
 try:
  response=http.get(url,headers=s.headers,impersonate='chrome124',timeout=30)
  soup=make_soup(response.text)
  from unittest.mock import patch
  with patch.object(http,'get',return_value=response):items=s._fetch_page(cat['name'],cat['url'],1)
  out.append({'shop':s.SHOP_NAME,'url':url,'status':response.status_code,'count':len(items),'complete':items.complete,'declared':s._declared_total(soup),'h1':[h.get_text(' ',strip=True) for h in soup.select('h1')],'selected_city':s.selected_city(soup,response.text) if hasattr(s,'selected_city') else None,'pager':[a.get('href') for a in soup.select('ul.pagination a[href]')],'sample':items[:1]})
 except Exception as e:out.append({'shop':s.SHOP_NAME,'error':type(e).__name__+': '+str(e)})
print(json.dumps(out,ensure_ascii=False,indent=2))
