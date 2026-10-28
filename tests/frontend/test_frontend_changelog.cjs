// Окно «История версий» строится из /api/changelog: текущая версия, порядок, экранирование, ошибка.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const ROOT=path.resolve(__dirname,'..','..');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
 const html=fs.readFileSync(path.join(ROOT,'web/templates/index.html'),'utf8');
 const open=async(changelog)=>{
  const page=await browser.newPage(); const errors=[]; let calls=0;
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(ROOT,'web',u.pathname),'utf8')});
   if(u.pathname==='/api/changelog'){calls++;return changelog?route.fulfill({contentType:'application/json',body:JSON.stringify(changelog)}):route.fulfill({status:500,body:''});}
   const data=u.pathname==='/api/me'?{user:null,settings:{},auth:{},shops:{}}:u.pathname.includes('alerts')?[]:{};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://127.0.0.1:18086/');
  await page.waitForFunction(()=>typeof openChangelogModal==='function');
  await page.evaluate(()=>openChangelogModal());
  await page.waitForFunction(()=>!document.querySelector('#changelogList').textContent.includes('Загрузка'));
  return {page,errors,calls:()=>calls};
 };

 const ok=await open({current:'6.4.4',releases:[
  {version:'6.4.4',date:'2026-10-28',items:['**История версий снова живая.** Настоящие выпуски.']},
  {version:'6.4.3',date:'2026-10-28',items:['<img src=x onerror="window.pwned=1">Цены не теряются']},
 ]});
 const view=await ok.page.evaluate(()=>({
  versions:[...document.querySelectorAll('#changelogList [data-version]')].map(e=>e.dataset.version),
  first:document.querySelector('#changelogList [data-version]').textContent,
  bold:document.querySelector('#changelogList b')?.textContent,
  imgs:document.querySelectorAll('#changelogList img').length,
  pwned:window.pwned===1,
  stale:document.querySelector('#modalChangelog').textContent.includes('v2.1.0'),
 }));
 assert.deepEqual(view.versions,['6.4.4','6.4.3']);
 assert.match(view.first,/v6\.4\.4 \(Текущая версия\)/);
 assert.equal(view.bold,'История версий снова живая.');
 assert.equal(view.imgs,0,'разметка из пункта попала в DOM');
 assert.equal(view.pwned,false);
 assert.equal(view.stale,false,'в шаблоне осталась ручная история v2.1.0');
 // Повторное открытие не перезапрашивает историю
 await ok.page.evaluate(()=>{closeChangelogModal();openChangelogModal()});
 assert.equal(ok.calls(),1);
 assert.deepEqual(ok.errors,[]);
 await ok.page.close();

 const failed=await open(null);
 const text=await failed.page.textContent('#changelogList');
 assert.match(text,/Не удалось загрузить историю версий/);
 assert.deepEqual(failed.errors,[]);
 console.log('PASS: changelog modal — current version, order, escaping, load error');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
