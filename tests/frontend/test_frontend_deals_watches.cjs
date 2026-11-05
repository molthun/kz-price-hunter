// CA-01…07: лента, избранное, наблюдения, профиль. Две части: настоящий сервер на временной БД
// (SQLite -> API -> браузер -> HTTP) и офлайн-случаи для враждебных данных, отказов и гонок.
const {chromium}=require('playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..','..');
const html=fs.readFileSync(path.join(ROOT,'web/templates/search_first.html'),'utf8');

async function startServer(){
 const child=spawn(process.env.TEST_PYTHON||path.join(ROOT,'venv/bin/python'),['-u','-m','tests.web.test_browser_server'],
  {cwd:ROOT,env:{...process.env,PYTHONDONTWRITEBYTECODE:'1',BROWSER_TEST_SEED:'deals'},stdio:['ignore','pipe','pipe']});
 const base=await new Promise((resolve,reject)=>{
  let output='';const timer=setTimeout(()=>reject(Error('Test server startup timeout')),20000);
  child.stdout.on('data',c=>{output+=c;const m=output.match(/BROWSER_TEST_URL=(http:\/\/127\.0\.0\.1:\d+)/);if(m){clearTimeout(timer);resolve(m[1])}});
  child.on('exit',code=>{clearTimeout(timer);reject(Error('Test server exited '+code))});
 });
 return {child,base};
}

