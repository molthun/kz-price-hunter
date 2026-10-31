// Ручная модерация спорных пар: решение уходит на сервер, отказ с участниками группы и сбой видны оператору. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const ROOT=path.resolve(__dirname,'..','..');
const assert=require('node:assert/strict');
const pair=(ph,a,b)=>({pair_hash:ph,title_a:a,title_b:b,reason:'test',status:'pending',attempts:0,key_a:'k:a',key_b:'k:b',
 fields_a:null,fields_b:null,offers_a:[],offers_b:[],verdict:null,same_key:false});
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(ROOT,'web/templates/monitoring.html'),'utf8');
 let items=[pair('ph1','Galaxy S24 128GB <img src=x onerror=alert(1)>','Galaxy S24 128GB white'),pair('ph2','EarPods USB-C','EarPods Lightning')];
 const sent=[]; let reply=null;
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[], alerts=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>{alerts.push(d.message());d.accept()});
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url()), req=route.request();
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/matching/stats')return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',stats:{pending:items.length,human_same:0,human_different:0,total:2}})});
   if(u.pathname==='/api/admin/matching/disputed')return route.fulfill({contentType:'application/json',body:JSON.stringify({items,total:items.length,counts:{}})});
   if(u.pathname==='/api/admin/matching/verdict'){
    sent.push(JSON.parse(req.postData()));
    return route.fulfill({status:reply.status,contentType:'application/json',body:JSON.stringify(reply.body)});
   }
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify({
    status:'healthy',generated_at:new Date().toISOString(),open_incidents:[],shops:[],sections:{
     overview:{status:'healthy',reason:'ok'},matching:{status:'limited',reason:'Ожидают решения: 2',counts:{}},shops:{status:'healthy',reason:'ok',shops:[],counts:{healthy:1}},
     scanning:{status:'healthy',reason:'ok',state:{},recent:[]},
     search:{status:'healthy',reason:'ok',total:1,errors:0,not_found:0,by_source:{},p95_ms:1,note:''},
     ai:{status:'unknown',reason:'нет данных',outcomes:{},counts:{}},
     telegram:{status:'unknown',reason:'нет данных',delivery_24h:{},outbox:{},note:''},
     events:{status:'healthy',reason:'ok'},
     system:{status:'healthy',reason:'ok',version:'5.12.0',schema_version:5,python:'3.14',sqlite:'3',db_size_bytes:1,counts:{products:1,active_products:1},telemetry:{}}}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18092/monitoring#matching');
  await page.waitForSelector('[data-matching-action="same"][data-pair-hash="ph1"]');
  assert.equal(await page.evaluate(()=>document.querySelectorAll('#matchingPairsContainer img').length),0,'название экранируется');

  // Отказ сервера: оператор видит причину и участников, пара остаётся в списке
  reply={status:400,body:{status:'error',message:'В группах есть другие товары, которые противоречат этому решению',conflicts:['Galaxy S24 256GB blue']}};
  await page.fill('#note_ph1','проверено');
  await page.click('[data-matching-action="same"][data-pair-hash="ph1"]');
  await page.waitForTimeout(100);
  assert.deepEqual(sent.at(-1),{pair_hash:'ph1',decision:'same',notes:'проверено'});
  assert.match(alerts.at(-1),/противоречат/);
  assert.match(alerts.at(-1),/256GB blue/,'участник группы назван');
  assert.equal(await page.locator('[data-pair-hash="ph1"]').count()>0,true);

  // Сбой записи на сервере не выглядит успехом
  reply={status:500,body:{}};
  await page.click('[data-matching-action="different"][data-pair-hash="ph2"]');
  await page.waitForTimeout(100);
  assert.equal(sent.at(-1).decision,'different');
  assert.match(alerts.at(-1),/Ошибка сохранения/);

  // Успех: список перечитывается, решённая пара исчезает
  const count=alerts.length; reply={status:200,body:{status:'ok',ok:true,decision:'different'}}; items=[items[0]];
  await page.click('[data-matching-action="different"][data-pair-hash="ph2"]');
  await page.waitForFunction(()=>!document.querySelector('[data-pair-hash="ph2"]'));
  assert.equal(alerts.length,count,'успех без сообщения об ошибке');
  assert.deepEqual(errors,[]);
  console.log('PASS: ручная модерация — отказ с участниками группы, сбой записи и успешное решение');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
