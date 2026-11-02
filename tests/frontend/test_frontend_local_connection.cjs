const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18119',html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[],posts=[];let revision='a'.repeat(64),model='fixture:8b',conflict=false;
 const runtime={mode:{on:true},gpu:{fresh:true,busy:false}};
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 const data=()=>({settings:{local_api_base:'http://localhost:11434/v1',local_model:model,key_configured:true},revision,runtime});
 if(u.pathname==='/api/admin/settings/local-ai'){
  if(r.request().method()==='GET')return J(data());
  const b=r.request().postDataJSON();posts.push({path:u.pathname,body:b});assert.equal(b.local_api_key,undefined);assert.equal(b.scan_interval_minutes,undefined);
  if(conflict){conflict=false;return r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Подключение изменилось. Обновите форму.'})});}
  await new Promise(resolve=>setTimeout(resolve,100));model=b.local_model;revision='b'.repeat(64);return J(data());
 }
 if(u.pathname==='/api/admin/settings/local-ai/check'){const body=r.request().postDataJSON();posts.push({path:u.pathname,body});return J({runtime,check:{model_present:true,answered:true,latency_ms:77,models:['fixture:8b','fixture:next']}});}
 if(u.pathname==='/api/admin/ai/local/status')return J(runtime);
 if(u.pathname==='/api/admin/users')return J([]);if(u.pathname==='/api/admin/sellers')return J({sellers:[]});if(u.pathname.startsWith('/api/'))return J({items:[],categories:[],summary:{}});
 return r.fulfill({contentType:'text/html',body:html});});
 await p.goto(BASE+'/admin#systemTool');await p.getByText('Настройки загружены.',{exact:true}).waitFor();
 assert.equal(await p.inputValue('#localConnectionModel'),'fixture:8b');assert.equal(await p.inputValue('#localConnectionKey'),'');
 await p.fill('#localConnectionModel','fixture:next');await p.locator('#localConnectionCheck').click();
 await p.getByText('Модель ответила за 77 мс. Изменения не сохранялись.',{exact:true}).waitFor();assert.equal(posts.length,1);
 await p.locator('.admin-nav a[href="#shopsTool"]').click();await p.locator('.admin-nav a[href="#systemTool"]').click();
 assert.equal(await p.inputValue('#localConnectionModel'),'fixture:next');
 await p.locator('#localConnectionSave').evaluate(b=>{b.click();b.click();});
 await p.getByText('Подключение сохранено. Остальные настройки не менялись.',{exact:true}).waitFor();assert.equal(posts.length,2);
 assert.equal(posts[1].body.key_action,'preserve');
 conflict=true;await p.fill('#localConnectionModel','fixture:draft');await p.locator('#localConnectionSave').click();
 await p.getByText('Подключение изменилось. Обновите форму.',{exact:true}).waitFor();assert.equal(await p.inputValue('#localConnectionModel'),'fixture:draft');
 p.once('dialog',d=>d.dismiss());await p.locator('#localConnectionReload').click();assert.equal(await p.inputValue('#localConnectionModel'),'fixture:draft');
 p.once('dialog',d=>d.accept());await p.locator('#localConnectionReload').click();await p.getByText('Настройки загружены.',{exact:true}).waitFor();
 assert.equal(await p.inputValue('#localConnectionModel'),'fixture:next');
 await p.selectOption('#localConnectionKeyAction','replace');assert.equal(await p.locator('#localConnectionKey').isVisible(),true);
 await p.selectOption('#localConnectionKeyAction','preserve');
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 if(process.env.LOCAL_SETTINGS_SCREENSHOTS)await p.screenshot({path:path.join(process.env.LOCAL_SETTINGS_SCREENSHOTS,`local-settings-${width}-${scheme}.png`),fullPage:true});
 console.log(`PASS local connection ${width}/${scheme}: scope/check/save/secret preservation/409/draft navigation/reload`);await c.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
