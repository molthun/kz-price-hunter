from pathlib import Path
import json
from playwright.sync_api import sync_playwright
html=Path('/tmp/kzph-audit-20261118/source/web/templates/search_first.html').read_text()
results=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 for width,height in [(390,844),(768,900),(800,600),(1024,768),(1366,768),(1920,1080)]:
  for theme in ['light','dark']:
   page=b.new_page(viewport={'width':width,'height':height},color_scheme=theme)
   page.route('**/*',lambda r:r.fulfill(content_type='application/json',body='{}') if '/api/' in r.request.url else r.fulfill(content_type='text/html',body=html))
   page.goto('http://127.0.0.1:18121/');page.evaluate('openCatalogModal()');page.wait_for_timeout(100)
   for root in page.evaluate('Object.keys(CATALOG_STRUCTURE)'):
    page.evaluate('(r)=>selectCatalogL1(r)',root)
    d=page.evaluate('''()=>{const p=document.getElementById('catalogContentColumn'),g=document.querySelector('.catalog-groups-container');const r=p.getBoundingClientRect();return {width:innerWidth,root:activeCatalogL1,pane:{client:p.clientWidth,scroll:p.scrollWidth,clientHeight:p.clientHeight,scrollHeight:p.scrollHeight},groups:{client:g.clientWidth,scroll:g.scrollWidth,clientHeight:g.clientHeight,scrollHeight:g.scrollHeight},outside:[...p.querySelectorAll('.catalog-group-card')].filter(x=>x.getBoundingClientRect().right>r.right+1).length}}''')
    if d['outside'] or root=='electronics':results.append({'theme':theme,**d})
   page.evaluate("selectCatalogL1('electronics')")
   if theme=='light' and width in [390,800,1366]:page.screenshot(path=f'/tmp/kzph-audit-20261118/catalog-{width}.png')
   page.close()
 b.close()
Path('/tmp/kzph-audit-20261118/layout.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False))
