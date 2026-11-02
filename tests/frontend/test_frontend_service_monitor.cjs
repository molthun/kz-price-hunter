const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18122',html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[],mutations=[];
 let getFail=false,explanations=0,hold=null,queueAvailable=false;
 const data=()=>({generated_at:new Date().toISOString(),window_hours:24,status:'unknown',sections:{
 shops:{available:true,status:'unknown',counts:{healthy:1,unknown:1},items:[{shop_key:'fixture',name:'<img src=x onerror=alert(1)>',status:'unknown',last_success_at:null,last_items:0,categories_problem:0,historical_categories_observed:7,failure:{error_code:'http_429',phase:'fetch',page:2,http_status:429,message:'Магазин ограничил частоту запросов'}}]},
 queue:queueAvailable?{available:true,status:'healthy',worker_running:true,counts:{pending:7,processing:1,rejected:0,dead_letter:0}}:{available:false,status:'unknown'},
 scanning:{available:true,status:'unknown',runtime:{is_running:false},last_scan:null},local:{available:true,status:'unknown',configured:true,paused:false,calls_24h:0,errors_24h:0,last_call_at:null,gpu_fresh:false},
 notifications:{available:true,status:'healthy',counts:{pending:2,sent:9}},events:{available:true,status:'unknown',events_24h:0,errors_24h:0,enabled:false,flusher_alive:true,last_event_at:null}}});
 const diagnosis={status:'ready',note:'Локальный ИИ расставил приоритеты.',actions:[{code:'unreadable',title:'Не все показатели доступны',text:'Повторите обновление.',href:'/monitoring'}]};
 p.on('pageerror',e=>errors.push(e.message));await p.route('**/*',async r=>{
  const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
  if(r.request().method()==='POST')mutations.push(u.pathname);
  if(u.pathname==='/api/admin/service-overview')return getFail?r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Снимок временно недоступен.'})}):J(data());
  if(u.pathname==='/api/admin/service-overview/explain'){explanations++;const snapshot=data();if(hold)await hold.promise;return J({snapshot,diagnosis});}
  if(u.pathname==='/api/admin/users')return J([]);if(u.pathname==='/api/admin/sellers')return J({sellers:[]});if(u.pathname.startsWith('/api/'))return J({items:[],categories:[],summary:{}});
  return r.fulfill({contentType:'text/html',body:html});
 });
 await p.goto(BASE+'/admin#monitoringTool');await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();assert.equal(explanations,1);
 assert.equal(await p.locator('.service-block').count(),6);assert.equal(await p.locator('.service-block').filter({hasText:'Сохранение товаров'}).getByText('Не удалось прочитать показатели этого блока. Остальные блоки доступны.',{exact:true}).count(),1);
 assert.equal(await p.locator('#serviceSections img').count(),0);assert.match(await p.locator('#serviceSections').textContent(),/HTTP 429/);assert.match(await p.locator('#serviceSections').textContent(),/Страница: 2/);assert.match(await p.locator('#serviceSections').textContent(),/Прежних категорий в истории: 7/);assert.match(await p.locator('#serviceSections').textContent(),/onerror/);
 await p.locator('.admin-nav a[href="#shopsTool"]').click();await p.locator('.admin-nav a[href="#monitoringTool"]').click();assert.equal(explanations,1);
 let release;hold={promise:new Promise(r=>release=r)};await p.locator('#serviceExplain').evaluate(b=>{b.click();b.click();});await p.waitForFunction(()=>document.getElementById('serviceExplain').disabled);assert.equal(explanations,2);
 queueAvailable=true;await p.locator('#serviceRefresh').click();await p.getByText('Состояние обновлено. Повторите разбор после завершения предыдущего запроса.',{exact:true}).waitFor();
 release();hold=null;await p.waitForFunction(()=>!document.getElementById('serviceExplain').disabled);assert.equal(await p.locator('#serviceActions li').count(),0);assert.match(await p.locator('#serviceSections').textContent(),/Ожидают: 7/);
 getFail=true;await p.locator('#serviceRefresh').click();await p.getByText('Снимок временно недоступен.',{exact:true}).waitFor();assert.equal(await p.locator('.service-block').count(),0);assert.equal(await p.locator('#serviceActions li').count(),0);
 getFail=false;await p.locator('#serviceRefresh').click();await p.getByText('Локальный ИИ расставил приоритеты.',{exact:true}).waitFor();
 assert.deepEqual([...new Set(mutations)],['/api/admin/service-overview/explain']);assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 if(process.env.SERVICE_MONITOR_SCREENSHOTS)await p.screenshot({path:path.join(process.env.SERVICE_MONITOR_SCREENSHOTS,`service-${width}-${scheme}.png`),fullPage:true});
 console.log(`PASS service overview ${width}/${scheme}: isolated failure/no false zeros/local diagnosis/late AI ignored/read-only/XSS safe/navigation/recovery`);await c.close();
}}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
