// Offline UI contract; backend/real local-model checks are separate.
const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '../..');
const BASE = 'http://127.0.0.1:18098';
const out = process.env.GROWTH_SCREENSHOTS;
const html = fs.readFileSync(path.join(ROOT, 'web/templates/search_first.html'), 'utf8');
const admin = fs.readFileSync(path.join(ROOT, 'web/templates/admin.html'), 'utf8');
const plan = {category_name:'Шины и диски',fields:[{key:'season',label:'Сезон'}]};
(async () => {
 const browser = await chromium.launch({channel:'chrome'});
 try {
  for (const width of [390,1366]) for (const scheme of ['light','dark']) {
   const ctx = await browser.newContext({viewport:{width,height:900},colorScheme:scheme});
   const page = await ctx.newPage();const errors=[];const calls=[];
   let expansionStatus='proposed', expansionPolls=0, expansionPosts=0;
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',async route=>{
    const u=new URL(route.request().url());
    if(u.origin!==BASE)return route.abort();
    const J = data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    if(u.pathname==='/api/v2/search'){
     calls.push(u.searchParams);
     const empty=u.searchParams.get('q')==='зимняя резина';
     return J({query:u.searchParams.get('q'),items:empty?[]:[{product:{id:'p1',title:'Шина зимняя Audit'},min_price:10000,sellers_count:1,has_new:true}],
      total_products:empty?0:1,outcome:empty?'NOT_FOUND':'FOUND',category_facets:[],seller_facets:[],brand_facets:[],
      model_facets:[],facets_available:true,local_ai:{status:'ok'},
      search_help:{category_id:'tires',category_name:'Шины и диски',clarification:empty?'Укажите размер, например 205/55 R16.':'',expansion:empty?{status:'proposed'}:null},
      attribute_facets:[{key:'season',label:'Сезон',values:[{value:'Зимние',count:1},{value:'Летние',count:1}]}]});
    }
    if(u.pathname==='/api/me')return J({ok:true,authenticated:false,user:null,maintenance_mode:false,auth:{}});
    if(u.pathname==='/api/v2/categories')return J({categories:[]});
    if(u.pathname==='/api/admin/users')return J([]);
    if(u.pathname==='/api/admin/catalog-expansions'){
     if(['approved','checking'].includes(expansionStatus)){
      expansionPolls++;
      expansionStatus=expansionPolls<=2?'approved':expansionPolls===3?'checking':expansionPolls===4?'approved':'needs_source';
     }
     return J({items:[{id:'r1',query:'зимняя резина',city:'Астана',plan,status:expansionStatus,demand:1,
      scan_offset:expansionPolls>=3?30:0,scan_total:expansionPolls>=3?100:0,found:expansionPolls>=3?12:0,
      checked_at:expansionStatus==='needs_source'?'2026-11-01T16:00:00+00:00':null,
      message:expansionStatus==='needs_source'?'Подходящие предложения не подтверждены.':'Проверка запланирована.'}]});
    }
    if(u.pathname.endsWith('/review')){expansionPosts++;expansionStatus='approved';return J({ok:true});}
    if(u.pathname.startsWith('/api/'))return J({});
    return route.fulfill({contentType:'text/html',body:u.pathname==='/admin'?admin:html});
   });
   await page.goto(BASE+'/search?q=зимняя%20резина&city=Астана');
   await page.locator('#resultsEmpty').waitFor({state:'visible'});
   assert.match(await page.locator('#localSearchHelp').innerText(),/расширение ассортимента записан/);
   assert.match(await page.locator('#resultsEmpty').innerText(),/Уточнить запрос/);
   await page.locator('#resultsEmptyAction').click();
   assert.equal(await page.locator('#resultsSearchInput').evaluate(el=>el===document.activeElement),true);
   if(out){fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,`empty-${width}-${scheme}.png`),fullPage:true});}
   await page.getByRole('button',{name:'Посмотреть раздел «Шины и диски»'}).click();
   await page.locator('.product-card').waitFor();
   if(width<900)await page.evaluate(()=>openMobileFilters());
   await page.selectOption('#attribute-season','Зимние');
   await page.waitForFunction(()=>location.search.includes('attributes'));
   await page.waitForTimeout(80);
   assert.deepEqual(JSON.parse(calls.at(-1).get('attributes')), {season:'Зимние'});
   await page.reload();await page.locator('.product-card').waitFor();
   assert.equal(await page.inputValue('#attribute-season'),'Зимние');
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   if(out)await page.screenshot({path:path.join(out,`filters-${width}-${scheme}.png`),fullPage:true});
   await page.goto(BASE+'/admin#catalogExpansionTool');
   await page.getByRole('button',{name:'Подтвердить и проверить'}).waitFor();
   await page.getByRole('button',{name:'Подтвердить и проверить'}).evaluate(button=>{button.click();button.click();});
   await page.getByRole('button',{name:'В очереди…',exact:true}).waitFor();
   assert.equal(await page.getByRole('button',{name:'В очереди…',exact:true}).isDisabled(),true);
   await page.getByText('Проверено 30 из 100, подходящих: 12').waitFor({timeout:12000});
   assert.equal(await page.getByRole('button',{name:'Отклонить'}).count(),0,'no reject while the check is running');
   assert.equal(await page.getByRole('button',{name:'Проверяем…'}).count(),1);
   await page.getByText('Нужно подключить источник',{exact:true}).waitFor({timeout:12000});
   assert.equal(expansionPosts,1,'rapid repeat does not restart the task');
   assert.match(await page.locator('#expansionList').innerText(),/Последняя завершённая проверка:/);
   assert.match(await page.locator('#expansionState').innerText(),/завершена/);
   assert.match(await page.locator('#expansionList').innerText(),/Фильтры: Сезон/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   assert.deepEqual(errors,[]);
   if(out)await page.screenshot({path:path.join(out,`admin-${width}-${scheme}.png`),fullPage:true});
   console.log(`PASS local catalog growth ${width}/${scheme}`);await ctx.close();
  }
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
