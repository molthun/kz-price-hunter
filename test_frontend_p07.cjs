// P07 «Мои наблюдения»: раздел только для вошедших, создание, ошибка сервера, включение и удаление. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 let watches=[{id:7,kind:'search',target:'iphone 15 pro',title:'iPhone 15 Pro',condition:'target_price',
   threshold:700000,city:null,shops:[],mode:'instant',quiet_from:'23:00',quiet_to:'08:00',
   timezone:'Asia/Almaty',cooldown_hours:6,repeat:true,is_active:1,last_sent_at:null,
   description:'поисковый запрос «iPhone 15 Pro»: цена не выше 700 000 ₸'}];
 const posts=[]; let rejectNext=false;
 try{
  for(const role of ['guest','user']){
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
        limits:{max:50},kinds:['product','model','search','category'],conditions:[],labels:{}})});
    if(u.pathname==='/api/me/watches'&&method==='POST'){
      const body=JSON.parse(route.request().postData()||'{}'); posts.push(body);
      if(rejectNext)return route.fulfill({status:400,contentType:'application/json',
        body:JSON.stringify({status:'error',message:'Процент снижения: допустимо от 1 до 99'})});
      watches=watches.concat([{...body,id:8,is_active:1,quiet_from:'23:00',quiet_to:'08:00',cooldown_hours:6,
        description:'поисковый запрос «'+body.target+'»: любое снижение'}]);
      return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',watch:watches.at(-1)})});
    }
    if(u.pathname.startsWith('/api/me/watches/')){
      const id=Number(u.pathname.split('/').pop());
      if(method==='DELETE'){watches=watches.filter(w=>w.id!==id);posts.push('delete'+id);}
      else {watches=watches.map(w=>w.id===id?{...w,is_active:0}:w);posts.push('toggle'+id);}
      return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok'})});
    }
    if(u.pathname.includes('alerts'))return route.fulfill({contentType:'application/json',body:'[]'});
    return route.fulfill({contentType:'application/json',body:'{}'});
   });
   await page.goto('http://127.0.0.1:18087/');
   await page.waitForFunction(()=>typeof loadWatches==='function');
   const section=page.locator('#sec-watches');

   if(role==='guest'){
    // Гостю раздел не показывается: наблюдения принадлежат конкретному человеку
    await page.waitForFunction(()=>document.getElementById('sec-watches').classList.contains('hidden'));
    // Офлайн Tailwind не загружается, поэтому проверяем сам класс, а не вычисленную видимость
    assert.ok(await page.evaluate(()=>document.getElementById('sec-watches').classList.contains('hidden')));
    await page.close();
    continue;
   }

   await page.waitForFunction(()=>!document.getElementById('sec-watches').classList.contains('hidden'));
   await page.waitForFunction(()=>document.getElementById('watchesList').children.length>0);
   assert.match(await section.innerText(),/цена не выше 700 000 ₸/);
   assert.match(await section.innerText(),/тишина 23:00–08:00/);
   assert.match(await page.locator('#watchesCount').innerText(),/1 из 50/);

   // Поле значения показывается только для условий с числом
   await page.selectOption('#watchCondition','any_drop');
   assert.ok(await page.evaluate(()=>document.getElementById('watchThresholdWrap').classList.contains('hidden')));
   await page.selectOption('#watchCondition','target_price');
   assert.ok(await page.evaluate(()=>!document.getElementById('watchThresholdWrap').classList.contains('hidden')));

   // Пустая цель — просим уточнить, запроса не делаем
   await page.click('button:has-text("Добавить наблюдение")');
   assert.match(await page.locator('#watchesMessage').innerText(),/Укажите/);
   assert.equal(posts.length,0);

   // Ошибку сервера показываем словами
   rejectNext=true;
   await page.fill('#watchTarget','стиральная машина');
   await page.selectOption('#watchCondition','any_drop');
   await page.click('button:has-text("Добавить наблюдение")');
   await page.waitForFunction(()=>document.getElementById('watchesMessage').innerText.includes('от 1 до 99'));
   rejectNext=false;

   // Успешное создание добавляет наблюдение в список
   await page.click('button:has-text("Добавить наблюдение")');
   await page.waitForFunction(()=>document.getElementById('watchesList').children.length===2);
   assert.equal(posts.at(-1).target,'стиральная машина');
   assert.equal(posts.at(-1).repeat,true);

   // Включение/выключение и удаление
   await page.click('#watchesList button:has-text("Выключить")');
   await page.waitForFunction(()=>document.getElementById('watchesList').innerText.includes('выключено'));
   await page.click('#watchesList button:has-text("Удалить")');
   await page.waitForFunction(()=>document.getElementById('watchesList').children.length===1);

   // Текст наблюдения приходит от человека — он должен экранироваться
   watches=[{...watches[0],id:9,description:'<img src=x onerror=alert(1)>'}];
   await page.evaluate(()=>loadWatches());
   await page.waitForFunction(()=>document.getElementById('watchesList').innerText.includes('<img'));
   assert.equal(await page.evaluate(()=>document.querySelectorAll('#watchesList img').length),0);

   assert.deepEqual(errors,[],'ошибок JS быть не должно');
   await page.close();
  }
  console.log('PASS: P07 наблюдения — раздел после входа, создание, ошибка сервера, включение, удаление, экранирование');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
