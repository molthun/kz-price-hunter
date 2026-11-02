const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18116',html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
const legacy=fs.readFileSync(path.join(__dirname,'../../web/templates/index.html'),'utf8');
(async()=>{const b=await chromium.launch({channel:'chrome'});try{
for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await b.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[];let mutations=0;
 p.on('pageerror',e=>errors.push(e.message));await p.addInitScript(()=>window.tailwind={config:{}});
 await p.route('**/*',r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();
 if(!['GET','HEAD'].includes(r.request().method()))mutations++;
 const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 if(u.pathname==='/static/js/format.js')return r.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'../../web/static/js/format.js'),'utf8')});
 if(u.pathname==='/api/admin/users')return J([]);
 if(u.pathname==='/api/admin/sellers')return J({sellers:[],total:0});
 if(u.pathname==='/api/admin/classification')return J({items:[],total:0,summary:{},categories:[],worker:{enabled:true}});
 if(u.pathname==='/api/admin/catalog-expansions')return J({items:[]});
 if(u.pathname==='/api/me')return J({user:{id:1,is_admin:true},settings:{},shops:{},auth:{},maintenance_mode:false});
 if(u.pathname==='/api/categories')return J([]);
 if(u.pathname.startsWith('/api/'))return J({});
 return r.fulfill({contentType:'text/html',body:u.pathname==='/dashboard'?legacy:html});});
 await p.goto(BASE+'/admin#classificationTool');
 await p.locator('#classificationQuery').fill('Оперативная память');
 for(const id of ['catalogExpansionTool','shopsTool','usersTool','systemTool','classificationTool']){
  await p.locator('.admin-nav a[href="#'+id+'"]').click();
  await p.waitForFunction(id=>!document.getElementById(id).hidden,id);
  assert.equal(await p.locator('.admin-panel:visible').count(),1);
  assert.equal(await p.locator('.admin-nav a[aria-current=page]').getAttribute('href'),'#'+id);
  assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 }
 assert.equal(await p.inputValue('#classificationQuery'),'Оперативная память');
 await p.locator('.admin-nav a[href="#systemTool"]').click();
 await p.goBack();await p.locator('#classificationTool').waitFor({state:'visible'});
 await p.goForward();await p.locator('#systemTool').waitFor({state:'visible'});
 const screenshotDir=process.env.ADMIN_NAV_SCREENSHOTS;
 if(screenshotDir)await p.screenshot({path:path.join(screenshotDir,`navigation-${width}-${scheme}.png`),fullPage:true});
 await p.getByRole('link',{name:/Модели и лимиты ИИ/}).click();
 await p.waitForFunction(()=>!document.getElementById('tabSettings').classList.contains('hidden'),null,{timeout:5000}).catch(async e=>{console.log(JSON.stringify({errors,state:await p.evaluate(()=>({user:typeof currentMe==='undefined'?null:currentMe.user,url:location.href}))}));throw e;});
 assert.equal(await p.locator('#adminSection').isVisible(),true);
 assert.equal(new URL(p.url()).hash,'#sec-admin-ai');
 assert.equal(mutations,0);assert.deepEqual(errors,[]);
 console.log(`PASS admin navigation ${width}/${scheme}: panels/history/preserved fields/settings deep link/no mutations`);
 await c.close();
}
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
