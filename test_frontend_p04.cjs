// P04 (U01–U07): единый писатель счётчиков, видимые ошибки API, вёрстка на узком и широком экране. Офлайн, API подменён.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 const statsCalls=[];
 async function open(viewport,{failLists=false}={}){
  const page=await browser.newPage({viewport}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}};localStorage.setItem('kz_hunter_city','Астана')});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
   let data={}, status=200;
   if(u.pathname==='/api/me')data={user:{id:1,is_admin:true,first_name:'A'},settings:{},auth:{},shops:{}};
   if(u.pathname==='/api/stats'){statsCalls.push(u.search);data={total_products:33294,total_store_deals:11732,total_discounts:11732,total_anomalies:3,total_arbitrage:13,total_alerts:16,shops:{},scan_state:{},db_freshness:{}};}
   if(u.pathname==='/api/deals'){if(failLists)status=500;else data={deals:[],total:9999,shops_summary:[{shop:'Все',count:9999}],offset:0,limit:150};}
   if(u.pathname==='/api/alerts'){if(failLists)status=500;else data=[];}
   if(u.pathname==='/api/products'){if(failLists)status=500;else data={products:[],total:0};}
   if(u.pathname==='/api/admin/users'&&failLists)status=500;
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(status===200?data:{status:'error'})});
  });
  await page.goto('http://127.0.0.1:18084/');
  await page.waitForFunction(()=>typeof loadStats==='function' && typeof fmtPrice==='function');
  await page.evaluate(async()=>{await loadStats();});
  return {page,errors};
 }
 try{
  // U01–U03: бейджи пишет только loadStats (город в запросе); витрина с другим total их не перезаписывает
  let {page,errors}=await open({width:1366,height:768});
  await page.evaluate(async()=>{await loadDiscounts();await loadAnomalies();await loadStats();await loadDiscounts();});
  assert.equal(await page.locator('#badgeDiscountsCount').innerText(),(11732).toLocaleString('ru-RU'));
  assert.equal(await page.locator('#badgeAnomaliesCount').innerText(),'3');
  assert.equal(await page.locator('#metricTotalAlerts').innerText(),(11732).toLocaleString('ru-RU'));
  assert.ok(statsCalls.some(q=>q.includes('city=%D0%90%D1%81%D1%82%D0%B0%D0%BD%D0%B0')),'stats must be city-scoped: '+statsCalls.join(','));
  // U07: ряд вкладок переносится на широких экранах (геометрия со стилями Tailwind проверяется в браузере вручную —
  // офлайн-тест CDN не загружает)
  const tabsRow=await page.evaluate(()=>document.getElementById('tabBtnAnomalies').parentElement.className);
  assert.match(tabsRow,/lg:flex-wrap/);
  assert.deepEqual(errors,[]);
  await page.close();

  // U04: ошибки API видны, бейджи не портятся
  ({page,errors}=await open({width:1366,height:768},{failLists:true}));
  await page.evaluate(async()=>{await loadAnomalies();await loadDiscounts();await loadCatalog(0);await loadAdminUsers();await loadVersionInfo();});
  for(const id of ['anomaliesContainer','discountsContainer','catalogTableBody']){
   const text=await page.locator('#'+id).innerText();
   assert.match(text,/Не удалось загрузить/,id);
   assert.match(text,/Повторить/,id);
  }
  assert.equal(await page.locator('#badgeAnomaliesCount').innerText(),'3');
  // В видимых элементах (не скриптах) нет «undefined» — в том числе при неполных ответах /api/version и /api/admin/users
  const undef=await page.evaluate(()=>[...document.querySelectorAll('body *:not(script)')].filter(e=>e.children.length===0&&e.textContent.includes('undefined')).map(e=>e.id||e.className));
  assert.deepEqual(undef,[]);
  assert.ok(!(await page.locator('#appVersionBadge').innerText()).includes('2.5.0'),'stale hardcoded version');
  assert.deepEqual(errors,[]);
  await page.close();

  // U05: разбивка — три неразрывных пункта (перенос — классами Tailwind, проверен в браузере);
  // U06: 390 px — видно 10 иконок и «+N» (собственный CSS страницы, работает и без Tailwind)
  ({page,errors}=await open({width:390,height:844}));
  const layout=await page.evaluate(()=>({
   items:document.querySelectorAll('#metricAlertsBreakdown > span.whitespace-nowrap').length,
   wrap:document.getElementById('metricAlertsBreakdown').className,
   icons:[...document.querySelectorAll('#activeShopIcons > div')].filter(d=>d.offsetParent!==null).length,
   more:document.getElementById('activeShopIconsMore')?.innerText,
   total:document.querySelectorAll('#activeShopIcons > div').length}));
  assert.equal(layout.items,3); assert.match(layout.wrap,/flex-wrap/);
  assert.equal(layout.icons,10); assert.equal(layout.more,'+'+(layout.total-10));
  await page.setViewportSize({width:1366,height:768});
  assert.equal(await page.evaluate(()=>[...document.querySelectorAll('#activeShopIcons > div')].filter(d=>d.offsetParent!==null).length),layout.total);
  assert.equal(await page.locator('#activeShopIconsMore').isVisible(),false);
  assert.deepEqual(errors,[]);
  await page.close();
  console.log('PASS: P04 counters single writer + city scope, API error states (incl. version/users), shop icons collapse, tile/tab structure');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
