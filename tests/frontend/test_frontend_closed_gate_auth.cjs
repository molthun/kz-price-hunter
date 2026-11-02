// Real server/middleware/session + browser on temporary SQLite. No external login calls.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const path=require('node:path'),assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'../..');
(async()=>{
 const child=spawn(path.join(ROOT,'venv/bin/python'),['-u','-m','tests.web.test_browser_server'],{cwd:ROOT,env:{...process.env,MAINTENANCE_DEV_MODE:'1'},stdio:['ignore','pipe','pipe']});
 let browser;
 try{
  const base=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('startup timeout')),10000);child.stdout.on('data',c=>{out+=c;const m=out.match(/BROWSER_TEST_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(m){clearTimeout(timer);resolve(m[1]);}});child.on('exit',code=>{clearTimeout(timer);reject(Error('server exit '+code));});});
  browser=await chromium.launch({channel:'chrome'});const page=await browser.newPage();
  await page.route('**/*',route=>new URL(route.request().url()).origin===base?route.continue():route.abort());
  await page.goto(base+'/search?q=ram');await page.getByRole('button',{name:'Вход разработчика',exact:true}).waitFor();
  assert.equal(await page.locator('header,nav,footer').count(),0);
  assert.equal((await page.request.get(base+'/api/v2/search')).status(),403);
  await page.getByRole('button',{name:'Вход разработчика',exact:true}).click();
  await page.locator('#closedLogin').waitFor({state:'detached'});
  const me=await (await page.request.get(base+'/api/me')).json();assert.equal(me.user.is_confirmed,true);
  assert.equal(await page.locator('header').count(),1);
  const result=await page.evaluate(async()=>{const r=await fetch('/api/auth/logout',{method:'POST'});return r.status;});assert.equal(result,200);
  await page.reload();await page.locator('#closedLogin').waitFor();
  assert.equal(await page.locator('header,nav,footer').count(),0);
  assert.equal((await page.request.get(base+'/api/v2/search')).status(),403);
  console.log('PASS real closed gate → dev session → application → logout → gate; temporary DB, no Telegram send');
 }finally{if(browser)await browser.close();child.kill('SIGINT');await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(()=>{child.kill('SIGKILL');resolve();},5000).unref();});}
})().catch(e=>{console.error(e);process.exitCode=1;});
