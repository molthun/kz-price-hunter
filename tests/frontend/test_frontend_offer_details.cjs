const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18118',html=fs.readFileSync(path.join(__dirname,'../../web/templates/search_first.html'),'utf8');
const offer=id=>({id,product_id:'p',title:'Товар '+id,seller_id:id,seller_name:'Магазин '+id,channel_name:'Сайт '+id,city:'Астана',price:id==='a'?1000:2000,url:'https://'+id+'.example/product',image_url:BASE+'/'+id+'.svg',is_purchasable:true,condition:'NEW',freshness:'fresh'});
const generic={product:{id:'p',title:'Общая карточка',image_url:BASE+'/shared.svg'},offers:[offer('a'),offer('b')],sellers:[{},{}],min_price:1000,options:[],history:{series:[]}};
const genericSingle={product:{id:'p_single',title:'Карточка с 1 предложением',image_url:BASE+'/single.svg'},offers:[offer('single')],sellers:[{}],min_price:1500,options:[],history:{series:[]}};
const scoped=id=>({...generic,product:{...generic.product,title:'Товар '+id,image_url:BASE+'/'+id+'.svg',attributes:{},description:''},offers:[offer(id)],sellers:[{}],min_price:offer(id).price,selected_offer:offer(id),source_signature:id+'sig'});
(async()=>{const b=await chromium.launch({channel:'chrome'});try{for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await b.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 if(u.pathname.endsWith('.svg'))return r.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="640" height="400"><rect width="640" height="400" fill="#dbeafe"/><text x="30" y="180" font-size="50">Фото ${u.pathname}</text></svg>`});
 if(u.pathname==='/api/me')return J({user:{id:1,is_confirmed:true},auth:{},maintenance_mode:false});
 if(u.pathname==='/api/v2/products/p/details'){await new Promise(x=>setTimeout(x,300));return J({status:'ready',images:[BASE+'/shared.svg'],description:'OTHER SELLER SHARED DATA'});}
 if(u.pathname==='/api/v2/products/p')return J(generic);
 if(u.pathname==='/api/v2/products/p_single')return J(genericSingle);
 if(u.pathname==='/api/v2/products/p_single/details')return J({status:'ready',images:[BASE+'/single.svg'],description:'Сведения единственного продавца'});
 if(['/api/v2/offers/a','/api/v2/offers/b'].includes(u.pathname))return J(scoped(u.pathname.at(-1)));
 if(u.pathname==='/api/v2/offers/missing')return r.fulfill({status:404,contentType:'application/json',body:'{}'});
 if(u.pathname==='/api/v2/offers/a/details')return J({offer_id:'a',source_signature:'asig',status:'ready',facts:[{name:'Источник',value:'a'}],images:[BASE+'/a.svg',BASE+'/a2.svg'],description:'Описание только продавца a',sources:[{url:'https://a.example/product',name:'Магазин a'}]});
 if(u.pathname==='/api/v2/offers/b/details')return J({offer_id:'b',source_signature:'bsig',status:'unavailable',facts:[],images:[BASE+'/b.svg'],description:'',sources:[{url:'https://b.example/product',name:'Магазин b'}]});
 if(u.pathname.startsWith('/api/'))return J({});return r.fulfill({contentType:'text/html',body:html});});
 await p.goto(BASE+'/product/p');
 assert.equal(await p.locator('#offersSectionLabel').innerText(),'Предложения от продавцов');
 assert.equal(await p.locator('#offersCountBadge').innerText(),'(2 предложения)');
 assert.match(await p.locator('.btn-to-seller').first().innerText(),/Выбрать предложение/);

 // Проверка товара с 1 предложением: кнопка сразу «К продавцу», без «Выбрать предложение»
 await p.evaluate(()=>navigateTo(null,'/product/p_single'));
 await p.locator('#detailProductTitle').getByText('Карточка с 1 предложением').waitFor();
 assert.equal(await p.locator('#offersSectionLabel').innerText(),'Предложение продавца');
 assert.equal(await p.locator('#offersCountBadge').innerText(),'(1 предложение)');
 assert.equal(await p.getByRole('link',{name:'Выбрать предложение'}).count(),0);
 const singleBtn=p.locator('#offersTableBody .btn-to-seller');
 assert.match(await singleBtn.innerText(),/К продавцу/);
 assert.equal(await singleBtn.getAttribute('href'),'https://single.example/product');
 assert.equal(await singleBtn.getAttribute('target'),'_blank');

 // Возврат к p и выбор предложения
 await p.evaluate(()=>navigateTo(null,'/product/p'));
 await p.getByRole('link',{name:'Выбрать предложение',exact:true}).first().click();
 await p.getByText('Описание только продавца a',{exact:true}).waitFor();await p.waitForTimeout(400);
 assert.match(p.url(),/\/offer\/a/);assert.equal(await p.locator('#detailProductTitle').innerText(),'Товар a');
 assert.equal(await p.locator('#offersTableBody tr').count(),1);assert.equal(await p.locator('#detailPhotoThumbs button').count(),2);
 assert.equal(await p.locator('#detailDescription').innerText(),'Описание только продавца a');
 assert.equal(await p.getByRole('link',{name:'К продавцу',exact:false}).getAttribute('href'),'https://a.example/product');
 if(process.env.OFFER_SCREENSHOTS)await p.screenshot({path:path.join(process.env.OFFER_SCREENSHOTS,`offer-${width}-${scheme}.png`),fullPage:true});
 await p.getByRole('link',{name:'← Все предложения этого товара',exact:true}).click();
 await p.getByRole('link',{name:'Магазин b',exact:true}).click();await p.waitForFunction(()=>document.getElementById('detailSourceStatus').textContent.includes('пока не подтверждены'));
 assert.match(p.url(),/\/offer\/b/);assert.equal(await p.locator('#detailMainImg').getAttribute('src'),BASE+'/b.svg');
 assert.doesNotMatch(await p.locator('#detailDescription').innerText(),/продавца a|SHARED DATA/);
 assert.equal(await p.locator('#detailPhotoThumbs button').count(),1);
 await p.evaluate(()=>navigateTo(null,'/offer/missing'));await p.getByText('Предложение больше недоступно',{exact:true}).waitFor();
 assert.equal(await p.locator('#detailPhotoOpen').isDisabled(),true);assert.equal(await p.locator('#detailSourceLinks a').count(),0);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 console.log(`PASS own seller offer ${width}/${scheme}: selection/source isolation/late generic reply/failure/404`);await c.close();
}}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
