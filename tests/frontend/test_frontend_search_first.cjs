// Search-first: повторные поиски в одном документе, состояния ошибок, город в URL, панель фильтров,
// история и условия предложения, обе темы. Офлайн: сеть перехвачена, JSON в форме настоящих DTO.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const ROOT=path.resolve(__dirname,'..','..');
const assert=require('node:assert/strict');

const html=fs.readFileSync(path.join(ROOT,'web/templates/search_first.html'),'utf8');
const BASE='http://127.0.0.1:18098';

const offer=(over={})=>({id:'o1',product_id:'p1',seller_id:'s1',seller_name:'Магазин Один',channel_type:'website',channel_name:'Сайт',
 price:6000,old_price:null,condition:'NEW',city:'Алматы',url:'https://example.invalid/offer/1',freshness:'fresh',is_purchasable:true,
 observed_at:'2026-10-30T10:00:00+00:00',provenance:{source:'Сайт магазина'},delivery_type:'courier',pickup_available:true,
 warranty:'12 месяцев от производителя',is_unit_price:true,quantity:0.5,unit:'kg',...over});

const detail={ok:true,product:{id:'p1',title:'Товар один',category:'food'},min_price:6000,best_new_price:6000,best_used_price:7000,
 sellers:[{}],best_offer:offer(),
 offers:[offer(),offer({id:'o2',condition:'OPEN_BOX',price:7000,is_unit_price:false,quantity:null,unit:null,old_price:9000,delivery_type:null,pickup_available:false,warranty:null})],
 history:{total_series:2,total_observations:3,series:[
  {seller_name:'Магазин Один',channel_type:'website',channel_name:'Сайт',condition:'NEW',city:'Алматы',offer_id:'o1',
   min_price:6000,max_price:8000,first_price:8000,latest_price:6000,change_pct:-25,
   points:[{observed_at:'2026-10-01T10:00:00+00:00',price:8000},{observed_at:'2026-10-30T10:00:00+00:00',price:6000}]},
  {seller_name:'Магазин Один',channel_type:'kaspi',channel_name:'Kaspi',condition:'OPEN_BOX',city:'Астана',offer_id:'o2',
   min_price:7000,max_price:7000,first_price:7000,latest_price:7000,change_pct:0,
   points:[{observed_at:'2026-10-29T10:00:00+00:00',price:7000}]}]}};

const searchResponse=(q)=>({query:q,total_products:45,page:1,total_pages:3,has_prev:false,has_next:true,outcome:'FOUND',facets_available:true,
 items:[{product:{id:'p1',title:'Товар '+q},min_price:6000,sellers_count:1,has_new:true,best_offer:{}}],
 category_facets:[{id:'food',name:'Еда',count:40},{id:'home',name:'Дом',count:5}],
 seller_facets:[{id:'s1',name:'Магазин Один',slug:'one',count:45}],city_facets:[],brand_facets:[]});

