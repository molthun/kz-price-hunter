// Раздел «Обращения магазинов к парсерам» в Центре мониторинга. Офлайн.
// Главное здесь — экранирование: текст приходит с сайта магазина, это чужой текст, не наш.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 let notices={status:'healthy',reason:'Найдено обращений: 1. Магазин может предлагать готовую выгрузку',
  last_check_at:'2026-09-25T09:00:00+00:00',interval_hours:168,generated_at:'2026-09-25T10:00:00+00:00',
  note:'Проверяются главная страница магазина и его собственные скрипты.',
  items:[{shop_key:'shopkz',host:'shop.kz',
    notices:[{text:'Если вы парсите сайт <img src=x onerror=alert(1)>, воспользуйтесь выгрузкой.',
              source:'bundle.js',url:'https://shop.kz/bitrix/catalog_export/yandex.php'}],
    links:['https://shop.kz/bitrix/catalog_export/yandex.php'],
    first_seen_at:'2026-09-25T09:00:00+00:00',last_changed_at:'2026-09-25T09:00:00+00:00',
    last_checked_at:'2026-09-25T09:00:00+00:00'}]};
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  const alerts=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>{alerts.push(d.message);d.dismiss();});
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring/notices')
     return route.fulfill({contentType:'application/json',body:JSON.stringify(notices)});
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),open_incidents:[],shops:[],sections:{
     overview:{status:'healthy',reason:'ok'},shops:{status:'healthy',reason:'ok',shops:[],counts:{healthy:1}},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'ok',total:1,errors:0,not_found:0,by_source:{},p95_ms:1,note:''},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     events:{status:'healthy',reason:'ok'},
     system:{status:'healthy',reason:'ok',version:'5.31.1',schema_version:5,python:'3.14',sqlite:'3',
             db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{}}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18099/monitoring#system');
  await page.waitForFunction(()=>document.getElementById('noticesBox')
    && !(document.getElementById('noticesBox')?.innerText||'').includes('Смотрю обращения'));
  const text=await page.locator('#noticesBox').innerText();

  // Обращение видно целиком, вместе с сайтом и ссылкой на выгрузку
  assert.match(text,/Обращения магазинов к парсерам/);
  assert.match(text,/shop\.kz/);
  assert.match(text,/воспользуйтесь выгрузкой/,'цитата магазина показана');
  assert.match(text,/catalog_export\/yandex\.php/,'ссылка рядом с обращением важнее самой фразы');
  assert.match(text,/Проверяем раз в\s*7 дн\./,'периодичность видна');

  // Текст магазина — чужой: он показывается как текст, а не исполняется
  assert.deepEqual(alerts,[],'скрипт из текста магазина не должен выполняться');
  const injected=await page.evaluate(()=>document.querySelectorAll('#noticesBox img').length);
  assert.equal(injected,0,'разметка из чужого текста не должна попадать в DOM');
  assert.match(text,/<img src=x onerror=alert\(1\)>/,'чужая разметка видна как текст');

  // Пока проверка не проходила — «неизвестно», а не «обращений нет»
  notices={status:'unknown',reason:'Проверка ещё не выполнялась',items:[],last_check_at:null,
           interval_hours:168,note:''};
  await page.evaluate(()=>loadNotices());
  await page.waitForFunction(()=>(document.getElementById('noticesBox')?.innerText||'').includes('ещё не выполнялась'));
  const empty=await page.locator('#noticesBox').innerText();
  assert.match(empty,/Обращений пока не найдено/);
  assert.doesNotMatch(empty,/Найдено обращений: [1-9]/);

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: раздел обращений магазинов, ссылка на выгрузку, чужой текст экранирован');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