async function realServer(browser){
 const {child,base}=await startServer();
 try{
  const context=await browser.newContext({viewport:{width:1366,height:900},locale:'en-US'});
  const page=await context.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>new URL(r.request().url()).origin===base?r.continue():r.abort());
  const api=(url,opts)=>page.evaluate(async([u,o])=>{const r=await fetch(u,o);return {status:r.status,data:await r.json().catch(()=>null)}},[url,opts||{}]);
  const titleIs=t=>page.waitForFunction(x=>document.getElementById('detailProductTitle').textContent===x,t);
  const toast=()=>page.locator('#toastPopup').textContent();

  // Гость: отказ виден, ничего не сохранено, форма остаётся для повтора
  await page.goto(base+'/deals?city=Алматы');
  const card=page.locator('.deal-reel-card').first();
  await card.waitFor();
  const dealTitle=(await card.locator('.deal-reel-title').innerText()).trim();
  await card.locator('[aria-label="Следить за ценой"]').click();
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>document.getElementById('watchFormError').textContent.includes('входа'));
  assert.ok(await page.locator('#watchModalBackdrop').evaluate(el=>el.classList.contains('open')),'форма не закрылась');
  assert.equal(await page.evaluate(()=>localStorage.getItem('kzph_watches_v1')),null);
  assert.doesNotMatch(await toast(),/создано/);
  await page.locator('#watchForm .btn-secondary').click();

  await page.goto(base+'/profile');
  await page.waitForFunction(()=>document.getElementById('profileWatchesList').textContent.includes('после входа'));
  assert.match(await page.locator('#profileAuthBadge').innerText(),/Гость/);

  assert.equal((await api('/api/auth/dev-login',{method:'POST'})).status,200);

  // Лента -> карточка -> избранное -> карточка на настоящих идентификаторах
  await page.goto(base+'/deals?city=Алматы');
  const targetCard=page.locator('.deal-reel-card').filter({hasText:dealTitle});
  await targetCard.waitFor();
  const deals=(await api('/api/v2/deals?city=Алматы')).data.deals;
  const deal=deals.find(d=>d.title===dealTitle);
  assert.match(deal.canonical_product_id,/^prod_[0-9a-f]{16}$/);
  assert.equal(deal.price_basis,'store_claim');
  assert.match(await targetCard.locator('.deal-provenance-text').innerText(),/по данным магазина/);
  assert.doesNotMatch(await targetCard.locator('.deal-provenance-text').innerText(),/✓|истори/i);
  await targetCard.locator('[aria-label="В избранное"]').click();
  await targetCard.locator('[aria-label="О товаре"]').click();
  await titleIs(dealTitle);
  assert.ok(page.url().includes(deal.canonical_product_id));
  await page.goto(base+'/favorites');
  await page.locator('.fav-card').first().waitFor();
  const saved=await page.evaluate(()=>JSON.parse(localStorage.getItem('kzph_favorites_v1'))[0]);
  assert.equal(saved.canonical_product_id,deal.canonical_product_id);
  assert.equal(saved.legacy_product_id,null);
  await page.locator('.fav-card [onclick*="open"]').first().click();
  await titleIs(dealTitle);

  // Карточка: настоящие цена и изображение из detail DTO
  await page.waitForFunction(()=>AppState.currentProduct);
  const detail=await page.evaluate(()=>AppState.currentProduct);
  await page.locator('#detailFavBtn').click();
  await page.locator('#detailWatchBtn').click();
  const expectedPrice=await page.evaluate(p=>formatPrice(p),detail.min_price);
  assert.match(await page.locator('#watchModalCurrentPrice').innerText(),new RegExp(expectedPrice.replace(/\s/g,'\\s')));
  assert.equal(await page.inputValue('#watchTargetPriceInput'),String(Math.round(detail.min_price*0.9)));
  const fav=await page.evaluate(id=>JSON.parse(localStorage.getItem('kzph_favorites_v1')).find(x=>x.id===id),detail.product.id);
  assert.equal(fav.new_price,detail.min_price);
  assert.equal(fav.image_url,detail.product.image_url);
  assert.ok(fav.image_url);

  // any_drop: настоящий POST, затем GET той же записи
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
  assert.match(await toast(),/создано: Алматы/);
  let list=(await api('/api/me/watches')).data.watches;
  assert.equal(list.length,1);
  assert.deepEqual([list[0].kind,list[0].target,list[0].condition,list[0].city],['product',detail.product.id,'any_drop','Алматы']);

  // target_price: неверный порог не уходит на сервер, верный сохраняется
  await page.locator('#detailWatchBtn').click();
  await page.locator('input[name="watchCondition"][value="target_price"]').check();
  await page.fill('#watchTargetPriceInput','0');
  await page.locator('#watchForm').evaluate(f=>f.noValidate=true);
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>document.getElementById('watchFormError').textContent.includes('целевую'));
  assert.equal((await api('/api/me/watches')).data.watches.length,1);
  await page.fill('#watchTargetPriceInput','5000');
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
  list=(await api('/api/me/watches')).data.watches;
  assert.equal(list.length,2);
  assert.ok(list.some(w=>w.condition==='target_price'&&w.threshold===5000));

  // Смена города на повторном создании видна пользователю и хранится сервером
  await page.selectOption('#globalCitySelect','Астана');
  await page.waitForFunction(()=>decodeURIComponent(location.search).includes('Астана'));
  await page.waitForFunction(()=>AppState.currentProduct);
  await page.locator('#detailWatchBtn').click();
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
  assert.match(await toast(),/обновлено: Астана/);
  list=(await api('/api/me/watches')).data.watches;
  assert.equal(list.length,2);
  assert.equal(list.find(w=>w.condition==='any_drop').city,'Астана');

  // Подписка, созданная мимо этой формы, видна; список переживает reload
  const other=(await api('/api/me/watches',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({kind:'search',target:'из другого интерфейса',condition:'any_drop'})})).data.watch;
  await page.goto(base+'/profile');
  await page.locator('.watch-item-card').first().waitFor();
  assert.equal(await page.locator('.watch-item-card').count(),3);
  assert.match(await page.locator('#profileWatchesList').innerText(),/из другого интерфейса/);
  assert.match(await page.locator('#profileAuthBadge').innerText(),/Вход выполнен/);
  await page.reload();
  await page.locator('.watch-item-card').first().waitFor();

  // Отмена: настоящий DELETE, подтверждается GET
  await page.locator('.watch-item-card',{hasText:'из другого интерфейса'}).locator('.watch-item-delete-btn').click();
  await page.waitForFunction(()=>document.querySelectorAll('.watch-item-card').length===2);
  list=(await api('/api/me/watches')).data.watches;
  assert.ok(!list.some(w=>w.id===other.id));

  // Отказ DELETE оставляет запись и показывает ошибку
  await page.route('**/api/me/watches/*',r=>r.request().method()==='DELETE'?r.fulfill({status:500,contentType:'application/json',body:'{}'}):r.continue());
  await page.locator('.watch-item-delete-btn').first().click();
  await page.waitForFunction(()=>document.getElementById('toastPopup').textContent.includes('продолжает работать'));
  assert.equal(await page.locator('.watch-item-card').count(),2);
  assert.equal((await api('/api/me/watches')).data.watches.length,2);
  await page.unroute('**/api/me/watches/*');

  // Выход: чужой список не показывается
  await api('/api/auth/logout',{method:'POST'});
  await page.goto(base+'/profile');
  await page.waitForFunction(()=>document.getElementById('profileWatchesList').textContent.includes('после входа'));
  assert.equal(await page.locator('.watch-item-card').count(),0);

  assert.deepEqual(errors,[]);
  await context.close();
 }finally{child.kill()}
}

