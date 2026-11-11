// 6.36.0: доступ по времени. Таймер в приложении (гость — со входом, вошедший — «ждём подтверждения»),
// перезагрузка по окончании времени и тексты страницы входа для гостя и для вошедшего без подтверждения.
const {chromium}=require('playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..','..');
const app=fs.readFileSync(path.join(ROOT,'web/templates/search_first.html'),'utf8');
const login=fs.readFileSync(path.join(ROOT,'web/templates/closed_login.html'),'utf8');
const B='http://127.0.0.1:18094';

async function withPage(browser,viewport,me,page404,fn){
 const context=await browser.newContext({viewport,locale:'en-US'});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const state={me,doc:page404||app,meCalls:0};
 await page.route('**/*',r=>{
  const u=new URL(r.request().url());
  if(u.origin!==B)return r.abort();
  const json=o=>r.fulfill({contentType:'application/json',body:JSON.stringify(o)});
  if(u.pathname==='/api/me'){state.meCalls++;return json(state.me);}
  if(u.pathname==='/api/v2/deals')return json({deals:[],has_more:false,total:0});
  if(u.pathname.startsWith('/api/'))return json({});
  return r.fulfill({contentType:'text/html',body:state.doc});
 });
 try{await fn(page,state);assert.deepEqual(errors,[]);}finally{await context.close();}
}

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  // Гость: таймер с кнопкой «Войти», в ленте на телефоне — сверху, не на кнопке покупки
  await withPage(browser,{width:390,height:664},{user:null,auth:{},maintenance_mode:false,
   access:{mode:'timed',level:'guest',allowed:true,remaining_seconds:420,limit_minutes:10}},null,async page=>{
   await page.goto(B+'/');
   const timer=page.locator('#accessTimer');
   await timer.waitFor();
   assert.match(await timer.innerText(),/Гостевой доступ: 7 мин/);
   assert.ok(await timer.locator('.access-timer-action').isVisible(),'гостю предложен вход');
   const box=await timer.boundingBox();
   const nav=await page.locator('.mobile-bottom-nav').boundingBox();
   assert.ok(box.y+box.height<=nav.y,'таймер над нижним меню');
   await page.goto(B+'/deals');
   await timer.waitFor();
   await page.waitForFunction(()=>getComputedStyle(document.getElementById('accessTimer')).top!=='auto');
   const top=(await timer.boundingBox()).y;
   assert.ok(top<200,`в ленте таймер сверху (${top})`);
  });
  // Вошедший без подтверждения: без кнопки входа, с пояснением
  await withPage(browser,{width:1366,height:900},{user:{id:5,first_name:'Аня'},auth:{},maintenance_mode:false,settings:{},
   access:{mode:'timed',level:'trial',allowed:true,remaining_seconds:1500,limit_minutes:30}},null,async page=>{
   await page.goto(B+'/');
   const timer=page.locator('#accessTimer');
   await timer.waitFor();
   assert.match(await timer.innerText(),/Пробный доступ: 25 мин · ждём подтверждения/);
   assert.equal(await timer.locator('.access-timer-action').isVisible(),false);
  });
  // Подтверждённый: таймера нет
  await withPage(browser,{width:1366,height:900},{user:{id:6,first_name:'Ок',is_confirmed:true},auth:{},maintenance_mode:false,settings:{},
   access:{mode:'timed',level:'approved',allowed:true,remaining_seconds:null}},null,async page=>{
   await page.goto(B+'/');
   await page.waitForFunction(()=>typeof currentUser!=='undefined'&&currentUser);
   assert.equal(await page.locator('#accessTimer').isVisible(),false);
  });
  // Время вышло во время работы — страница перезагружается (сервер отдаст страницу входа)
  await withPage(browser,{width:1366,height:900},{user:null,auth:{},maintenance_mode:false,
   access:{mode:'timed',level:'guest',allowed:true,remaining_seconds:2,limit_minutes:10}},null,async (page,state)=>{
   await page.goto(B+'/');
   await page.locator('#accessTimer').waitFor();
   state.doc=login;
   state.me={user:null,auth:{bot_username:''},maintenance_mode:false,access:{mode:'timed',level:'guest',allowed:false,remaining_seconds:0}};
   await page.waitForSelector('#closedLogin',{timeout:8000});
   await page.waitForFunction(()=>document.getElementById('title').textContent.includes('10 минут'));
   assert.match(await page.locator('#description').innerText(),/Войдите через Telegram/);
  });
  // Страница входа для вошедшего без подтверждения: «пробное время закончилось», кнопки проверки и выхода
  await withPage(browser,{width:390,height:664},{user:{id:5,first_name:'Аня'},auth:{},maintenance_mode:false,
   access:{mode:'timed',level:'trial',allowed:false,remaining_seconds:0}},login,async page=>{
   await page.goto(B+'/');
   await page.waitForFunction(()=>document.getElementById('title').textContent==='Пробное время закончилось');
   assert.match(await page.locator('#description').innerText(),/подтвердит/);
   assert.ok(await page.locator('#refresh').isVisible());
   assert.equal(await page.locator('#identity').innerText(),'Аня');
  });
  // Подтвердили — страница входа сама перезагружается в приложение
  await withPage(browser,{width:390,height:664},{user:{id:5,first_name:'Аня'},auth:{},maintenance_mode:false,
   access:{mode:'timed',level:'trial',allowed:false,remaining_seconds:0}},login,async (page,state)=>{
   await page.goto(B+'/');
   await page.waitForFunction(()=>document.getElementById('title').textContent==='Пробное время закончилось');
   state.doc=app;
   state.me={user:{id:5,first_name:'Аня',is_confirmed:true},auth:{},maintenance_mode:false,settings:{},access:{mode:'timed',level:'approved',allowed:true,remaining_seconds:null}};
   await page.locator('#refresh').click();
   await page.waitForSelector('.mobile-bottom-nav',{timeout:8000});
  });
  console.log('test_frontend_timed_access PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
