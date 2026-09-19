// Offline browser regression: product modal photo after a previous broken image.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
// 1x1 PNG
const PNG=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==','base64');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
 const page=await browser.newPage(); const errors=[];
 await page.addInitScript(()=>{window.tailwind={config:{}}});
 page.on('pageerror',e=>errors.push(e.message));
 const html=fs.readFileSync(path.join(__dirname,'web/templates/index.html'),'utf8');
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.hostname==='img.test'){
   return u.pathname==='/ok.png'?route.fulfill({contentType:'image/png',body:PNG}):route.fulfill({status:404,body:''});
  }
  if(u.hostname!=='127.0.0.1')return route.abort();
  if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
  if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(__dirname,'web',u.pathname),'utf8')});
  const data=u.pathname==='/api/me'?{user:null,settings:{},auth:{},shops:{}}:u.pathname.includes('alerts')?[]:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:18084/');
 await page.waitForFunction(()=>typeof openProductDetailsModal==='function');
 const product=(id,image_url)=>({id,title:'Товар '+id,shop:'Sulpak',city:'Астана',price:100,new_price:100,old_price:200,
  description:'Описание',image_url,url:'https://www.sulpak.kz/g/x'});
 const state=async()=>page.evaluate(()=>{const img=document.getElementById('modalProductImg');
  return {display:getComputedStyle(img).display,src:img.getAttribute('src'),loaded:img.complete&&img.naturalWidth>0,referrer:img.referrerPolicy}});

 await page.evaluate(p=>openProductDetailsModal(p),product('broken','https://img.test/missing.png'));
 await page.waitForFunction(()=>{const i=document.getElementById('modalProductImg');return i.complete&&!i.src.includes('missing.png')});
 const broken=await state();
 await page.evaluate(()=>closeProductDetailsModal());

 await page.evaluate(p=>openProductDetailsModal(p),product('good','https://img.test/ok.png'));
 await page.waitForFunction(()=>{const i=document.getElementById('modalProductImg');return i.complete&&i.src.includes('ok.png')});
 const good=await state();

 await page.evaluate(p=>openProductDetailsModal(p),product('none',''));
 const empty=await state();

 // Broken photo shows the placeholder instead of disappearing
 assert.notEqual(broken.display,'none','broken image hidden');
 assert.match(broken.src,/^data:image\/svg\+xml/);
 // Next product photo is visible and loaded, not stuck hidden by the previous error
 assert.notEqual(good.display,'none','good image stuck hidden');
 assert.equal(good.loaded,true,'good image not loaded');
 assert.equal(good.referrer,'no-referrer');
 assert.match(empty.src,/^data:image\/svg\+xml/);
 assert.deepEqual(errors,[]);
 console.log('PASS: modal photo recovers after a broken image, placeholder on error');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