const EVIL=[
 "x');window.__xss=1;//",
 'x");window.__xss=1;//',
 "&#039;);window.__xss=1;//",
 '&quot;);window.__xss=1;//',
 "x\\');window.__xss=1;//",
 "line\n');window.__xss=1;//",
 '<img src=x onerror="window.__xss=1">',
 '" onclick="window.__xss=1" x="',
];
const EVIL_URLS=[
 'https://example.invalid/" onclick="window.__xss=1',
 "https://example.invalid/' onclick='window.__xss=1",
 'https://example.invalid/?a=1&b=<img src=x onerror=window.__xss=1>',
 'javascript:window.__xss=1',
];

async function offline(browser){
 const context=await browser.newContext({viewport:{width:1366,height:900},locale:'en-US'});
 const page=await context.newPage();const errors=[];const calls=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('dialog',d=>d.dismiss());
 let watchesResp=()=>({status:200,body:{status:'ok',watches:[]}});
 let createResp=()=>({status:500,body:{}});
 let dealsResp=null;
 const evilDeals=[];
 for(const title of EVIL)for(const url of EVIL_URLS)evilDeals.push({id:title+url,product_id:title,canonical_product_id:title,title,shop:title,city:title,url,image_url:title,
  new_price:100,old_price:200,discount_pct:50,savings_kzt:100,provenance:title,competitor_shop:title,deal_type:'MARKET_ARBITRAGE',price_basis:'market'});
 await page.route('**/*',async r=>{
  const u=new URL(r.request().url());
  if(u.origin!=='http://127.0.0.1:18097')return r.abort();
  const json=(o,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(o)});
  if(u.pathname==='/api/v2/deals'){
   calls.push(Object.fromEntries(u.searchParams));
   if(dealsResp)return dealsResp(r,u,json);
   return json({deals:evilDeals.slice(0,6),has_more:false,total:6});
  }
  if(u.pathname==='/api/v2/search'){
   const t=EVIL[0];
   return json({total_products:EVIL.length,page:1,total_pages:1,outcome:'FOUND',facets_available:true,
    items:EVIL.map(t=>({product:{id:t,title:t,image_url:t},min_price:100,sellers_count:1,has_new:true,best_offer:{old_price:200,seller_name:t,url:EVIL_URLS[0]}})),
    category_facets:[{id:t,name:t,count:1}],seller_facets:[{id:t,slug:t,name:t,count:1}]});
  }
  if(u.pathname==='/api/me/watches'&&r.request().method()==='POST'){const x=createResp();return x.abort?r.abort():json(x.body,x.status);}
  if(u.pathname==='/api/me/watches'){const x=watchesResp();return json(x.body,x.status);}
  if(u.pathname.startsWith('/api/me/watches/'))return json({status:'ok'});
  if(u.pathname.startsWith('/api/'))return json({});
  return r.fulfill({contentType:'text/html',body:html});
 });
 const B='http://127.0.0.1:18097';
 const noXss=async where=>assert.notEqual(await page.evaluate(()=>window.__xss),1,where+': внедрённый код выполнился');
 await page.evaluate(()=>0);
 await page.addInitScript(()=>{window.__openCalls=[];window.open=()=>null;
  try{navigator.clipboard.writeText=async()=>{};}catch(e){}});

 // Лента: все действия на всех карточках
 dealsResp=(r,u,json)=>json({deals:evilDeals,has_more:false,total:evilDeals.length});
 await page.goto(B+'/deals');
 await page.locator('.deal-reel-card').first().waitFor();
 assert.equal(await page.locator('.deal-reel-card').count(),evilDeals.length);
 assert.equal(await page.locator('.deal-reel-card a[href^="javascript"]').count(),0);
 for(let i=0;i<evilDeals.length;i++){
  await page.evaluate(i=>{
   const c=document.querySelectorAll('.deal-reel-card')[i];
   c.querySelector('[aria-label="В избранное"]').click();
   c.querySelector('[aria-label="Поделиться"]').click();
   c.querySelector('[aria-label="Следить за ценой"]').click();
   closeWatchModal();
   const a=c.querySelector('a.btn-deal-buy');
   if(a){a.addEventListener('click',e=>e.preventDefault());a.click();}
  },i);
 }
 await noXss('лента');
 assert.equal(await page.locator('.deal-reel-card img[src="x"]').count(),0);
 const hrefs=await page.locator('a.btn-deal-buy').evaluateAll(els=>els.map(a=>new URL(a.href).protocol));
 assert.ok(hrefs.length&&hrefs.every(p=>p==='https:'));
 // Переход идёт на настоящий ID, каким бы он ни был
 await page.evaluate(()=>document.querySelector('.deal-reel-card [aria-label="О товаре"]').click());
 await page.waitForFunction(()=>location.pathname.startsWith('/product/'));
 assert.equal(decodeURIComponent(new URL(page.url()).pathname),'/product/'+EVIL[0]);
 await noXss('переход из ленты');

 // Избранное
 await page.goto(B+'/favorites');
 await page.locator('.fav-card').first().waitFor();
 await page.evaluate(()=>{document.querySelectorAll('.fav-card a').forEach(a=>{a.addEventListener('click',e=>e.preventDefault());a.click();});});
 await noXss('избранное');
 const before=await page.locator('.fav-card').count();
 assert.ok(before>0);
 await page.locator('.fav-card-remove-btn').first().click();
 await page.waitForFunction(n=>document.querySelectorAll('.fav-card').length===n,before-1);
 await noXss('удаление из избранного');

 // Старая запись избранного с legacy ID не ведёт на 404, а ищет товар по названию
 await page.evaluate(()=>localStorage.setItem('kzph_favorites_v1',JSON.stringify([
  {id:'p_1',product_id:'legacy-77',title:'Старая запись',new_price:0,url:''},
  {id:'prod_0123456789abcdef',product_id:'prod_0123456789abcdef',title:'Старая каноническая',new_price:100,url:''}])));
 await page.goto(B+'/favorites');
 await page.locator('.fav-card').first().waitFor();
 assert.match(await page.locator('.fav-card').first().innerText(),/Цена не указана/);
 await page.locator('.fav-card').nth(1).locator('[title="Старая каноническая"][onclick*="open"]').click(); // клик по названию товара
 await page.waitForFunction(()=>location.pathname==='/product/prod_0123456789abcdef');
 await page.goBack();
 await page.locator('.fav-card').first().locator('[title="Старая запись"][onclick*="open"]').click();
 await page.waitForFunction(()=>location.pathname==='/search'&&new URLSearchParams(location.search).get('q')==='Старая запись');
 assert.ok(!(await page.url()).includes('legacy-77'));

 // Поиск: карточки и фасеты
 await page.evaluate(()=>localStorage.clear());
 await page.goto(B+'/search?q=probe');
 await page.locator('.product-card').first().waitFor();
 await page.evaluate(()=>document.querySelectorAll('.btn-card-fav').forEach(b=>b.click()));
 await page.locator('#filterCategoriesList input').nth(1).evaluate(el=>el.click());
 await noXss('поиск');
 assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('kzph_favorites_v1')).length),EVIL.length,'избранное из выдачи работает');
 const fromSearch=await page.evaluate(t=>JSON.parse(localStorage.getItem('kzph_favorites_v1')).find(x=>x.id===t),EVIL[0]);
 assert.equal(fromSearch.canonical_product_id,EVIL[0]);
 assert.equal(fromSearch.new_price,100);

 // Профиль: наблюдения с враждебными полями; записи старой формы не выдаются за действующие
 watchesResp=()=>({status:200,body:{status:'ok',watches:EVIL.map((t,i)=>({id:i+1,kind:'product',target:t,title:t,condition:t,description:t,city:t,is_active:1}))}});
 await page.evaluate(()=>localStorage.setItem('kzph_watches_v1',JSON.stringify([{id:'w_1',title:'Старая'}])));
 await page.goto(B+'/profile');
 await page.locator('.watch-item-card').first().waitFor();
 await page.evaluate(()=>document.querySelectorAll('.watch-item-title').forEach(t=>t.click()));
 await noXss('профиль');
 await page.goto(B+'/profile');
 await page.locator('#legacyWatchDrafts').waitFor();
 assert.match(await page.locator('#legacyWatchDrafts').innerText(),/не были сохранены/);
 await page.locator('#legacyWatchDrafts button').click();
 await page.waitForFunction(()=>localStorage.getItem('kzph_watches_v1')===null&&document.querySelector('.watch-item-card'));

 // Сервер недоступен: список не притворяется пустым
 watchesResp=()=>({status:500,body:{}});
 await page.goto(B+'/profile');
 await page.waitForFunction(()=>document.getElementById('profileWatchesList').textContent.includes('Не удалось загрузить'));
 watchesResp=()=>({status:200,body:{status:'ok',watches:[]}});

 // Создание: 500, 400, обрыв и ответ без id не дают успеха
 dealsResp=(r,u,json)=>json({deals:[{id:'p_1',product_id:'l1',canonical_product_id:'prod_0123456789abcdef',title:'T',new_price:100,url:'https://example.invalid/'}],has_more:false,total:1});
 await page.goto(B+'/deals');
 await page.locator('.deal-reel-card').first().waitFor();
 for(const [resp,re] of [[{status:500,body:{}},/не сохранил/],[{status:400,body:{status:'error',message:'Сообщение сервера'}},/Сообщение сервера/],
                         [{abort:true},/Нет связи/],[{status:200,body:{status:'ok'}},/не сохранил/]]){
  createResp=()=>resp;
  await page.locator('[aria-label="Следить за ценой"]').click();
  await page.evaluate(()=>{document.getElementById('toastPopup').textContent='';});
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(()=>document.getElementById('watchFormError').style.display==='block');
  assert.match(await page.locator('#watchFormError').innerText(),re);
  assert.equal(await page.locator('#toastPopup').textContent(),'');
  assert.equal(await page.locator('#watchSubmitBtn').isEnabled(),true);
  await page.locator('#watchForm .btn-secondary').click();
 }
 assert.equal(await page.evaluate(()=>localStorage.getItem('kzph_watches_v1')),null);

 // Цена неизвестна: остаётся неизвестной и в избранном, и в наблюдении
 const unknown=await page.evaluate(()=>{
  AppState.currentProduct={product:{id:'prod_0123456789abcdef',title:'Без цены'},min_price:null,best_offer:null,offers:[]};
  localStorage.clear();
  toggleDetailFavorite();openWatchModalFromDetail();
  return {fav:JSON.parse(localStorage.getItem('kzph_favorites_v1'))[0].new_price,shown:document.getElementById('watchModalCurrentPrice').textContent,target:document.getElementById('watchTargetPriceInput').value};
 });
 assert.deepEqual(unknown,{fav:null,shown:'не указана',target:''});
 const unit=await page.evaluate(()=>{
  AppState.currentProduct={product:{id:'prod_1123456789abcdef',title:'Мясо',image_url:'https://example.invalid/i.png'},min_price:6000,
   best_offer:{is_unit_price:true,quantity:0.5,unit:'kg'},offers:[]};
  localStorage.clear();
  toggleDetailFavorite();openWatchModalFromDetail();
  closeWatchModal();
  return {fav:JSON.parse(localStorage.getItem('kzph_favorites_v1'))[0],shown:document.getElementById('watchModalCurrentPrice').textContent};
 });
 assert.equal(unit.fav.new_price,6000);
 assert.equal(unit.fav.price_basis,'за 0.5 кг');
 assert.equal(unit.fav.image_url,'https://example.invalid/i.png');
 assert.match(unit.shown,/за 0\.5 кг/);

 // Гонка фильтров: выбор во время загрузки выполняется, старый ответ не применяется
 const pending=[];
 const dealOf=(id,type)=>({id,product_id:id,title:id,new_price:100,deal_type:type,url:''});
 dealsResp=async(r,u,json)=>{pending.push({type:u.searchParams.get('type')||'all',city:u.searchParams.get('city'),offset:u.searchParams.get('offset'),json});};
 await page.goto(B+'/deals?city=Алматы');
 await page.waitForFunction(()=>true);
 while(pending.length<1)await page.waitForTimeout(10);
 await page.locator('.deal-type-pill[data-type="arbitrage"]').click();
 while(pending.length<2)await page.waitForTimeout(10);
 assert.equal(pending[1].type,'arbitrage','новый запрос выполнен');
 await pending[1].json({deals:[dealOf('new-arb','MARKET_ARBITRAGE')],has_more:true,total:5}).catch(()=>{});
 await page.locator('.deal-reel-card[data-deal-id="new-arb"]').waitFor();
 await pending[0].json({deals:[dealOf('old-all','STORE_DISCOUNT')],has_more:false,total:1}).catch(()=>{});
 await page.waitForTimeout(100);
 assert.equal(await page.locator('[data-deal-id="old-all"]').count(),0,'старый ответ не применён');

 // Подгрузка страницы во время смены фильтра не смешивает выборки и offset
 await page.evaluate(()=>{loadDealsFeed(false);});
 while(pending.length<3)await page.waitForTimeout(10);
 assert.equal(pending[2].offset,'1');
 await page.evaluate(()=>{onDealsCategoryChanged('Смартфоны');});
 while(pending.length<4)await page.waitForTimeout(10);
 assert.equal(pending[3].offset,'0');
 await pending[2].json({deals:[dealOf('old-page','MARKET_ARBITRAGE')],has_more:false,total:2}).catch(()=>{});
 await pending[3].json({deals:[dealOf('cat-1','MARKET_ARBITRAGE')],has_more:false,total:1}).catch(()=>{});
 await page.locator('[data-deal-id="cat-1"]').waitFor();
 assert.equal(await page.locator('.deal-reel-card').count(),1);
 assert.equal(await page.evaluate(()=>DealsFeedState.offset),1);
 assert.equal(await page.evaluate(()=>DealsFeedState.loading),false);

 // Смена города в ленте — новый запрос этого города
 await page.selectOption('#globalCitySelect','Астана');
 while(pending.length<5)await page.waitForTimeout(10);
 assert.equal(pending[4].city,'Астана');
 await pending[4].json({deals:[],has_more:false,total:0}).catch(()=>{});
 await noXss('конец');
 assert.deepEqual(errors,[]);
 await context.close();
}

