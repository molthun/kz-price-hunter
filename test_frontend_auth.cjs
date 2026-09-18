// Real browser + real auth middleware on a temporary DB; no production process.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path');
const assert=require('node:assert/strict');
(async()=>{
 const child=spawn(process.env.TEST_PYTHON||path.join(__dirname,'venv/bin/python'),['-u',path.join(__dirname,'test_browser_server.py')],{cwd:__dirname,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{
   let output=''; const timer=setTimeout(()=>reject(Error('Test server startup timeout')),10000);
   child.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/BROWSER_TEST_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(match){clearTimeout(timer);resolve(match[1])}});
   child.on('exit',code=>{clearTimeout(timer);reject(Error('Test server exited '+code))});
  });
  browser=await chromium.launch({channel:'chrome',headless:true});
  const page=await browser.newPage(); const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await page.goto(base);
  await page.waitForFunction(()=>currentMe.auth.dev_login===true);
  assert.equal(await page.evaluate(()=>postAuth('/api/auth/dev-login')),true);
  await page.evaluate(()=>loadMe());
  assert.equal(await page.evaluate(()=>currentMe.user.id),-1);
  assert.equal(await page.evaluate(()=>currentMe.user.is_admin),true);
  const settings=await page.evaluate(async()=>{const r=await fetch('/api/me/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({telegram_notify_enabled:true})});return {status:r.status,data:await r.json()}});
  assert.equal(settings.status,200);assert.equal(settings.data.settings.telegram_notify_enabled,false);
  assert.equal(await page.evaluate(()=>postAuth('/api/auth/logout')),true);
  await page.evaluate(()=>loadMe());assert.equal(await page.evaluate(()=>currentMe.user),null);
  assert.equal((await page.request.get(base+'/privacy')).status(),200);
  assert.deepEqual(errors,[]);
  console.log('PASS: real browser dev login, session, settings POST Origin, logout, privacy page; isolated DB');
 }finally{
  if(browser)await browser.close();
  child.kill('SIGINT');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(()=>{child.kill('SIGKILL');resolve()},5000).unref()});
 }
})().catch(e=>{console.error(e);process.exitCode=1});
