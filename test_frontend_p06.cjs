// P06 Search Analytics: раздел «Поиск» в мониторинге показывает точные агрегаты, формулу и таблицы запросов. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 const analytics={status:'limited',reason:'Успешных ответов 40 % — людям часто нечего показать',days:7,city:'Все',
  total:50,counts:{found:20,weak:15,not_found:15,error:5},success_rate:40,error_rate:10,avg_results:3.2,
  by_source:{summary:{found:20,weak:15,not_found:15,error:0,total:50,success_rate:40,error_rate:0}},
  top_queries:[{normalized_query:'iphone 15',city:'Астана',searches:20,found:20,weak:0,not_found:0,error:0,success_rate:100}],
  bad_queries:[{normalized_query:'<img src=x onerror=alert(1)>',city:'Алматы',searches:9,found:0,weak:4,not_found:5,error:0,success_rate:0}],
  formula:'Доля успеха = FOUND / (FOUND + WEAK + NOT_FOUND); ошибки в знаменатель не входят и показаны отдельной долей',
  note:'Текст запроса сохраняется начиная с 3-го повтора и только если не похож на личные данные; срок хранения 90 дн.'};
 let searchCalls=0, failSearch=false;
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring/search'){
    searchCalls++;
    if(failSearch)return route.fulfill({status:500,contentType:'application/json',body:'{"status":"error"}'});
    return route.fulfill({contentType:'application/json',body:JSON.stringify(analytics)});
   }
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),sections:{
     overview:{status:'healthy',reason:'ok'},shops:{status:'healthy',reason:'ok',shops:[]},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'Поиск отвечает',total:12,errors:0,not_found:2,by_source:{},p95_ms:120,note:'выборка'},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     system:{status:'healthy',reason:'ok',version:'5.10.0',schema_version:5,python:'3.14',sqlite:'3',
             db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{}}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18086/monitoring#search');
  await page.waitForFunction(()=>document.querySelectorAll('#searchAnalytics *').length>0);
  await page.waitForFunction(()=>!document.getElementById('searchAnalytics').innerText.includes('Загружаю'));
  const box=page.locator('#searchAnalytics');
  const text=await box.innerText();

  // Точные числа и формула видны администратору
  assert.match(text,/Доля успеха/);
  assert.match(text,/40 %/);
  assert.match(text,/Доля ошибок/);
  assert.match(text,/10 %/);
  assert.match(text,/20 \/ 15 \/ 15 \/ 5/,'разбивка исходов: нашлось / слабо / пусто / ошибка');
  assert.match(text,/FOUND \/ \(FOUND \+ WEAK \+ NOT_FOUND\)/,'формула успеха документирована на странице');
  assert.match(text,/ошибки в знаменатель не входят/);
  // Обе таблицы запросов и предупреждение о приватности
  assert.match(text,/Чаще всего ищут/);
  assert.match(text,/iphone 15/);
  assert.match(text,/Плохо отвечаем/);
  assert.match(text,/3-го повтора/);
  // Текст запроса выводится экранированным: запрос приходит от постороннего человека
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#searchAnalytics img').length),0);
  assert.match(text,/<img src=x onerror=alert\(1\)>/);
  // Раздел грузится один раз при открытии и только на своей вкладке
  assert.equal(searchCalls,1);
  await page.evaluate(()=>{document.querySelector('[data-section="ai"]').click();});
  assert.equal(searchCalls,1);

  // Ошибка запроса видна, а не тихая пустота
  failSearch=true;
  await page.evaluate(()=>{document.querySelector('[data-section="search"]').click();});
  await page.waitForFunction(()=>document.getElementById('searchAnalytics').innerText.includes('Не удалось'));
  assert.equal(searchCalls,2);

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: P06 search analytics — точные доли, формула, таблицы запросов, экранирование, состояние ошибки');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
