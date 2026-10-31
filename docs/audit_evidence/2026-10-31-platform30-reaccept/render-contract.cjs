const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
(async()=>{
 const evidence=__dirname;
 const metadata=JSON.parse(fs.readFileSync(path.join(evidence,'snapshot.json'),'utf8'));
 const probes=JSON.parse(fs.readFileSync(path.join(evidence,'independent.json'),'utf8'));
 const html=fs.readFileSync(path.join(metadata.snapshot,'web/templates/index.html'),'utf8');
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage();
 const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{window.tailwind={config:{}}});
 await page.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(metadata.snapshot,'web',u.pathname),'utf8')});
   return route.fulfill({contentType:'application/json',body:JSON.stringify(u.pathname==='/api/me'?{user:null,settings:{},auth:{},shops:{}}:{})});
 });
 await page.goto('http://127.0.0.1:18099/');
 const rendered=await page.evaluate(item=>{
   const box=document.createElement('div');box.id='audit-render';document.body.appendChild(box);
   renderSearchV2ProductCards([item],box);
   const text=box.innerText;
   const group=[...box.querySelectorAll('details')].find(x=>x.querySelector('summary')?.textContent.includes('Продавцы и сгруппированные каналы'));
   const groupText=group?.innerText || ''; 
   return {text,group_text:groupText,group_shop_links:group?.querySelectorAll('a').length || 0,group_has_kaspi_badge:groupText.includes('Kaspi'),shop_links:[...box.querySelectorAll('a')].filter(a=>a.textContent.includes('В магазин')).length,
           channel_type_badge:text.includes('Kaspi'),payload_seller_keys:Object.keys(item.sellers[0])};
 },probes.group_with_unavailable_price);
 fs.writeFileSync(path.join(evidence,'render-contract.json'),JSON.stringify({rendered,errors},null,2));
 await browser.close();
 console.log(JSON.stringify({shop_links:rendered.shop_links,channel_type_badge:rendered.channel_type_badge,errors}));
})().catch(e=>{console.error(e);process.exit(1)});
