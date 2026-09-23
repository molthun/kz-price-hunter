// P13 Суточная сводка: цифры видны без AI, пересказ с выдуманными числами не показывается. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 const report={day:'2026-09-22',tz:'UTC',partial:false,empty:false,unavailable:{},summary:null,
  window:{from:'2026-09-22T00:00:00+00:00',to:'2026-09-23T00:00:00+00:00',exact_utc_day:true},
  note:'Все числа получены расчётом по собственным данным.',
  blocks:{search:{searches:3,success_rate:66.7,approximate:false},
          catalog:{new_products:1,price_changes:1,cheaper:1,dearer:0,
                   biggest_drops:[{title:'<img src=x onerror=alert(1)>',was:100000,now:90000,drop_pct:10}]},
          telegram:{sent:4,attempts:5},
          ai:{requests:1,cost_usd:null,approximate:false},
          shops:{scans:1,degraded:0,recovered:1}}};
 let summary=null, days=[{day:'2026-09-22',partial:false},{day:'2026-09-21',partial:false}], asked=[];
 let telegram={enabled:false,hour:10,tz:'Asia/Almaty',recipients:1,note:'Отправка в Telegram выключена'};
 const switches=[];
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url()), req=route.request();
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring/daily/summary'){
    asked.push(JSON.parse(req.postData()||'{}'));
    return route.fulfill({contentType:'application/json',body:JSON.stringify(summary)});
   }
   if(u.pathname==='/api/admin/monitoring/daily/telegram'){
    const body=JSON.parse(req.postData()||'{}'); switches.push(body);
    if(body.hour===25)return route.fulfill({status:400,contentType:'application/json',
      body:JSON.stringify({status:'error',message:'Час суточной сводки: допустимо от 0 до 23'})});
    telegram={...telegram,...body,note:body.enabled===false?'Отправка в Telegram выключена'
      :'Сводка уходит администраторам после указанного часа, один раз за сутки'};
    return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',telegram})});
   }
   if(u.pathname==='/api/admin/monitoring/daily'){
    asked.push({day:u.searchParams.get('day'),refresh:u.searchParams.get('refresh')});
    return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',report,days,telegram})});
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
  await page.goto('http://127.0.0.1:18093/monitoring#overview');
  await page.waitForFunction(()=>document.getElementById('dailyBox')
    && !document.getElementById('dailyBox').innerText.includes('Собираю'));
  let text=await page.locator('#dailyBox').innerText();

  // Цифры видны сами по себе, без всякого пересказа
  assert.match(text,/Сводка за сутки 2026-09-22/);
  assert.match(text,/успех 66.7 %|успех 66,7 %/);
  assert.match(text,/дешевле 1, дороже 0/);
  assert.match(text,/4 \/ 5/,'отправлено из попыток');
  assert.match(text,/цены не заданы/,'стоимость без цен — не ноль');
  assert.match(text,/0 \/ 1/,'ухудшений и восстановлений');
  // Название товара приходит с сайта магазина — оно экранируется
  await page.click('#dailyBox summary');
  text=await page.locator('#dailyBox').innerText();
  assert.match(text,/<img src=x onerror=alert\(1\)>/);
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#dailyBox img').length),0);

  // Пересказ с выдуманными числами не показывается, цифры остаются
  summary={status:'ok',summary:null,rejected:'в пересказе есть числа, которых нет в отчёте: 37, 4200',provider:'gemini'};
  await page.click('#btnDailySummary');
  await page.waitForFunction(()=>document.getElementById('dailySummary').innerText.includes('не показан'));
  text=await page.locator('#dailyBox').innerText();
  assert.match(text,/37, 4200/);
  assert.match(text,/Цифры выше остаются в силе/);
  assert.match(text,/дешевле 1, дороже 0/);

  // Хороший пересказ показывается рядом с цифрами
  summary={status:'ok',summary:'Поисков 3, новых товаров 1. Вероятно, день был спокойным.',rejected:null,provider:'gemini'};
  await page.click('#btnDailySummary');
  await page.waitForFunction(()=>document.getElementById('dailySummary').innerText.includes('Вероятно'));

  // Неполные сутки названы неполными, пустые — отсутствием данных
  report.partial=true; report.empty=true;
  report.note='Сутки ещё не закончились — цифры неполные.';
  await page.click('#btnDailyRefresh');
  await page.waitForFunction(()=>document.getElementById('dailyBox').innerText.includes('не закончились'));
  text=await page.locator('#dailyBox').innerText();
  assert.match(text,/не ноль достижений/);
  assert.equal(asked.at(-1).refresh,'1','пересчёт запрашивается явно');

  // Выключатель отправки в Telegram: состояние видно, включение сохраняется, час меняется
  report.partial=false; report.empty=false; report.note='Все числа посчитаны кодом.';
  await page.click('#btnDailyRefresh');
  await page.waitForFunction(()=>document.getElementById('dailyTelegram'));
  assert.equal(await page.evaluate(()=>document.getElementById('dailyTelegram').checked),false);
  assert.match(await page.locator('#dailyBox').innerText(),/Отправка в Telegram выключена/);
  assert.match(await page.locator('#dailyBox').innerText(),/Asia\/Almaty/);

  await page.check('#dailyTelegram');
  await page.waitForFunction(()=>document.getElementById('dailyBox').innerText.includes('один раз за сутки'));
  assert.deepEqual(switches.at(-1),{enabled:true});
  assert.equal(await page.evaluate(()=>document.getElementById('dailyTelegram').checked),true);

  await page.selectOption('#dailyHour','8');
  await page.waitForFunction(()=>document.getElementById('dailyHour').value==='8');
  assert.deepEqual(switches.at(-1),{hour:8});

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: P13 суточная сводка — цифры без AI, отклонённый пересказ, неполные сутки, отправка в Telegram');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
