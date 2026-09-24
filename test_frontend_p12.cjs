// P12 Помощник администратора: ответ показывается только с проверенными цифрами, факты видны всегда. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 const facts='• Обходы магазинов и HTTP (7 дн.)\n  dns/<img src=x onerror=alert(1)>: принято 100 товаров';
 const note='Цифры собраны кодом из отчётов мониторинга; модель только объясняет их словами.';
 let asked=[], reply=null, status=200;
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url()), req=route.request();
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/assistant'){
    asked.push(JSON.parse(req.postData()||'{}'));
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(reply)});
   }
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),open_incidents:[],shops:[],sections:{
     overview:{status:'healthy',reason:'ok'},shops:{status:'healthy',reason:'ok',shops:[],counts:{healthy:1}},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'ok',total:1,errors:0,not_found:0,by_source:{},p95_ms:1,note:''},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     events:{status:'healthy',reason:'ok'},
     system:{status:'healthy',reason:'ok',version:'5.12.0',schema_version:5,python:'3.14',sqlite:'3',
             db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{}}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18092/monitoring#overview');
  await page.waitForSelector('#btnAsk');

  // Пустой вопрос не уходит на сервер
  await page.click('#btnAsk');
  await page.waitForFunction(()=>(document.getElementById('askResult')?.innerText||'').includes('Задайте вопрос'));
  assert.equal(asked.length,0,'пустой вопрос не должен отправляться');

  // Обычный ответ: текст модели, факты и оговорка рядом
  reply={status:'ok',answer:'Факты: последний обход dns принёс 100 товаров вместо 2000.',
         summary:facts,rejected:null,ai:'gemini',note};
  await page.fill('#askQ','почему упал каталог dns');
  await page.selectOption('#askDays','30');
  await page.click('#btnAsk');
  await page.waitForFunction(()=>(document.getElementById('askResult')?.innerText||'').includes('100 товаров'));
  let text=await page.locator('#askResult').innerText();
  assert.match(text,/вместо 2000/);
  assert.match(text,/принято 100 товаров/,'факты показываются вместе с ответом');
  assert.match(text,/только объясняет их словами/);
  assert.deepEqual(asked.at(-1),{question:'почему упал каталог dns',days:30});

  // Название категории приходит с сайта магазина — оно экранируется
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#askResult img').length),0);
  assert.match(text,/<img src=x onerror=alert\(1\)>/);

  // Ответ с выдуманными числами не показывается, факты остаются
  reply={status:'ok',answer:null,summary:facts,rejected:'в ответе есть числа, которых нет в данных: 73, 987',
         ai:'gemini',note};
  await page.click('#btnAsk');
  await page.waitForFunction(()=>(document.getElementById('askResult')?.innerText||'').includes('не показан'));
  text=await page.locator('#askResult').innerText();
  assert.match(text,/которых нет в данных: 73, 987/);
  assert.match(text,/принято 100 товаров/,'факты остаются видимыми');
  assert.doesNotMatch(text,/вместо 2000/,'отклонённый ответ не показывается');

  // AI выключен — сервис всё равно отвечает фактами
  reply={status:'ok',answer:null,summary:facts,rejected:null,ai:'AI недоступен: ai_disabled',note};
  await page.click('#btnAsk');
  await page.waitForFunction(()=>(document.getElementById('askResult')?.innerText||'').includes('AI недоступен'));
  text=await page.locator('#askResult').innerText();
  assert.match(text,/принято 100 товаров/);

  // Ошибка сервера видна словами
  status=403; reply={status:'error',message:'Доступ только для администратора'};
  await page.click('#btnAsk');
  await page.waitForFunction(()=>(document.getElementById('askResult')?.innerText||'').includes('только для администратора'));

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: P12 помощник администратора — факты всегда видны, выдуманные числа не показываются');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
