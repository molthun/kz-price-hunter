// R24-01…03 (приёмка 6.40.24): состояние и подписи наблюдений на настоящем API.
// Настоящий сервер на временной БД (SQLite -> API -> браузер). Выключенное наблюдение (is_active=0
// от API) не подсвечивает карточку и Избранное и не попадает в «Активные»; best_price подписан как
// минимум за время наблюдения; все восемь видов KINDS имеют свою подпись.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..','..');

async function startServer(){
 const child=spawn(process.env.TEST_PYTHON||path.join(ROOT,'venv/bin/python'),['-u','-m','tests.web.test_browser_server'],
  {cwd:ROOT,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',BROWSER_TEST_SEED:'deals'},stdio:['ignore','pipe','pipe']});
 const base=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Test server startup timeout')),20000);
  child.stdout.on('data',c=>{output+=c;const m=output.match(/BROWSER_TEST_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(m){clearTimeout(timer);resolve(m[1])}});
  child.on('exit',code=>{clearTimeout(timer);reject(Error('Test server exited '+code))});
 });
 return {child,base};
}

(async()=>{
 const browser=await chromium.launch({channel:'chrome'});
 const {child,base}=await startServer();
 try{
  for(const [width,colorScheme] of [[390,'light'],[1366,'dark']]){
   const mobile=width<768;
   const context=await browser.newContext({viewport:{width,height:844},colorScheme,locale:'en-US',isMobile:mobile,hasTouch:mobile});
   const page=await context.newPage();const errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
   const api=(url,body,method)=>page.evaluate(async([u,b,m])=>{
    const r=await fetch(u,b===undefined&&!m?{}:{method:m||'POST',headers:{'Content-Type':'application/json'},body:b===undefined?undefined:JSON.stringify(b)});
    return {status:r.status,data:await r.json().catch(()=>null)}},[url,body,method]);
   const label=`${width}/${colorScheme}`;

   await page.goto(base+'/profile');
   assert.equal((await api('/api/auth/dev-login',{})).status,200);
   // Чистый лист: наблюдения предыдущего прохода удаляются настоящим DELETE
   for(const w of (await api('/api/me/watches')).data.watches)await api('/api/me/watches/'+w.id,undefined,'DELETE');
   await api('/api/me/favorites',undefined,'DELETE');
   const deal=(await api('/api/v2/deals?city='+encodeURIComponent('Алматы'))).data.deals[0];
   const pid=deal.canonical_product_id;
   const product=base+'/product/'+pid+'?city='+encodeURIComponent('Алматы');
   const openProduct=async()=>{await page.goto(product);await page.waitForFunction(()=>AppState.currentProduct&&WatchManager.status==='ok');};
   const button=()=>page.evaluate(()=>({text:document.getElementById('detailWatchText').textContent,
    active:document.getElementById('detailWatchBtn').classList.contains('watch-active')}));
   const modal=async()=>{
    await page.locator('#detailWatchBtn').click();
    const box=page.locator('#watchExistingList');
    const out={hidden:await box.evaluate(el=>el.hidden),rows:await page.locator('.watch-existing-row').count(),text:await box.innerText()};
    await page.locator('#watchForm .btn-secondary').click();
    return out;
   };
   const favHighlighted=async()=>{
    await page.goto(base+'/favorites');await page.locator('.fav-card').first().waitFor();
    await page.waitForFunction(()=>WatchManager.status==='ok');
    return page.evaluate(()=>document.querySelector('.fav-card .fav-action-icon-btn[aria-label="Следить за ценой"]').classList.contains('watch-active'));
   };

   // Активное наблюдение с порогом: подсветка, текст, список в окне, Избранное
   const created=await api('/api/me/watches',{kind:'product',target:pid,title:deal.title,condition:'target_price',threshold:5000,city:'Алматы'});
   assert.equal(created.status,200,label);const id=created.data.watch.id;
   await openProduct();
   await page.locator('#detailFavBtn').click();
   assert.deepEqual(await button(),{text:'Следим (≤ '+await page.evaluate(()=>formatPrice(5000))+')',active:true},label);
   assert.equal((await modal()).rows,1,label);
   assert.equal(await favHighlighted(),true,label+': Избранное');

   // R24-01: выключено поддерживаемым API → после reload ни карточка, ни Избранное, ни «Активные»
   const off=await api('/api/me/watches/'+id,{is_active:false});
   assert.equal(off.status,200);assert.equal(off.data.watch.is_active,0,'API отдаёт число SQLite');
   await openProduct();
   assert.deepEqual(await button(),{text:'Следить за ценой',active:false},label+': выключенное не «Следим»');
   const m=await modal();
   assert.equal(m.hidden,true,label);assert.equal(m.rows,0,label);
   assert.equal(await favHighlighted(),false,label+': Избранное не подсвечено');
   await page.goto(base+'/profile');await page.locator('.watch-item-card').first().waitFor();
   assert.match(await page.locator('#profileWatchesList').innerText(),/Выключено/,label+': профиль честно помечает');

   // Смешанный список: в «Активные» попадает только включённая запись
   const second=await api('/api/me/watches',{kind:'product',target:pid,title:deal.title,condition:'any_drop',city:'Астана'});
   assert.equal(second.status,200);
   await openProduct();
   assert.deepEqual(await button(),{text:'Вы следите',active:true},label);
   const mixed=await modal();
   assert.equal(mixed.rows,1,label);assert.match(mixed.text,/Астана/);assert.doesNotMatch(mixed.text,/5\s?000/);

   // Одноразовое наблюдение после срабатывания: движок хранит is_active=0, fired=true
   await page.route('**/api/me/watches',async r=>{
    if(r.request().method()!=='GET')return r.continue();
    const resp=await r.fetch();const data=await resp.json();
    data.watches=data.watches.map(w=>({...w,is_active:0,fired:true,repeat:false,fired_at:'2026-11-19T10:00:00'}));
    return r.fulfill({response:resp,json:data});
   });
   await openProduct();
   assert.deepEqual(await button(),{text:'Следить за ценой',active:false},label+': сработавшее одноразовое');
   assert.equal((await modal()).rows,0,label);
   // Неизвестное состояние (поля нет) не считается подтверждённо активным
   await page.unroute('**/api/me/watches');
   await page.route('**/api/me/watches',async r=>{
    if(r.request().method()!=='GET')return r.continue();
    const resp=await r.fetch();const data=await resp.json();
    data.watches=data.watches.map(({is_active,...w})=>w);
    return r.fulfill({response:resp,json:data});
   });
   await openProduct();
   assert.equal((await button()).active,false,label+': без is_active');
   await page.unroute('**/api/me/watches');

   // R24-02: best_price — минимум за время наблюдения, как в описании API
   const best=await api('/api/me/watches/'+id,{condition:'best_price',threshold:null,is_active:true});
   assert.equal(best.status,200);
   await page.goto(base+'/profile');await page.locator('.watch-item-card').first().waitFor();
   const bp=await page.evaluate(()=>{const w=WatchManager.items.find(x=>x.condition==='best_price');
    return {api:w.description,ui:formatWatchCondition(w),profile:document.getElementById('profileWatchesList').textContent};});
   assert.match(bp.api,/лучшая цена за всё время/);
   assert.equal(bp.ui,'Новый минимум цены за время наблюдения');
   assert.doesNotMatch(bp.profile,/на рынке/,label);
   await openProduct();
   const bm=await modal();assert.match(bm.text,/минимум цены за время наблюдения/);assert.doesNotMatch(bm.text,/на рынке/);

   // R24-03: все восемь видов; настоящие DTO после reload
   const kinds={category:'🗂 Категория',shop:'🏪 Магазин',seller:'👤 Продавец',arbitrage:'⚖️ Разница цен',
    search:'🔍 Поиск',model:'📱 Модель',deal:'🏷 Скидка'};
   for(const kind of Object.keys(kinds)){
    const alert=kind==='arbitrage'||kind==='deal';
    const r=await api('/api/me/watches',{kind,target:alert?'все':'phones '+kind,condition:alert?'any_find':'any_drop'});
    assert.equal(r.status,200,kind+': '+JSON.stringify(r.data));assert.equal(r.data.watch.kind,kind);
   }
   await page.goto(base+'/profile');await page.locator('.watch-item-card').first().waitFor();
   const badges=await page.evaluate(()=>WatchManager.items.map((w,i)=>({kind:w.kind,
    badge:document.querySelectorAll('.watch-item-card')[i].querySelector('.watch-kind-badge').textContent})));
   for(const {kind,badge} of badges)assert.equal(badge,kind==='product'?'📦 Товар':kinds[kind],label+': '+kind);
   assert.deepEqual([...new Set(badges.map(b=>b.kind))].sort(),[...Object.keys(kinds),'product'].sort());
   assert.equal(await page.evaluate(()=>watchKindLabel('unknown_kind')),'🔔 Наблюдение');
   assert.equal(await page.evaluate(()=>watchKindLabel('__proto__')),'🔔 Наблюдение');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label);

   assert.deepEqual(errors,[],label);
   console.log(`PASS watch states ${label}`);
   await context.close();
  }
 }finally{await browser.close();child.kill('SIGINT');}
})().catch(e=>{console.error(e);process.exitCode=1;});
