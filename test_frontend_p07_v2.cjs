// P07 V2: кнопка «следить» прямо на находке и в поиске — состояние, создание, отмена, гость. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 const alert={id:1,product_id:'p-1',title:'Смартфон <img src=x onerror=alert(1)>',shop:'Kaspi',city:'Астана',
  url:'https://kaspi.kz/p1',image_url:'',category:'Смартфоны',alert_type:'SUPER_DISCOUNT',
  old_price:200000,new_price:100000,discount_pct:50,savings_kzt:100000,canonical_key:'k1',price:100000};
 let watches=[]; let nextId=11; const calls=[]; let failCreate=false;
 try{
  for(const role of ['user','guest']){
   const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'}); const errors=[];
   await page.addInitScript(()=>{window.tailwind={config:{}}});
   page.on('pageerror',e=>errors.push(e.message));
   page.on('dialog',d=>d.accept());
   await page.route('**/*',async route=>{
    const u=new URL(route.request().url()); const method=route.request().method();
    if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
    if(u.hostname!=='127.0.0.1')return route.abort();
    if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
    if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
    if(u.pathname==='/api/me')return route.fulfill({contentType:'application/json',body:JSON.stringify(
      role==='guest'?{user:null,settings:{},auth:{},shops:{}}
                    :{user:{id:5,is_admin:false,first_name:'T'},settings:{},auth:{},shops:{}})});
    if(u.pathname==='/api/me/watches'&&method==='GET')
      return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',watches,events:[],
        limits:{max:50},kinds:[],conditions:[],labels:{}})});
    if(u.pathname==='/api/me/watches'&&method==='POST'){
      const body=JSON.parse(route.request().postData()||'{}'); calls.push(body);
      if(failCreate)return route.fulfill({status:400,contentType:'application/json',
        body:JSON.stringify({status:'error',message:'Больше 50 наблюдений нельзя'})});
      const watch={...body,id:nextId++,is_active:1,description:'товар'};
      watches=watches.concat([watch]);
      return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',watch})});
    }
    if(u.pathname.startsWith('/api/me/watches/')&&method==='DELETE'){
      const id=Number(u.pathname.split('/').pop()); calls.push('delete'+id);
      watches=watches.filter(w=>w.id!==id);
      return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok'})});
    }
    if(u.pathname.includes('/api/alerts'))return route.fulfill({contentType:'application/json',body:JSON.stringify([alert])});
    if(u.pathname.includes('alerts'))return route.fulfill({contentType:'application/json',body:JSON.stringify([alert])});
    return route.fulfill({contentType:'application/json',body:'{}'});
   });
   await page.goto('http://127.0.0.1:18094/');
   await page.waitForFunction(()=>typeof watchButton==='function');

   if(role==='guest'){
    // Гостю кнопка не предлагается: наблюдение принадлежит вошедшему человеку
    assert.equal(await page.evaluate(()=>watchButton({id:'p-1',title:'x'})),'');
    assert.equal(await page.evaluate(()=>watchIconButton({id:'p-1',title:'x'})),'');
    await page.close();
    continue;
   }

   // Кнопка появляется на карточке находки и сначала предлагает следить
   const fixture=await page.evaluate(a=>{
     const box=document.createElement('div'); box.id='cardFixture'; document.body.append(box);
     box.innerHTML=renderAlertCard(a);
     const btn=box.querySelector('[data-watch-product]');
     return {present:!!btn, text:btn?btn.innerText:'', imgs:box.querySelectorAll('img[onerror*="alert(1)"]').length};
   },alert);
   assert.ok(fixture.present,'кнопка «следить» должна быть на карточке');
   assert.match(fixture.text,/Следить за ценой/);
   assert.equal(fixture.imgs,0,'название товара экранируется');

   // Клик создаёт наблюдение за этим товаром с понятными значениями по умолчанию
   await page.click('#cardFixture [data-watch-product]');
   await page.waitForFunction(()=>document.querySelector('#cardFixture [data-watch-product]').innerText.includes('Слежу'));
   assert.deepEqual(calls.at(-1),{kind:'product',target:'p-1',title:alert.title,condition:'any_drop',repeat:true});

   // Повторный клик снимает наблюдение
   await page.click('#cardFixture [data-watch-product]');
   await page.waitForFunction(()=>document.querySelector('#cardFixture [data-watch-product]').innerText.includes('Следить за ценой'));
   assert.equal(calls.at(-1),'delete11');

   // Отказ сервера виден словами, состояние кнопки не врёт
   failCreate=true;
   await page.click('#cardFixture [data-watch-product]');
   await page.waitForFunction(()=>document.getElementById('globalToast').innerText.includes('нельзя'));
   assert.match(await page.evaluate(()=>document.querySelector('#cardFixture [data-watch-product]').innerText),/Следить за ценой/);
   failCreate=false;

   // Уже наблюдаемый товар показывается как наблюдаемый сразу при отрисовке
   watches=[{id:21,kind:'product',target:'p-1',is_active:1,description:'товар'}];
   await page.evaluate(()=>loadWatches());
   await page.waitForFunction(()=>Object.keys(watchedProducts).length===1);
   const fresh=await page.evaluate(a=>{
     const box=document.getElementById('cardFixture'); box.innerHTML=renderAlertCard(a);
     return box.querySelector('[data-watch-product]').innerText;
   },alert);
   assert.match(fresh,/Слежу за ценой/);

   // Компактная кнопка в строке поиска — с подписью для чтения с экрана
   const icon=await page.evaluate(()=>{
     const box=document.getElementById('cardFixture');
     box.innerHTML=watchIconButton({id:'p-1',title:'Смартфон'});
     const b=box.querySelector('[data-watch-product]');
     return {label:b.getAttribute('aria-label'),icon:b.dataset.watchIcon};
   });
   assert.equal(icon.icon,'1');
   assert.match(icon.label,/Перестать следить/);

   // Форма знает про новые виды: у сделок своя цель и свои условия
   await page.selectOption('#watchKind','deal');
   await page.waitForFunction(()=>!document.getElementById('watchTargetHint').classList.contains('hidden'));
   assert.match(await page.locator('#watchTargetHint').innerText(),/все. или категорию/);
   assert.equal(await page.evaluate(()=>document.getElementById('watchCondition').value),'any_find');
   assert.equal(await page.evaluate(()=>[...document.getElementById('watchCondition').options]
     .filter(o=>!o.hidden).map(o=>o.value).join(',')),'target_price,any_find,discount_pct');
   // Пустая цель для сделок означает «все», а не ошибку
   calls.length=0;
   await page.click('button:has-text("Добавить наблюдение")');
   await page.waitForFunction(()=>window.__calls===undefined);
   assert.equal(calls.at(-1).target,'все');
   assert.equal(calls.at(-1).kind,'deal');
   // У обычного вида условия находок не предлагаются
   await page.selectOption('#watchKind','model');
   assert.equal(await page.evaluate(()=>[...document.getElementById('watchCondition').options]
     .filter(o=>!o.hidden).map(o=>o.value).includes('any_find')),false);
   assert.equal(await page.evaluate(()=>document.getElementById('watchCondition').value),'any_drop');

   assert.deepEqual(errors,[],'ошибок JS быть не должно');
   await page.close();
  }
  console.log('PASS: P07 V2 кнопка «следить» — состояние, создание, отмена, отказ сервера, гость');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
