// P05 Settings UX: навигация по ролям, режимы и уровни, индикатор «свои значения», свёрнутые блоки, сброс. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 const saved={detect_zero_glitch:true,detect_super_discount:true,detect_market_arbitrage:true,min_item_price_kzt:12345,
  max_item_price_kzt:3000000,price_glitch_drop_pct:65,min_savings_kzt:40000,arbitrage_min_drop_pct:25,arbitrage_min_diff_kzt:25000,
  junk_keywords:['чехол'],exclude_used_goods:true,search_exclude_accessories_default:true,search_default_sort:'price_asc',
  alert_shops:{kaspi:true},telegram_notify_enabled:true,telegram_notify_level:'ALL'};
 const posts=[];
 try{
  for(const role of ['user','admin']){
   const page=await browser.newPage({viewport:{width:1366,height:900}}); const errors=[];
   await page.addInitScript(()=>{window.tailwind={config:{}}});
   page.on('pageerror',e=>errors.push(e.message));
   page.on('dialog',d=>d.accept());
   await page.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(u.hostname!=='127.0.0.1')return route.abort();
    if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
    if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
    let data={};
    if(u.pathname==='/api/me')data={user:{id:5,is_admin:role==='admin',first_name:'T'},settings:saved,auth:{},shops:{kaspi:'Kaspi'}};
    if(u.pathname.includes('alerts'))data=[];
    if(u.pathname==='/api/me/settings/reset'){posts.push(u.pathname);data={status:'ok',settings:{...saved,min_item_price_kzt:30000,min_savings_kzt:40000}};}
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   });
   await page.goto('http://127.0.0.1:18085/');
   await page.addStyleTag({content:'.hidden { display:none !important; }'});
   await page.waitForFunction(()=>typeof loadSettings==='function');
   await page.evaluate(async()=>{await loadMe(); applyRoleVisibility(); switchTab('settings');});
   // Навигация: две группы; системная — только администратору
   const adminGroupVisible=await page.locator('#settingsNav .admin-only').isVisible();
   assert.equal(adminGroupVisible,role==='admin',role);
   // «Состояние магазинов» больше не в настройках — ссылка на мониторинг
   assert.equal(await page.locator('#adminShopsTable').count(),0);
   if(role==='admin'){
    assert.ok(await page.locator('a[href="/monitoring#shops"]').first().isVisible());
    assert.equal(await page.evaluate(()=>document.getElementById('sec-admin-algo').open),false,'advanced algorithms collapsed');
    await page.evaluate(()=>scrollSettingsTo('sec-admin-algo'));
    assert.equal(await page.evaluate(()=>document.getElementById('sec-admin-algo').open),true);
   }
   // Сохранённые числа не совпадают ни с одним уровнем — «свои значения»; типы — «всё выгодное»
   let summary=await page.locator('#thresholdsSummary').innerText();
   assert.match(summary,/всё выгодное/); assert.match(summary,/свои значения/);
   assert.equal(await page.evaluate(()=>document.getElementById('inputMinPrice').value),'12345');  // старое значение сохранено
   assert.equal(await page.evaluate(()=>document.getElementById('thresholdsAdvanced').open),false);
   // Режим меняет только типы
   await page.evaluate(()=>applyDisplayMode('glitches'));
   const afterMode=await page.evaluate(()=>({z:checkDetectZero.checked,d:checkDetectDiscount.checked,a:checkDetectArbitrage.checked,min:inputMinPrice.value}));
   assert.deepEqual(afterMode,{z:true,d:false,a:false,min:'12345'});
   // Уровень меняет только числа
   await page.evaluate(()=>applySettingsPreset('electronics'));
   const afterLevel=await page.evaluate(()=>({d:checkDetectDiscount.checked,min:inputMinPrice.value,drop:inputDropPct.value}));
   assert.deepEqual(afterLevel,{d:false,min:'25000',drop:'65'});
   summary=await page.locator('#thresholdsSummary').innerText();
   assert.match(summary,/только ошибки цен/); assert.match(summary,/Техника/);
   // Ручное изменение числа — снова «свои значения»
   await page.locator('#thresholdsAdvanced summary').click();
   await page.locator('#inputMinPrice').fill('26000');
   assert.match(await page.locator('#thresholdsSummary').innerText(),/свои значения/);
   // Сброс к умолчаниям
   await page.evaluate(async()=>{await resetMySettings();});
   assert.equal(await page.evaluate(()=>inputMinPrice.value),'30000');
   assert.deepEqual(errors,[]);
   await page.close();
  }
  assert.equal(posts.length,2);
  console.log('PASS: P05 settings — role navigation, shop status moved to monitoring, modes vs levels, custom indicator, advanced collapsed, reset');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