// CA-R02: кнопки формы достижимы настоящей прокруткой на любом экране, с ошибками и длинным названием
async function modalReach(browser,width,height,colorScheme){
 const label=`${width}x${height}/${colorScheme}`;
 const context=await browser.newContext({viewport:{width,height},colorScheme,locale:'en-US'});
 const page=await context.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let createResp={status:401,body:{status:'error'}};
 await page.route('**/*',r=>{
  const u=new URL(r.request().url());
  if(u.origin!=='http://127.0.0.1:18096')return r.abort();
  const json=(o,status=200)=>r.fulfill({status,contentType:'application/json',body:JSON.stringify(o)});
  if(u.pathname==='/api/v2/deals')return json({deals:[{id:'p_1',product_id:'l1',canonical_product_id:'prod_0123456789abcdef',
   title:'Очень длинное название товара '.repeat(8),new_price:100000,old_price:200000,discount_pct:50,url:'https://example.invalid/'}],has_more:false,total:1});
  if(u.pathname==='/api/me/watches'&&r.request().method()==='POST')return json(createResp.body,createResp.status);
  if(u.pathname.startsWith('/api/'))return json({});
  return r.fulfill({contentType:'text/html',body:html});
 });
 await page.goto('http://127.0.0.1:18096/deals');
 await page.locator('.deal-reel-card').first().waitFor();
 const open=async()=>{
  await page.evaluate(()=>document.querySelector('[aria-label="Следить за ценой"]').click());
  await page.locator('input[name="watchCondition"][value="target_price"]').evaluate(el=>el.click());
 };
 // Прокрутка колесом над формой, а не программная; кнопка должна стать верхним элементом в своей точке
 const reachByWheel=async selector=>{
  const box=await page.locator('#watchModalBody').boundingBox();
  await page.mouse.move(box.x+box.width/2,Math.max(box.y,0)+Math.min(box.height,height-box.y)/2);
  await page.mouse.wheel(0,5000);
  await page.waitForFunction(()=>{const b=document.getElementById('watchModalBody');return b.scrollTop+b.clientHeight>=b.scrollHeight-1;});
  return page.locator(selector).evaluate(el=>{
   const r=el.getBoundingClientRect();
   const top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
   return r.top>=0&&r.bottom<=innerHeight&&(top===el||el.contains(top));
  });
 };
 const closeVisible=()=>page.locator('#watchModalBackdrop .modal-close-btn').evaluate(el=>{
  const r=el.getBoundingClientRect();
  const top=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);
  return r.top>=0&&r.bottom<=innerHeight&&(top===el||el.contains(top));
 });

 await open();
 assert.ok(await reachByWheel('#watchSubmitBtn'),`${label}: отправка достижима до ошибки`);
 assert.ok(await closeVisible(),`${label}: закрытие видно`);
 for(const [resp,re] of [[{status:401,body:{status:'error'}},/входа/],[{status:400,body:{status:'error',message:'Ошибка сервера '.repeat(12)}},/Ошибка сервера/],[{status:500,body:{}},/не сохранил/]]){
  createResp=resp;
  await page.locator('#watchSubmitBtn').click();
  await page.waitForFunction(r=>new RegExp(r).test(document.getElementById('watchFormError').textContent),re.source);
  const seen=await page.locator('#watchFormError').evaluate(el=>{const r=el.getBoundingClientRect();const b=document.getElementById('watchModalBody').getBoundingClientRect();return r.top>=b.top-1&&r.top<b.bottom;});
  assert.ok(seen,`${label}: текст ошибки на виду`);
  assert.ok(await closeVisible(),`${label}: закрытие видно при ошибке`);
  assert.ok(await reachByWheel('#watchSubmitBtn'),`${label}: повтор достижим`);
  assert.ok(await page.locator('#watchForm .btn-secondary').evaluate(el=>{const r=el.getBoundingClientRect();return r.top>=0&&r.bottom<=innerHeight;}),`${label}: отмена достижима`);
 }
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${label}: нет горизонтального переполнения`);
 // Повтор проходит и закрывает окно; отмена и крестик работают настоящим нажатием
 createResp={status:200,body:{status:'ok',watch:{id:5,city:'Алматы'}}};
 await page.locator('#watchSubmitBtn').click();
 await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
 await open();
 assert.ok(await reachByWheel('#watchForm .btn-secondary'),`${label}: отмена достижима прокруткой`);
 await page.locator('#watchForm .btn-secondary').click();
 await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
 await open();
 await page.locator('#watchModalBackdrop .modal-close-btn').click();
 await page.waitForFunction(()=>!document.getElementById('watchModalBackdrop').classList.contains('open'));
 assert.deepEqual(errors,[],label);
 await context.close();
}

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  await offline(browser);
  await realServer(browser);
  for(const [w,h] of [[844,390],[640,320],[360,640],[390,844],[768,1024],[1366,900]]){
   for(const scheme of ['light','dark'])await modalReach(browser,w,h,scheme);
  }
  console.log('test_frontend_deals_watches PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
