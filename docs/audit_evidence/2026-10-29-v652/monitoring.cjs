const {chromium}=require('playwright');
const fs=require('fs');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try {
  const page=await browser.newPage();const errors=[];let wal=300*1048576;
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>{window.tailwind={config:{}}});
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync('web/templates/monitoring.html','utf8')});
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({json:{status:wal>256*1048576?'degraded':'healthy',generated_at:new Date().toISOString(),sections:{system:{status:wal>256*1048576?'degraded':'healthy',reason:wal>256*1048576?'Журнал WAL 300 МБ больше порога 256 МБ':'Норма',version:'6.5.2',counts:{products:0,active_products:0,telemetry_events:0},telemetry:{},wal_bytes:wal,wal_warn_bytes:256*1048576,db_size_bytes:wal+1048576,recent_errors:[]}}}});
   return route.fulfill({status:503,json:{error:'unavailable in isolated UI test'}});
  });
  await page.goto('http://127.0.0.1:18888/#system');
  await page.waitForFunction(()=>document.querySelector('#view').textContent.includes('300 МБ — больше порога'));
  assert.match(await page.locator('#view').innerText(),/Из них журнал WAL/);
  wal=8*1048576;await page.locator('#btnRefresh').click();
  await page.waitForFunction(()=>document.querySelector('#view').textContent.includes('8 МБ')&&!document.querySelector('#view').textContent.includes('больше порога'));
  assert.deepEqual(errors,[]);console.log('PASS: monitoring WAL high/normal, threshold text, no page errors');
 } finally {await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
