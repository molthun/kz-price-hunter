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
 if(u.pathname.endsWith('.svg'))return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="800" height="500" fill="#eef2f7"/><rect x="60" y="170" width="680" height="160" rx="14" fill="#17263c"/><path d="M80 330h640" stroke="#dfbd5d" stroke-width="18"/><text x="400" y="260" text-anchor="middle" font-size="32" font-family="sans-serif" fill="white">Тестовое фото ${u.pathname.includes('2')?'2':'1'}</text></svg>`});
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
 await page.getByRole('button',{name:'Увеличить фото товара',exact:true}).click();
 await page.locator('#productPhotoDialog').waitFor({state:'visible'});
 assert.equal(await page.locator('#productPhotoCounter').innerText(),'2 / 2');
 assert.equal(await page.evaluate(()=>document.body.style.overflow),'hidden');
 await page.locator('#productPhotoNext').click();assert.equal(await page.locator('#productPhotoCounter').innerText(),'1 / 2');
 await page.keyboard.press('ArrowLeft');assert.equal(await page.locator('#productPhotoCounter').innerText(),'2 / 2');
 await page.locator('#productPhotoThumbs button').first().click();
 await page.waitForFunction(()=>document.getElementById('productPhotoLarge').naturalWidth>0);
 await page.getByRole('button',{name:'Увеличить изображение',exact:true}).click();
 assert.equal(await page.evaluate(()=>productPhotoZoom),2);
 assert.equal(await page.evaluate(()=>{const s=document.getElementById('photoViewerStage');return s.scrollWidth>s.clientWidth||s.scrollHeight>s.clientHeight;}),true);
 await page.getByRole('button',{name:'Уменьшить изображение',exact:true}).click();
 await page.dispatchEvent('#photoViewerStage','pointerdown',{pointerType:'touch',isPrimary:true,pointerId:1,clientX:300,clientY:150});
 await page.dispatchEvent('#photoViewerStage','pointerup',{pointerType:'touch',isPrimary:true,pointerId:1,clientX:50,clientY:155});
 assert.equal(await page.locator('#productPhotoCounter').innerText(),'2 / 2');
 const shots=process.env.CAROUSEL_SCREENSHOTS;if(shots)await page.screenshot({path:path.join(shots,`carousel-${width}-${scheme}.png`)});
 await page.keyboard.press('Escape');await page.locator('#productPhotoDialog').waitFor({state:'hidden'});
 assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
 assert.equal(await page.evaluate(()=>document.activeElement.id),'detailPhotoOpen');
 await page.getByRole('button',{name:'Увеличить фото товара',exact:true}).click();
 await page.getByRole('button',{name:'Закрыть фотографии',exact:true}).click();
 await page.locator('#productPhotoDialog').waitFor({state:'hidden'});
 assert.match(await page.locator('#detailPhotoStatus').innerText(),/2 фото/);assert.equal(await page.locator('#detailSourceLinks a').getAttribute('target'),'_blank');
 await page.evaluate(()=>{detailSelectedOptions={color:'white'};applyDetailOptions();});
 assert.equal(await page.locator('#detailPhotoThumbs button').count(),0);
 assert.equal(await page.locator('#detailSourceLinks a').count(),0);
 assert.doesNotMatch(await page.locator('#detailAttributesList').innerText(),/DDR5/);
 await page.evaluate(()=>{detailSelectedOptions={};applyDetailOptions();});
 await page.getByText(evidence.description,{exact:true}).waitFor();
 assert.equal(await page.locator('#detailPhotoThumbs button').count(),2);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.getByRole('button',{name:'Увеличить фото товара',exact:true}).click();
 if(width===390){
  await page.setViewportSize({width:900,height:390});
  assert.equal(await page.evaluate(()=>document.getElementById('productPhotoDialog').getBoundingClientRect().height<=innerHeight),true);
  await page.setViewportSize({width,height:900});
 }
 await page.evaluate(url=>handleProductPhotoFailure(url),BASE+'/image2.svg');
 assert.equal(await page.locator('#productPhotoCounter').innerText(),'1 / 1');
 assert.equal(await page.locator('#productPhotoNext').isVisible(),false);
 await page.evaluate(url=>handleProductPhotoFailure(url),BASE+'/image1.svg');
 await page.locator('#productPhotoDialog').waitFor({state:'hidden'});
 assert.equal(await page.locator('#detailPhotoOpen').isDisabled(),true);
 assert.equal(await page.evaluate(()=>document.body.style.overflow),'');
 await page.evaluate(url=>renderSourceGallery([url]),BASE+'/image1.svg');
 await page.getByRole('button',{name:'Увеличить фото товара',exact:true}).click();
 await page.evaluate(()=>navigateTo(null,'/deals'));
 await page.locator('#productPhotoDialog').waitFor({state:'hidden'});
 await page.getByText(evidence.summary,{exact:true}).waitFor();
 assert.equal(calls,2);assert.deepEqual(errors,[]);
 console.log(`PASS product facts/description/gallery/feed ${width}/${scheme}`);await context.close();
}
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
