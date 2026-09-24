// Offline browser regression: never contacts shops, AI providers, or Telegram.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
 const page=await browser.newPage(); const errors=[];
 await page.addInitScript(()=>{window.tailwind={config:{}}});
 page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.hostname!=='127.0.0.1')return route.abort();
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
  const data=u.pathname==='/api/me'?{user:null,settings:{},auth:{},shops:{}}:u.pathname.includes('alerts')?[]:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:18083/');
 await page.waitForFunction(()=>typeof renderAlertCard==='function');
 const result=await page.evaluate(async()=>{
  const attack=`O'Reilly \\ "<img data-injected src=x onerror="window.pwned=1">`;
  const p={id:77,title:attack,shop:attack,category:attack,city:attack,competitor_shop:attack,canonical_key:attack,
   alert_type:'ARBITRAGE',discount_pct:attack,savings_kzt:attack,new_price:100,old_price:200,price:100,
   image_url:'https://example.invalid/" onerror="window.pwned=1',url:'javascript:window.pwned=1'};
  const fixture=document.createElement('div'); fixture.id='securityFixture';document.body.append(fixture);
  currentMe.user=null;
  fixture.innerHTML=renderAlertCard(p)+renderConsultantProductCard(p)+getSourceTag(attack);
  const guestDismiss=fixture.querySelectorAll('[data-click-action="dismiss"]').length;
  currentMe.user={id:2,is_admin:false};
  const userDismiss=renderAlertCard(p).includes('data-click-action="dismiss"');
  currentMe.user={id:1,is_admin:true};
  fixture.innerHTML+=renderAlertCard(p);
  const adminDismiss=fixture.querySelectorAll('[data-click-action="dismiss"]').length;
  renderBestPriceResults({best_deal:{...p,savings_vs_max:100,savings_pct:attack},items:[p],price_stats:{min_price:100},store_comparison:[{...p,min_price:100}]});
  renderAdminCategories({categories:[{id:attack,name:attack,icon:attack,shops_count:attack,products_count:attack}],tracked_categories:[{id:91,name:attack,query:attack,search_count:attack}]});
  const trackedReceived=[];
  scanTrackedCatNow=(id,name)=>trackedReceived.push([id,name]);
  document.querySelector('[data-click-action="tracked-scan"]').click();
  toggleTrackedCat=(id,active)=>trackedReceived.push([id,active]);
  const toggle=document.querySelector('[data-change-action="tracked-toggle"]');toggle.checked=false;toggle.dispatchEvent(new Event('change',{bubbles:true}));
  // Текст поискового запроса приходит от человека: он не должен попадать внутрь JS-обработчика.
  // Кавычка в запросе закрывала строку в onclick — проверяем, что кнопка передаёт запрос данными.
  currentMe.user={id:2,is_admin:false};
  const queryAttack=`'); window.pwned=1; //`;
  const queryInput=document.createElement('input'); queryInput.id='bestPriceQuery';
  queryInput.value=queryAttack; document.body.append(queryInput);
  renderBestPriceResults({best_deal:{...p,savings_vs_max:100,savings_pct:attack},items:[p],
    price_stats:{min_price:100},store_comparison:[{...p,min_price:100}]});
  const queryBtn=document.querySelector('[data-click-action="watch-query"]');
  const queryPassed=[];
  watchSearchQuery=(q)=>queryPassed.push(q);
  if(queryBtn) queryBtn.click();
  const unsafeLinks=[...fixture.querySelectorAll('a')].filter(a=>!['http:','https:'].includes(a.protocol)).length;
  const handlerLeak=[...document.querySelectorAll('*')].some(e=>[...e.attributes].some(a=>/^on/.test(a.name)&&a.value.includes('window.pwned')));
  const queryInHandler=[...document.querySelectorAll('[onclick]')].some(e=>e.getAttribute('onclick').includes('pwned'));
  fixture.querySelector('[data-click-action="product"]').click();
  await Promise.resolve();
  const modalTitle=document.getElementById('modalProductTitle').textContent;
  const received=[];
  sendConsultantSuggestion=t=>received.push(t);
  const suggestion=document.createElement('div');
  suggestion.innerHTML=`<button ${actionAttrs('suggestion',[attack])}>${escapeHtml(attack)}</button>`;
  fixture.append(suggestion);suggestion.querySelector('button').click();
  const bad=['javascript:alert(1)','data:text/html,<script>1</script>','java\nscript:alert(1)','https://user:pass@example.com','http://wa.me/77010000000','https://wa.me/not-a-number'];
  return {guestDismiss,userDismiss,adminDismiss,unsafeLinks,handlerLeak,modalTitle,attack,received,trackedReceived,
   queryInHandler,queryPassed,queryAttack,
   injected:document.querySelectorAll('[data-injected]').length,pwned:!!window.pwned,
   rejected:bad.every(u=>cleanUrl(u)==='#'),
   legitimate:cleanUrl('https://wa.me/77010000000?text=hello')==='https://wa.me/77010000000?text=hello'&&cleanUrl('/p/example')==='https://kaspi.kz/shop/p/example'};
 });
 assert.equal(result.guestDismiss,0);assert.equal(result.userDismiss,false);assert.equal(result.adminDismiss,1);
 for(const k of ['unsafeLinks','injected'])assert.equal(result[k],0,k);
 for(const k of ['handlerLeak','pwned','queryInHandler'])assert.equal(result[k],false,k);
 assert.deepEqual(result.queryPassed,[result.queryAttack],'поисковый запрос передаётся данными, а не кодом');
 assert.equal(result.modalTitle,result.attack);assert.deepEqual(result.received,[result.attack]);assert.deepEqual(result.trackedReceived,[[91,result.attack],[91,false]]);
 assert.equal(result.rejected,true);assert.equal(result.legitimate,true);
 assert.deepEqual(errors,[]);
 console.log('PASS: catalog/AI/log XSS, поисковый запрос как данные, URL schemes, роли, действия карточки');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
