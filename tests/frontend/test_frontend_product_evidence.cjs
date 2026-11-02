const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18115',html=fs.readFileSync(path.join(__dirname,'../../web/templates/search_first.html'),'utf8');
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
for(const width of [390,1366])for(const scheme of ['light','dark']){
 const context=await browser.newContext({viewport:{width,height:900},colorScheme:scheme}),page=await context.newPage(),errors=[];let calls=0;
 page.on('pageerror',e=>errors.push(e.message));
 const evidence={status:'ready',facts:[{name:'Тип памяти',value:'DDR5 <img src=x>'},{name:'Объём',value:'64 ГБ'}],images:[BASE+'/image1.svg',BASE+'/image2.svg',BASE+'/image1.svg'],description:'Описание из источника <img src=x onerror=alert(1)>',summary:'Тип памяти: DDR5; Объём: 64 ГБ',sources:[{url:'https://itmag.kz/p/example',name:'ITMag'}]};
 await page.route('**/*',route=>{
 const u=new URL(route.request().url());if(u.origin!==BASE)return route.abort();
 const J=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 if(u.pathname.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="blue"/></svg>'});
 if(u.pathname==='/api/me')return J({user:{id:1,is_confirmed:true},auth:{},maintenance_mode:false});
 if(u.pathname==='/api/v2/products/p/details'){calls++;assert.equal(route.request().headers()['x-kzph-request'],'1');return J(evidence);}
 if(u.pathname==='/api/v2/products/p')return J({ok:true,product:{id:'p',title:'Память Kingston',category:'Оперативная память',image_url:BASE+'/image1.svg'},offers:[],sellers:[],history:{series:[]},options:[]});
 if(u.pathname==='/api/v2/deals')return J({deals:[{id:'d',canonical_product_id:'p',title:'Память Kingston',shop:'ITMag',city:'Астана',new_price:10000,old_price:12000,discount_pct:16,url:'https://itmag.kz/p/example',image_url:BASE+'/image1.svg'}],has_more:false});
 if(u.pathname.startsWith('/api/'))return J({});return route.fulfill({contentType:'text/html',body:html});
 });
 await page.goto(BASE+'/product/p');await page.getByText(evidence.description,{exact:true}).waitFor();
 assert.equal(await page.locator('#detailDescription img').count(),0);assert.equal(await page.locator('#detailAttributesList img').count(),0);
 await page.waitForFunction(()=>document.querySelectorAll('#detailPhotoThumbs button').length===2);
 await page.getByRole('button',{name:'Фото 2',exact:true}).click();assert.equal(await page.locator('#detailMainImg').getAttribute('src'),BASE+'/image2.svg');
 assert.match(await page.locator('#detailPhotoStatus').innerText(),/2 фото/);assert.equal(await page.locator('#detailSourceLinks a').getAttribute('target'),'_blank');
 await page.evaluate(()=>{detailSelectedOptions={color:'white'};applyDetailOptions();});
 assert.equal(await page.locator('#detailPhotoThumbs button').count(),0);
 assert.equal(await page.locator('#detailSourceLinks a').count(),0);
 assert.doesNotMatch(await page.locator('#detailAttributesList').innerText(),/DDR5/);
 await page.evaluate(()=>{detailSelectedOptions={};applyDetailOptions();});
 await page.getByText(evidence.description,{exact:true}).waitFor();
 assert.equal(await page.locator('#detailPhotoThumbs button').count(),2);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.goto(BASE+'/deals');await page.getByText(evidence.summary,{exact:true}).waitFor();
 assert.equal(calls,2);assert.deepEqual(errors,[]);
 console.log(`PASS product facts/description/gallery/feed ${width}/${scheme}`);await context.close();
}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
