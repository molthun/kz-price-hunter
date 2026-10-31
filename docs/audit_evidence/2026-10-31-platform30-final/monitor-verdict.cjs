
const {chromium}=require('playwright');const fs=require('fs');const path=require('path');
(async()=>{
const m=JSON.parse(fs.readFileSync(path.join(__dirname,'snapshot.json'),'utf8'));
const html=fs.readFileSync(path.join(m.snapshot,'web/templates/monitoring.html'),'utf8');
const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage();
await page.route('**/*',route=>{
 const u=new URL(route.request().url());
 if(u.hostname!=='127.0.0.1')return route.abort();
 if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
 return route.fulfill({contentType:'application/json',body:'{}'});
});
await page.goto('http://127.0.0.1:18098/');
const result=await page.evaluate(()=>{
 const cases={};
 if(!document.getElementById('platformV2Content')){const fixture=document.createElement('div');fixture.id='platformV2Content';document.body.appendChild(fixture);}
 for(const status of ['NOT_MEASURED','ERROR_NO_SCHEMA',null]){
   renderPlatformV2({scale_level:status?{sqlite_verdict:{status,message:'audit fixture'}}:{}});
   const box=document.getElementById('platformV2Content');
   const badge=[...box.querySelectorAll('span')].find(x=>x.textContent.includes(status||'HEALTHY_SQLITE'));
   cases[String(status)]={badge:badge?.textContent,classes:badge?.className};
 }
 return cases;
});
fs.writeFileSync(path.join(__dirname,'monitor-verdict.json'),JSON.stringify(result,null,2));
await browser.close();console.log(JSON.stringify(result));
})().catch(e=>{console.error(e);process.exit(1)});
