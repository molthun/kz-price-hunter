// P10 Теневой планировщик: раздел «Сканирование» показывает предложения с объяснением и не даёт команд. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 const suggestions={status:'healthy',reason:'Предложено обойти 2 из 12 источников',candidates:12,
  profiles:{normal:10,degraded:2},
  plan:[{shop:'kaspi',category:'<img src=x onerror=alert(1)>',score:12.5,profile:'normal',age_hours:52.9,
         reason:'не нашли, спрос'},
        {shop:'dns',category:'Телевизоры',score:8.2,profile:'expensive',age_hours:null,reason:'давно не обходили'}],
  skipped:[{shop:'alser',category:'Ноутбуки',skip_reason:'обойдён 2.0 ч назад, минимальный интервал 6 ч'}],
  comparison:{verdict:{unmet_demand_covered:120,watches_covered:8,avg_age_hours:-34.59,violations:0}},
  note:'Это предложение, а не действие: теневой планировщик ничего не обходит и ничего не меняет.'};
 let calls=0, fail=false;
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring/scheduler'){
    calls++;
    if(fail)return route.fulfill({status:500,contentType:'application/json',body:'{"status":"error"}'});
    return route.fulfill({contentType:'application/json',body:JSON.stringify(suggestions)});
   }
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),sections:{
     overview:{status:'healthy',reason:'ok'},shops:{status:'healthy',reason:'ok',shops:[]},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'ok',total:1,errors:0,not_found:0,by_source:{},p95_ms:1,note:''},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     system:{status:'healthy',reason:'ok',version:'5.11.0',schema_version:5,python:'3.14',sqlite:'3',
             db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{}}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18088/monitoring#scanning');
  await page.waitForFunction(()=>document.getElementById('schedulerShadow')
    && !document.getElementById('schedulerShadow').innerText.includes('Считаю'));
  const text=await page.locator('#schedulerShadow').innerText();

  // Предложения видны вместе с причиной и профилем
  assert.match(text,/Теневой планировщик обходов/);
  assert.match(text,/Предложено обойти 2 из 12/);
  assert.match(text,/не нашли, спрос/);
  assert.match(text,/давно не обходили/);
  assert.match(text,/ни разу/,'источник без обходов показан явно');
  // Сравнение стратегий и честная оговорка
  assert.match(text,/120/);
  assert.match(text,/-34.59 ч|-34,59 ч/);
  assert.match(text,/Нарушений ограничений/);
  assert.match(text,/не действие/);
  // Никаких кнопок запуска обхода в теневом разделе
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#schedulerShadow button, #schedulerShadow [data-scan-shop]').length),0);
  // Название категории приходит с сайта магазина — оно должно экранироваться
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#schedulerShadow img').length),0);
  assert.match(text,/<img src=x onerror=alert\(1\)>/);
  assert.equal(calls,1);

  // Ошибка запроса видна словами
  fail=true;
  await page.evaluate(()=>{document.querySelector('[data-section="search"]').click();});
  await page.evaluate(()=>{document.querySelector('[data-section="scanning"]').click();});
  await page.waitForFunction(()=>document.getElementById('schedulerShadow').innerText.includes('Не удалось'));

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: P10 теневой планировщик — предложения с причинами, сравнение стратегий, без кнопок действий');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
