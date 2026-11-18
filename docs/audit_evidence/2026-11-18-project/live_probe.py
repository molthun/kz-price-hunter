from playwright.sync_api import sync_playwright
from pathlib import Path
import json
out=[]
with sync_playwright() as p:
 b=p.chromium.launch(headless=True)
 for width,height in [(800,600),(1366,768)]:
  page=b.new_page(viewport={'width':width,'height':height})
  page.goto('https://shdev.molthun.ru/',wait_until='domcontentloaded');page.wait_for_timeout(1200);page.evaluate('openCatalogModal()');page.wait_for_timeout(600)
  if width==390:page.locator('.catalog-l1-item').first.click()
  pane=page.locator('#catalogContentColumn');pane.hover();page.mouse.wheel(0,2500);page.wait_for_timeout(250)
  data=page.evaluate('''()=>{const ids=['catalogModal','catalogModalGrid','catalogContentColumn'];let d={width:innerWidth,height:innerHeight};for(const id of ids){const x=document.getElementById(id),r=x.getBoundingClientRect();d[id]={top:r.top,bottom:r.bottom,clientHeight:x.clientHeight,scrollHeight:x.scrollHeight,scrollTop:x.scrollTop,overflow:getComputedStyle(x).overflowY}};const a=[...document.querySelectorAll('#catalogContentColumn .catalog-subcat-link')];d.links=a.length;d.below=a.filter(x=>x.getBoundingClientRect().top>document.querySelector('.catalog-modal-container').getBoundingClientRect().bottom).map(x=>x.textContent.trim());return d}''')
  out.append(data);page.screenshot(path=f'/tmp/kzph-audit-20261118/live-catalog-{width}.png');page.close()
 b.close()
Path('/tmp/kzph-audit-20261118/live-layout.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False))
