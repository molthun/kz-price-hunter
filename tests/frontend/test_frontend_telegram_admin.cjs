const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18121',html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[],posts=[];
 let hour=0,enabled=false,revision='a'.repeat(64),conflict=false,checks=0,explains=0,holdExplanation=null;
 const connection={configured:true,source:'environment',check:{state:'not_checked'},polling:{state:'conflict',fresh:true,last_success_at:null}};
 const data=()=>({settings:{daily_digest_telegram_enabled:enabled,daily_digest_hour:hour},revision,timezone:'Asia/Almaty',connection,notifications:{queue:{pending:3,sent:5},muted:false,personal_recipients:2,digest_recipients:1,last_delivery_at:null}});
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 if(u.pathname==='/api/admin/settings/telegram'){
  if(r.request().method()==='GET')return J(data());const body=r.request().postDataJSON();posts.push(body);
  assert.deepEqual(Object.keys(body).sort(),['daily_digest_hour','daily_digest_telegram_enabled','revision']);
  if(conflict){conflict=false;return r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Настройки сводки изменились. Обновите значения формы.'})});}
  await new Promise(x=>setTimeout(x,100));hour=body.daily_digest_hour;enabled=body.daily_digest_telegram_enabled;revision='b'.repeat(64);return J(data());
 }
 if(u.pathname==='/api/admin/settings/telegram/check'){
  checks++;await new Promise(x=>setTimeout(x,100));connection.check={state:'ok',username:'fixture_bot',at:'2026-11-02T12:00:00+00:00'};return J(connection);
 }
 if(u.pathname==='/api/admin/settings/telegram/explain'){
  explains++;if(holdExplanation)await holdExplanation.promise;
  return J({status:'ready',note:'Локальный ИИ расставил приоритеты.',actions:[{code:'conflict',text:'Проверьте второй процесс бота.'}]});
 }
 if(u.pathname==='/api/admin/users')return J([]);if(u.pathname==='/api/admin/sellers')return J({sellers:[]});if(u.pathname.startsWith('/api/'))return J({items:[],categories:[],summary:{}});
 return r.fulfill({contentType:'text/html',body:html});});
 await p.goto(BASE+'/admin#telegramTool');await p.getByText('Настройки Telegram загружены.',{exact:true}).waitFor();await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();
 assert.equal(await p.inputValue('#tgHour'),'0');assert.equal(explains,1);assert.equal(posts.length,0);assert.equal(checks,0);
 await p.fill('#tgHour','7');await p.check('#tgEnabled');
 await p.locator('.admin-nav a[href="#shopsTool"]').click();await p.locator('.admin-nav a[href="#telegramTool"]').click();assert.equal(await p.inputValue('#tgHour'),'7');
 await p.locator('#tgCheck').evaluate(b=>{b.click();b.click();});await p.getByText('Проверка завершена. Значения формы сохранены.',{exact:true}).waitFor();assert.equal(checks,1);assert.equal(await p.inputValue('#tgHour'),'7');
 await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();
 await p.getByRole('button',{name:'Сохранить сводку',exact:true}).evaluate(b=>{b.click();b.click();});await p.getByText('Настройки сводки сохранены. Токен и личные уведомления не менялись.',{exact:true}).waitFor();
 assert.equal(posts.length,1);assert.equal(posts[0].daily_digest_hour,7);assert.equal(posts[0].daily_digest_telegram_enabled,true);
 conflict=true;await p.fill('#tgHour','9');await p.getByRole('button',{name:'Сохранить сводку',exact:true}).click();await p.getByText('Настройки сводки изменились. Обновите значения формы.',{exact:true}).waitFor();assert.equal(await p.inputValue('#tgHour'),'9');
 await p.locator('#tgRefresh').click();await p.getByText('Состояние обновлено. Черновик формы сохранён.',{exact:true}).waitFor();assert.equal(await p.inputValue('#tgHour'),'9');
 p.once('dialog',d=>d.dismiss());await p.locator('#tgReload').click();assert.equal(await p.inputValue('#tgHour'),'9');
 p.once('dialog',d=>d.accept());await p.locator('#tgReload').click();await p.getByText('Настройки Telegram загружены.',{exact:true}).waitFor();assert.equal(await p.inputValue('#tgHour'),'7');await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();
 let release;holdExplanation={promise:new Promise(r=>release=r)};const oldCount=explains;await p.locator('#tgExplain').click();await p.waitForFunction(()=>document.getElementById('tgExplain').disabled);await p.fill('#tgHour','10');await p.getByRole('button',{name:'Сохранить сводку',exact:true}).click();await p.getByText('Настройки сводки сохранены. Токен и личные уведомления не менялись.',{exact:true}).waitFor();
 release();holdExplanation=null;await p.waitForFunction(()=>!document.getElementById('tgExplain').disabled);assert.equal(await p.locator('#tgActions li').count(),0);assert.match(await p.locator('#tgAiState').textContent(),/Настройки изменились/);assert.equal(explains,oldCount+1);
 await p.locator('#tgExplain').click();await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 if(process.env.TELEGRAM_ADMIN_SCREENSHOTS)await p.screenshot({path:path.join(process.env.TELEGRAM_ADMIN_SCREENSHOTS,`telegram-${width}-${scheme}.png`),fullPage:true});
 console.log(`PASS Telegram admin ${width}/${scheme}: no sends/auto local diagnosis/scoped save/zero hour/drafts/check/409/late AI ignored`);await c.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
