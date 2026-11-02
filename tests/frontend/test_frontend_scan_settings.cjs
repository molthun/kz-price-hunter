const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18120',html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[],posts=[];
 let minutes=187,revision='a'.repeat(64),conflict=false;
 let shops=[{id:'a',name:'Alpha',enabled:true},{id:'b',name:'Beta',enabled:false},{id:'c',name:'Gamma',enabled:true},...Array.from({length:20},(_,i)=>({id:'extra'+i,name:'Дополнительный магазин '+i,enabled:i%2===0}))];
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 const data=()=>({settings:{scan_interval_minutes:minutes,shops},revision,limits:{min_minutes:5,max_minutes:43200},schedule:{wave_mode:'rolling',total_waves:5,base_wave_interval_minutes:120,adaptive_stage:'off'},runtime:{is_running:false}});
 if(u.pathname==='/api/admin/settings/scanning'){
  if(r.request().method()==='GET')return J(data());const body=r.request().postDataJSON();posts.push(body);
  assert.deepEqual(Object.keys(body).sort(),['enabled_shops','revision','scan_interval_minutes']);
  if(conflict){conflict=false;return r.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Настройки сбора изменились. Обновите форму.'})});}
  await new Promise(x=>setTimeout(x,100));minutes=body.scan_interval_minutes;shops=shops.map(s=>({...s,enabled:body.enabled_shops[s.id]}));revision='b'.repeat(64);return J(data());
 }
 if(u.pathname==='/api/admin/users')return J([]);if(u.pathname==='/api/admin/sellers')return J({sellers:[]});if(u.pathname.startsWith('/api/'))return J({items:[],categories:[],summary:{}});
 return r.fulfill({contentType:'text/html',body:html});});
 await p.goto(BASE+'/admin#scanningTool');await p.getByText('Настройки сбора загружены.',{exact:true}).waitFor();
 assert.equal(await p.inputValue('#scanSettingsValue'),'187');assert.equal(await p.inputValue('#scanSettingsUnit'),'1');
 await p.fill('#scanSettingsSearch','Alpha');await p.getByRole('button',{name:'Выключить показанные',exact:true}).click();
 await p.fill('#scanSettingsValue','3');await p.selectOption('#scanSettingsUnit','60');
 await p.locator('.admin-nav a[href="#shopsTool"]').click();await p.locator('.admin-nav a[href="#scanningTool"]').click();
 assert.equal(await p.inputValue('#scanSettingsValue'),'3');
 await p.getByRole('button',{name:'Сохранить сбор цен',exact:true}).evaluate(b=>{b.click();b.click();});
 await p.getByText('Сбор цен сохранён. Остальные настройки не менялись.',{exact:true}).waitFor();assert.equal(posts.length,1);
 assert.equal(posts[0].scan_interval_minutes,180);assert.equal(posts[0].enabled_shops.a,false);assert.equal(posts[0].enabled_shops.b,false);assert.equal(posts[0].enabled_shops.c,true);assert.equal(Object.keys(posts[0].enabled_shops).length,23);
 conflict=true;await p.fill('#scanSettingsValue','7');await p.getByRole('button',{name:'Сохранить сбор цен',exact:true}).click();
 await p.getByText('Настройки сбора изменились. Обновите форму.',{exact:true}).waitFor();assert.equal(await p.inputValue('#scanSettingsValue'),'7');
 p.once('dialog',d=>d.dismiss());await p.locator('#scanSettingsReload').click();assert.equal(await p.inputValue('#scanSettingsValue'),'7');
 p.once('dialog',d=>d.accept());await p.locator('#scanSettingsReload').click();await p.getByText('Настройки сбора загружены.',{exact:true}).waitFor();
 assert.equal(await p.inputValue('#scanSettingsValue'),'3');await p.fill('#scanSettingsSearch','');
 assert.equal(await p.locator('#scanSettingsShops input').count(),23);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 if(process.env.SCAN_SETTINGS_SCREENSHOTS)await p.screenshot({path:path.join(process.env.SCAN_SETTINGS_SCREENSHOTS,`scanning-${width}-${scheme}.png`),fullPage:true});
 console.log(`PASS scan settings ${width}/${scheme}: exact interval/dynamic shops/filter bulk/hidden choices/preserved draft/409`);await c.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
