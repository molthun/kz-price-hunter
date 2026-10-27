// C-E06-01 (UI): предложение из соцсети без permalink приходит с пустым url.
// Кнопка магазина для него не рисуется — вместо неё честная пометка «Нет ссылки». Офлайн.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const ROOT=path.resolve(__dirname,'..','..');
const assert=require('node:assert/strict');

const offer=(url)=>({id:'off_soc_1',price:499000,channel_name:'ShopKZ (Instagram)',seller_name:'ShopKZ',
  condition:'new',city:'Алматы',url,freshness:'fresh'});

const v2Item=(url)=>({product:{id:'p-1',title:'iPhone 15',image_url:''},
  best_offer:offer(url),offers:[offer(url)],sellers_count:1,offers_count:1,conditions_available:['new']});

const legacyData=(url)=>({v2:false,items:[],
  best_deal:{title:'iPhone 15',shop:'ShopKZ (Instagram)',price:499000,url,image_url:'',
    savings_vs_max:0,savings_pct:0},
  price_stats:{min_price:499000,max_price:499000,avg_price:499000,total_offers:1},
  store_comparison:[{shop:'ShopKZ (Instagram)',min_price:499000,diff_from_best:0,title:'iPhone 15',url}],
  all_stale:false});

(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const html=fs.readFileSync(path.join(ROOT,'web/templates/index.html'),'utf8');
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'});
  const errors=[];
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept());
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.hostname==='cdn.tailwindcss.com')return route.fulfill({contentType:'application/javascript',body:'window.tailwind=window.tailwind||{config:{}};'});
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname.startsWith('/static/'))return route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(ROOT,'web',u.pathname),'utf8')});
   if(u.pathname==='/api/me')return route.fulfill({contentType:'application/json',body:JSON.stringify({user:null,settings:{},auth:{},shops:{}})});
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18099/');
  await page.waitForFunction(()=>typeof renderBestPriceResults==='function'&&typeof storeUrl==='function');

  // storeUrl отделяет настоящую ссылку от её отсутствия
  const urls=await page.evaluate(()=>[storeUrl(''),storeUrl('   '),storeUrl(null),storeUrl(undefined),
    storeUrl('#'),storeUrl('javascript:alert(1)'),storeUrl('https://t.me/shopkz/42')]);
  assert.deepEqual(urls.slice(0,6),[null,null,null,null,null,null],'пустой и небезопасный url — не ссылка');
  assert.equal(urls[6],'https://t.me/shopkz/42','настоящий permalink сохраняется');

  // Карточка 2.0: оффер без permalink не получает кнопку «В магазин»
  const card=await page.evaluate(item=>{
    const box=document.createElement('div'); box.id='fx1'; document.body.append(box);
    renderSearchV2ProductCards([item],box);
    const links=[...box.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'));
    return {links,html:box.innerHTML};
  },v2Item(''));
  assert.equal(card.links.filter(h=>h==='#'||h==='').length,0,'мёртвых ссылок в карточке быть не должно');
  assert.ok(!/t\.me/.test(card.html),'t.me не подставляется');
  assert.match(card.html,/Нет ссылки/,'отсутствие ссылки названо прямо');

  // Тот же оффер с permalink — кнопка на месте
  const cardWithUrl=await page.evaluate(item=>{
    const box=document.createElement('div'); box.id='fx2'; document.body.append(box);
    renderSearchV2ProductCards([item],box);
    return {links:[...box.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),html:box.innerHTML};
  },v2Item('https://t.me/shopkz/42'));
  assert.ok(cardWithUrl.links.includes('https://t.me/shopkz/42'),'permalink становится кнопкой');
  assert.ok(!/Нет ссылки/.test(cardWithUrl.html),'при наличии ссылки пометки нет');

  // Hero «Купить по лучшей цене» и плитки сравнения магазинов
  const hero=await page.evaluate(data=>{
    renderBestPriceResults(data);
    const heroBox=document.getElementById('bestDealHeroContainer');
    const grid=document.getElementById('storeComparisonGrid');
    return {
      heroLinks:[...heroBox.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),
      heroHtml:heroBox.innerHTML,
      gridLinks:[...grid.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),
      gridHtml:grid.innerHTML,
    };
  },legacyData(''));
  assert.equal(hero.heroLinks.filter(h=>h==='#'||h==='').length,0,'в hero нет кнопки в никуда');
  assert.match(hero.heroHtml,/Нет ссылки/,'hero говорит об отсутствии ссылки');
  assert.equal(hero.gridLinks.filter(h=>h==='#'||h==='').length,0,'в плитке магазина нет кнопки в никуда');
  assert.match(hero.gridHtml,/Нет ссылки/,'плитка говорит об отсутствии ссылки');

  const heroWithUrl=await page.evaluate(data=>{
    renderBestPriceResults(data);
    const heroBox=document.getElementById('bestDealHeroContainer');
    const grid=document.getElementById('storeComparisonGrid');
    return {
      heroLinks:[...heroBox.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),
      gridLinks:[...grid.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')),
    };
  },legacyData('https://www.instagram.com/p/abc/'));
  assert.ok(heroWithUrl.heroLinks.includes('https://www.instagram.com/p/abc/'),'hero ведёт на permalink');
  assert.ok(heroWithUrl.gridLinks.includes('https://www.instagram.com/p/abc/'),'плитка ведёт на permalink');

  // Модальное окно: кнопка покупки скрыта, когда ссылки нет
  const modal=await page.evaluate(async()=>{
    const link=document.getElementById('modalProductBuyLink');
    const res={};
    await openProductDetailsModal({id:'p-1',title:'iPhone 15',shop:'ShopKZ (Instagram)',price:499000,url:''});
    res.hiddenWithoutUrl=link.classList.contains('hidden');
    res.hrefWithoutUrl=link.getAttribute('href');
    await openProductDetailsModal({id:'p-1',title:'iPhone 15',shop:'Kaspi',price:499000,url:'https://kaspi.kz/shop/p/x'});
    res.hiddenWithUrl=link.classList.contains('hidden');
    res.hrefWithUrl=link.getAttribute('href');
    return res;
  });
  assert.ok(modal.hiddenWithoutUrl,'без ссылки кнопка покупки скрыта');
  assert.equal(modal.hrefWithoutUrl,'#','скрытая кнопка не ведёт в магазин');
  assert.ok(!modal.hiddenWithUrl,'со ссылкой кнопка покупки видна');
  assert.equal(modal.hrefWithUrl,'https://kaspi.kz/shop/p/x');

  assert.deepEqual(errors,[],'ошибок страницы быть не должно');
  await page.close();
  console.log('OK: предложения без permalink не показывают кнопку магазина');
 } finally {
  await browser.close();
 }
})().catch(e=>{console.error(e);process.exit(1);});