async function scenario(browser,{width,height,scheme}){
 const label=`${width}px/${scheme}`;
 const context=await browser.newContext({viewport:{width,height},colorScheme:scheme,locale:'en-US'});
 const page=await context.newPage();
 const errors=[]; const apiCalls=[];
 page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&!/Failed to load resource/.test(m.text()))errors.push(m.text());});
 await page.route('**/*',async route=>{
  const u=new URL(route.request().url());
  if(u.origin!==BASE)return route.abort();
  if(u.pathname==='/api/v2/search'){
   apiCalls.push(u.searchParams);
   const q=u.searchParams.get('q')||'';
   if(q==='servererror')return route.fulfill({status:500,contentType:'application/json',body:JSON.stringify({error:'x'})});
   if(q==='zero')return route.fulfill({contentType:'application/json',body:JSON.stringify({items:[],total_products:0,outcome:'NOT_FOUND',facets_available:true,category_facets:[],seller_facets:[]})});
   if(q==='nofacets')return route.fulfill({contentType:'application/json',body:JSON.stringify({...searchResponse(q),facets_available:false,category_facets:[],seller_facets:[]})});
   return route.fulfill({contentType:'application/json',body:JSON.stringify(searchResponse(q))});
  }
  if(u.pathname.startsWith('/api/v2/products/'))return route.fulfill({contentType:'application/json',body:JSON.stringify(detail)});
  if(u.pathname.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:'{}'});
  return route.fulfill({contentType:'text/html',body:html});
 });

 const cardsVisible=async(expected)=>{
  await page.waitForFunction(t=>{const c=document.querySelector('.product-card .card-title');return c&&c.textContent===t;},expected);
  assert.equal(await page.locator('#resultsEmpty').isVisible(),false,`${label}: нет ложной ошибки`);
  assert.match(await page.locator('#resultsCountSubtitle').innerText(),/найдено 45 товаров/,`${label}: счётчик виден`);
 };
 const searchFor=async(q)=>{
  await page.fill('#resultsSearchInput',q);
  await page.press('#resultsSearchInput','Enter');
  await cardsVisible('Товар '+q);
 };

 // SF1-R01: несколько поисков подряд в одном документе
 await page.goto(BASE+'/search?q=первый&city=Астана');
 await cardsVisible('Товар первый');
 await searchFor('второй');
 await searchFor('третий');
 assert.match(await page.locator('#resultsCountText').innerText(),/«третий»/);

 // Сортировка и страница
 await page.selectOption('#sortSelect','price_asc');
 await cardsVisible('Товар третий');
 assert.match(page.url(),/sort=price_asc/);
 await page.locator('#paginationBar .page-btn',{hasText:'2'}).click();
 await page.waitForFunction(()=>location.search.includes('page=2'));
 await cardsVisible('Товар третий');

 // Фильтры: применение и сброс; на узком экране — через панель
 const isMobile=width<1024;
 if(isMobile){
  const opener=page.locator('.mobile-filter-btn');
  await opener.focus();
  await opener.click();
  const expectedWidth=width<600?width:420;
  assert.equal((await page.locator('#filtersSidebar').boundingBox()).width,expectedWidth,`${label}: ширина панели`);
  assert.equal(await page.evaluate(()=>document.activeElement.id),'closeFiltersBtn',`${label}: фокус в панели`);
  assert.equal(await page.locator('#filtersSidebar').getAttribute('role'),'dialog');
  assert.equal(await opener.getAttribute('aria-expanded'),'true');
  for(let i=0;i<40;i++){
   await page.keyboard.press('Tab');
   assert.ok(await page.evaluate(()=>document.getElementById('filtersSidebar').contains(document.activeElement)),`${label}: Tab остаётся в панели`);
  }
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#filtersSidebar').isVisible(),false,`${label}: Escape закрывает`);
  assert.ok(await opener.evaluate(el=>el===document.activeElement),`${label}: фокус вернулся к кнопке`);
  assert.equal(await page.locator('#filtersSidebar').getAttribute('role'),null);
  await opener.click();
 }
 await page.fill('#filterMinPrice','100');
 await page.locator('#filtersSidebar .btn-apply-filters').click();
 await page.waitForFunction(()=>location.search.includes('min_price=100'));
 await cardsVisible('Товар третий');
 assert.equal(apiCalls.at(-1).get('min_price'),'100');
 if(isMobile){
  await page.locator('.mobile-filter-btn').click();
  await page.locator('#filterCategoriesList input[value="Дом"]').check();
  await page.waitForFunction(()=>new URLSearchParams(location.search).get('cat')==='Дом');
  await cardsVisible('Товар третий');
  assert.ok(await page.locator('#filterCategoriesList input[value="Дом"]').isChecked(),`${label}: выбор виден после render`);
  await page.locator('#filtersSidebar .btn-reset-filters').click();
 }else{
  await page.locator('#filterCategoriesList input[value="Дом"]').check();
  await page.waitForFunction(()=>new URLSearchParams(location.search).get('cat')==='Дом');
  await cardsVisible('Товар третий');
  assert.ok(await page.locator('#filterCategoriesList input[value="Дом"]').isChecked(),`${label}: выбор виден после render`);
  await page.locator('#filterCategoriesList input[value=""]').check();
  await page.waitForFunction(()=>!location.search.includes('cat='));
  await page.locator('#filtersSidebar .btn-reset-filters').click();
 }
 await page.waitForFunction(()=>!location.search.includes('min_price'));
 await cardsVisible('Товар третий');
 assert.equal(await page.inputValue('#filterMinPrice'),'');
 assert.equal(await page.inputValue('#sortSelect'),'relevance');

 // Товар → назад: выдача снова работает
 await page.locator('.btn-view-product').first().click();
 await page.waitForFunction(()=>document.getElementById('detailProductTitle').textContent==='Товар один');
 await page.goBack();
 await cardsVisible('Товар третий');
 await searchFor('четвёртый');

 // SF1-09: ошибка не наследуется пустой выдачей и наоборот
 await page.fill('#resultsSearchInput','servererror');
 await page.press('#resultsSearchInput','Enter');
 await page.locator('#resultsEmpty').waitFor({state:'visible'});
 let empty=await page.locator('#resultsEmpty').innerText();
 assert.match(empty,/Ошибка сервиса поиска/); assert.match(empty,/500/); assert.match(empty,/Повторить/);
 await page.fill('#resultsSearchInput','zero');
 await page.press('#resultsSearchInput','Enter');
 await page.waitForFunction(()=>document.querySelector('#resultsEmpty .state-title').textContent==='Сейчас подходящих предложений нет');
 empty=await page.locator('#resultsEmpty').innerText();
 assert.doesNotMatch(empty,/Ошибка|500|Повторить/,`${label}: нет текста прошлой ошибки`);
 assert.match(empty,/Уточнить запрос/);
 assert.match(await page.locator('#resultsCountSubtitle').innerText(),/найдено 0 товаров/);
 await searchFor('пятый');

 // SF1-06: сбой фасетов не выдаётся за их отсутствие
 await page.goto(BASE+'/search?q=nofacets');
 await cardsVisible('Товар nofacets');
 assert.match(await page.locator('#filterCategoriesList').textContent(),/временно недоступен/);

 // SF1-08: URL определяет город, в том числе после прошлого выбора в том же документе
 await page.goto(BASE+'/search?q=город&city=Все');
 await cardsVisible('Товар город');
 assert.equal(await page.inputValue('#globalCitySelect'),'Все');
 assert.equal(apiCalls.at(-1).get('city'),null,`${label}: «Все города» без фильтра`);
 await page.reload();
 await cardsVisible('Товар город');
 assert.equal(await page.inputValue('#globalCitySelect'),'Все',`${label}: «Все города» переживают reload`);
 await page.locator('.btn-view-product').first().click();
 await page.waitForFunction(()=>document.getElementById('detailProductTitle').textContent==='Товар один');
 assert.match(decodeURIComponent(page.url()),/city=Все/,`${label}: «Все города» в ссылке товара`);
 await page.reload();
 await page.waitForFunction(()=>document.getElementById('detailProductTitle').textContent==='Товар один');
 assert.equal(await page.inputValue('#globalCitySelect'),'Все');
 await page.goto(BASE+'/search?q=город&city=Астана');
 await cardsVisible('Товар город');
 await page.evaluate(()=>navigateTo(null,'/search?q=город'));
 await page.waitForFunction(()=>new URL(location.href).searchParams.get('city')===null);
 await cardsVisible('Товар город');
 assert.equal(apiCalls.at(-1).get('city'),'Алматы',`${label}: без city тот же результат, что и после reload`);
 assert.equal(await page.inputValue('#globalCitySelect'),'Алматы');
 await page.selectOption('#globalCitySelect','Шымкент');
 await page.waitForFunction(()=>decodeURIComponent(location.search).includes('city=Шымкент'));
 await cardsVisible('Товар город');
 await page.goBack();
 await cardsVisible('Товар город');
 assert.equal(await page.inputValue('#globalCitySelect'),'Алматы',`${label}: назад восстанавливает город`);

 // SF1-04, SF1-10: условия и история
 await page.goto(BASE+'/product/p1?city=Алматы');
 await page.waitForFunction(()=>document.getElementById('detailProductTitle').textContent==='Товар один');
 const offersText=await page.locator('#offersTableBody').innerText();
 assert.match(offersText,/Гарантия: 12 месяцев/);
 assert.match(offersText,/за 0\.5 кг/);
 assert.match(offersText,/Вскрытая упаковка/);
 assert.doesNotMatch(offersText,/Б\/у|Уточняйте/,`${label}: вскрытая упаковка не становится б/у`);
 assert.match(offersText,/Источник: Сайт магазина/);
 assert.match(offersText,/цена свежая/);
 assert.match(offersText,/по данным источника/);
 assert.match(await page.locator('#detailBadges').innerText(),/Не новые от 7 000/);
 assert.match(await page.locator('#detailBestPrice').innerText(),/за 0\.5 кг/);
 assert.equal(await page.locator('.history-series').count(),2,`${label}: линии не объединены`);
 const hist=await page.locator('#detailHistoryContent').innerText();
 assert.match(hist,/Магазин Один · Сайт · Новый · Алматы/);
 assert.match(hist,/Магазин Один · Kaspi · Вскрытая упаковка · Астана/);
 assert.match(hist,/минимум 6 000 ₸, максимум 8 000 ₸/);
 assert.match(hist,/-25\.0%/);
 assert.match(hist,/за 0\.5 кг/);
 assert.match(await page.locator('#historyObservationsBadge').innerText(),/3 наблюдения/);

 // Цена и переход к продавцу видны без горизонтальной прокрутки
 for(const sel of ['.offer-price-cell','.btn-to-seller']){
  const box=await page.locator(sel).first().boundingBox();
  assert.ok(box.x>=0&&box.x+box.width<=width,`${label}: ${sel} в пределах экрана`);
 }
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),`${label}: нет горизонтальной прокрутки страницы`);

 // Масштабирование не запрещено, фокус с клавиатуры виден
 assert.doesNotMatch(await page.getAttribute('meta[name=viewport]','content'),/user-scalable|maximum-scale/);
 await page.goto(BASE+'/');
 await page.keyboard.press('Tab');
 const focusOutline=await page.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle);
 assert.equal(focusOutline,'solid',`${label}: видимый фокус`);

 // Тема: системная по умолчанию, явный выбор переживает reload
 assert.equal(await page.getAttribute('html','data-theme'),scheme);
 await page.evaluate(()=>toggleTheme());
 const other=scheme==='dark'?'light':'dark';
 await page.reload();
 assert.equal(await page.getAttribute('html','data-theme'),other);

 assert.deepEqual(errors,[],`${label}: нет ошибок страницы`);
 await context.close();
}

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  for(const size of [{width:1366,height:900},{width:390,height:844}]){
   for(const scheme of ['light','dark'])await scenario(browser,{...size,colorScheme:scheme,scheme});
  }
  await scenario(browser,{width:360,height:740,scheme:'light'});
  await scenario(browser,{width:430,height:932,scheme:'dark'});
  await scenario(browser,{width:768,height:1024,scheme:'light'});
  await scenario(browser,{width:900,height:1024,scheme:'dark'});
  console.log('test_frontend_search_first PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
