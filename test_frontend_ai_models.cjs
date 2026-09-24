// Выбор модели из списка провайдера и ввод цен к ней. Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 let models={status:'ok',cached:false,
  gemini:{models:[{name:'gemini-3-flash',title:'Gemini 3 Flash'},{name:'gemini-3-pro',title:'Gemini 3 Pro'}],error:null},
  openai:{models:[{name:'gpt-5',title:'gpt-5'}],error:null},
  current:{gemini:'gemini-3-flash',openai:'gpt-5'},
  prices:{'gemini-3-flash':{input:0.3,output:2.5}},
  note:'Цены провайдеры не отдают.'};
 let saved=null, refreshes=0;
 try{
  const page=await browser.newPage({viewport:{width:1366,height:1000},locale:'en-US'}); const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url()); const method=route.request().method();
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
   if(u.pathname==='/api/me')return route.fulfill({contentType:'application/json',body:JSON.stringify(
     {user:{id:1,is_admin:true,first_name:'A'},settings:{},auth:{},shops:{}})});
   if(u.pathname==='/api/admin/ai/models'){
     if(u.searchParams.get('refresh')==='1')refreshes++;
     return route.fulfill({contentType:'application/json',body:JSON.stringify(models)});
   }
   if(u.pathname==='/api/admin/config'&&method==='GET')
     return route.fulfill({contentType:'application/json',body:JSON.stringify({settings:{
       ai_provider:'auto',gemini_model_mode:'manual',gemini_model:'gemini-3-flash',
       openai_model_mode:'manual',openai_model:'gpt-5',
       ai_model_prices:{'gemini-3-flash':{input:0.3,output:2.5}},enabled_shops:{}},bot:{},ai:{}})});
   if(u.pathname==='/api/admin/config'&&method==='POST'){
     saved=JSON.parse(route.request().postData()||'{}');
     return route.fulfill({contentType:'application/json',body:JSON.stringify({status:'ok',settings:{}})});
   }
   if(u.pathname.includes('alerts'))return route.fulfill({contentType:'application/json',body:'[]'});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18096/');
  await page.waitForFunction(()=>typeof loadAiModels==='function');
  await page.evaluate(()=>loadAdminSettings());
  await page.waitForFunction(()=>document.getElementById('listGeminiModels').options.length>1);

  // Список пришёл от провайдера, а не из кода
  const options=await page.evaluate(()=>[...document.getElementById('listGeminiModels').options].map(o=>o.value));
  assert.deepEqual(options,['gemini-3-flash','gemini-3-pro']);
  assert.equal(await page.evaluate(()=>document.getElementById('listOpenAiModels').value),'gpt-5');

  // Цены показаны для выбранных моделей, значения подставлены
  const priceInputs=await page.evaluate(()=>[...document.querySelectorAll('[data-price-model]')]
    .map(el=>`${el.dataset.priceModel}:${el.dataset.priceField}=${el.value}`));
  assert.ok(priceInputs.includes('gemini-3-flash:input=0.3'),priceInputs.join(','));
  assert.ok(priceInputs.some(p=>p.startsWith('gpt-5:input=')),'для второй модели поля тоже есть');

  // Выбор другой модели добавляет для неё строку цен
  await page.selectOption('#listGeminiModels','gemini-3-pro');
  await page.waitForFunction(()=>[...document.querySelectorAll('[data-price-model]')]
    .some(el=>el.dataset.priceModel==='gemini-3-pro'));
  assert.equal(await page.evaluate(()=>document.getElementById('inputGeminiModel').value),'gemini-3-pro');

  // Введённые цены уходят на сервер вместе с настройками
  await page.fill('[data-price-model="gemini-3-pro"][data-price-field="input"]','1.25');
  await page.fill('[data-price-model="gemini-3-pro"][data-price-field="output"]','10');
  await page.evaluate(()=>saveAdminSettings());
  await page.waitForFunction(()=>true);
  assert.ok(saved,'настройки должны отправиться');
  assert.deepEqual(saved.ai_model_prices['gemini-3-pro'],{input:1.25,output:10});
  assert.equal(saved.gemini_model,'gemini-3-pro');

  // Отказ провайдера виден словами, а не пустым списком
  models={...models,gemini:{models:[],error:'Gemini ответил HTTP 403'}};
  await page.evaluate(()=>loadAiModels(true));
  await page.waitForFunction(()=>document.getElementById('aiModelsNote').textContent.includes('403'));
  assert.equal(refreshes,1);

  assert.deepEqual(errors,[],'ошибок JS быть не должно');
  console.log('PASS: выбор модели из списка провайдера, цены к ней, отказ провайдера словами');
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1);});
