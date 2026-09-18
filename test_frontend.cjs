const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage(); const errors=[];
 await page.addInitScript(()=>{window.tailwind={config:{}}});
 page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.hostname!=='127.0.0.1') return route.abort();
  if(u.pathname==='/') return route.fulfill({contentType:'text/html',body:html});
  let data={};
  if(u.pathname==='/api/me')data={user:null,settings:{},auth:{},shops:{}};
  if(u.pathname.includes('alerts'))data=[];
  if(u.pathname==='/api/ai/consultant')data={answer:'Рекомендую Apple Watch SE.',products:[{title:'Apple Watch SE',shop:'Kaspi Магазин',price:100000,url:'https://example.invalid',city:'Астана'}],suggested_questions:[]};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:18082/');
 await page.addStyleTag({content:'.hidden { display:none !important; }'});
 await page.evaluate(()=>switchTab('consultant'));
 if(await page.locator('#tabBtnConsultant').isVisible())throw Error('guest tab visible');
 if(await page.locator('#tabConsultant').isVisible())throw Error('guest section visible');
 await page.evaluate(()=>{currentMe.user={id:123};applyRoleVisibility();switchTab('consultant')});
 if(!await page.locator('#tabBtnConsultant').isVisible())throw Error('user tab hidden');
 await page.locator('#inputConsultantMessage').fill('Какие часы купить?');
 await page.evaluate(()=>handleConsultantSubmit());
 if(!await page.locator('#consultantChatFeed').innerText().then(t=>t.includes('Apple Watch SE')))throw Error('missing product card');
 if((await page.locator('#consultantChatFeed').innerText()).includes('Ошибка связи'))throw Error('consultant render failed');
 await page.evaluate(()=>{
  document.getElementById('selectOpenAiModelMode').value='manual';syncAiModelInputs();
 });
 if(await page.locator('#inputOpenAiModel').isDisabled())throw Error('manual model input disabled');
 await page.evaluate(()=>{
  document.getElementById('selectOpenAiModelMode').value='auto';syncAiModelInputs();
 });
 if(!await page.locator('#inputOpenAiModel').isDisabled())throw Error('auto model input editable');
 await page.evaluate(()=>{currentMe.user=null;applyRoleVisibility()});
 if(await page.locator('#tabConsultant').isVisible())throw Error('logout section visible');
 if(errors.length)throw Error(errors.join('; '));
 console.log(JSON.stringify({guest_hidden:true,authenticated_card_rendered:true,logout_hidden:true,page_errors:errors}));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
