const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/closed_login.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 for(const width of [390,1366])for(const colorScheme of ['light','dark']){
 const context=await browser.newContext({viewport:{width,height:900},colorScheme});
 const page=await context.newPage(),errors=[],api=[];let pending=false,fail=false;
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{
 const u=new URL(route.request().url());
 if(u.origin!=='http://127.0.0.1:18110')return route.abort();
 if(u.pathname==='/api/me'){api.push(u.pathname);return fail?route.fulfill({status:500,body:'error'}):route.fulfill({contentType:'application/json',body:JSON.stringify({maintenance_mode:true,user:pending?{first_name:'Тест <img src=x>',is_confirmed:false}:null,auth:{bot_username:'fixture_bot',dev_login:false}})});}
 if(u.pathname==='/api/auth/logout'){api.push(u.pathname);pending=false;return route.fulfill({contentType:'application/json',body:'{"status":"ok"}'});}
 return route.fulfill({contentType:'text/html',body:html});
 });
 await page.goto('http://127.0.0.1:18110/search?q=private');
 await page.getByText('После входа потребуется подтверждение владельца.',{exact:true}).waitFor();
 assert.equal(await page.locator('nav,header,footer,a').count(),0);
 assert.equal(await page.locator('#widget script').getAttribute('data-telegram-login'),'fixture_bot');
 assert.equal(await page.locator('#pendingActions').isVisible(),false);
 pending=true;await page.evaluate(()=>checkAccess());await page.getByRole('heading',{name:'Ожидание подтверждения',exact:true}).waitFor();
 assert.equal(await page.locator('#identity').innerText(),'Тест <img src=x>');assert.equal(await page.locator('#identity img').count(),0);
 await page.getByRole('button',{name:'Выйти',exact:true}).click();
 await page.getByRole('heading',{name:'Вход на закрытый сайт',exact:true}).waitFor();
 fail=true;await page.evaluate(()=>checkAccess());await page.getByRole('button',{name:'Повторить',exact:true}).waitFor();
 assert.equal(await page.locator('nav,header,footer,a').count(),0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false);
 assert.deepEqual(errors,[]);assert.ok(api.every(p=>['/api/me','/api/auth/logout'].includes(p)));
 console.log(`PASS closed login ${width}/${colorScheme}`);await context.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
