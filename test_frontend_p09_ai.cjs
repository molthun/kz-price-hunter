// P09 AI-часть: режим разбора спорных пар виден и переключается, пороги показаны. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/monitoring.html'),'utf8');
 const thresholds={confidence:0.9,precision:1,recall:0.94,agreed_at:'2026-09-23'};
 let matching={status:'healthy',reason:'Не сравнивается: по разной фасовке 4, по неизвестной 7',days:7,
  blocked:4,uncertain:7,rows:[{kind:'uncertain',reason:'фасовка указана только у одного предложения',
   count:7,example_left:'Whiskas 85 г',example_right:'Whiskas'}],
  note:'Это наблюдение, а не тревога.',
  ai:{mode:'off',mode_label:'выключено: модель не вызывается',modes:['off','shadow','on'],
      pending:7,decided:0,same:0,different:0,examples:[],thresholds,
      note:'Модель спрашивают только о спорных парах.'}};
 const switches=[];
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url()), req=route.request();
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring/matching/ai'){
    const body=JSON.parse(req.postData()||'{}'); switches.push(body.mode);
    if(body.mode==='on'&&matching.ai.decided===0)
      return route.fulfill({status:400,contentType:'application/json',
        body:JSON.stringify({status:'error',message:'Пороги не проверены: сначала тень'})});
    const labels={off:'выключено: модель не вызывается',shadow:'тень: модель отвечает, сравнение цен не меняется',
      on:'включено: уверенное решение модели разрешает сравнение'};
    matching={...matching,ai:{...matching.ai,mode:body.mode,mode_label:labels[body.mode]}};
    return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',ai:matching.ai})});
   }
   if(u.pathname==='/api/admin/monitoring/matching')
     return route.fulfill({contentType:'application/json',body:JSON.stringify(matching)});
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),open_incidents:[],shops:[],sections:{
     overview:{status:'healthy',reason:'ok'},shops:{status:'healthy',reason:'ok',shops:[],counts:{healthy:1}},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'ok',total:1,errors:0,not_found:0,by_source:{},p95_ms:1,note:''},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     events:{status:'healthy',reason:'ok'},
     system:{status:'healthy',reason:'ok',version:'5.12.0',schema_version:5,python:'3.14',sqlite:'3',
             db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{},recent_errors:[],note:''}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18095/monitoring#system');
  await page.waitForFunction(()=>document.getElementById('matchingShadow')
    && !(document.getElementById('matchingShadow')?.innerText||'').includes('Загружаю'));
  let text=await page.locator('#matchingShadow').innerText();

  // Режим и согласованные пороги видны сразу
  assert.match(text,/выключено: модель не вызывается/);
  assert.match(text,/Ждут разбора/);
  assert.match(text,/уверенность 0.9|уверенность 0,9/);
  assert.match(text,/от 2026-09-23/);
  assert.match(text,/только о спорных парах/);

  // Переключение в тень
  await page.click('[data-matching-ai="shadow"]');
  await page.waitForFunction(()=>(document.getElementById('matchingShadow')?.innerText||'').includes('сравнение цен не меняется'));
  assert.equal(switches.at(-1),'shadow');

  // Включение без разобранных пар сервер отклоняет — это видно человеку
  const alerts=[];
  page.on('dialog',d=>{alerts.push(d.message());});
  await page.click('[data-matching-ai="on"]');
  await page.waitForFunction(()=>(document.getElementById('matchingShadow')?.innerText||'').includes('сравнение цен не меняется'));
  assert.equal(switches.at(-1),'on');

  // Решения модели показываются с уверенностью и причиной, названия экранируются
  matching={...matching,ai:{...matching.ai,decided:3,same:2,different:1,examples:[
    {left_title:'<img src=x onerror=alert(1)>',right_title:'Whiskas',same:true,confidence:0.95,
     reason:'та же фасовка',provider:'gemini',decided_at:'2026-09-23T10:00:00+00:00',seen:4}]}};
  await page.click('[data-matching-ai="shadow"]');
  await page.waitForFunction(()=>(document.getElementById('matchingShadow')?.innerText||'').includes('Разобрано'));
  await page.click('#matchingShadow summary:has-text("Последние решения")');
  text=await page.locator('#matchingShadow').innerText();
  assert.match(text,/один товар/);
  assert.match(text,/та же фасовка/);
  assert.match(text,/<img src=x onerror=alert\(1\)>/);
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#matchingShadow img').length),0);

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: P09 AI-часть — режим, согласованные пороги, решения модели, экранирование');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
