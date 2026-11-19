// R26-UI01 (приёмка 6.40.26): каталог, открытый настоящим кликом до окончания старта страницы
// (ожидание /api/me), не закрывается стартовым handleRoute(). Обычный переход, Escape и кнопка
// закрытия по-прежнему закрывают меню; в режиме закрытого сайта гость каталог не видит.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/search_first.html'),'utf8');
const BASE='http://127.0.0.1:18113';
const ME_DELAY=1500;

(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 for(const [width,height] of [[1366,768],[390,844]])for(const colorScheme of ['light','dark']){
  const mobile=width<768,label=`${width}/${colorScheme}`;
  const context=await browser.newContext({viewport:{width,height},colorScheme,hasTouch:mobile,isMobile:mobile,locale:'ru-RU'});
  const page=await context.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  let maintenance=false,meServed=0;
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.origin!==BASE)return route.abort();
   if(u.pathname==='/api/me'){
    await new Promise(r=>setTimeout(r,ME_DELAY));meServed++;
    return route.fulfill({contentType:'application/json',body:JSON.stringify({user:null,maintenance_mode:maintenance,auth:{}})});
   }
   if(u.pathname.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:'{}'});
   return route.fulfill({contentType:'text/html',body:html});
  });
  const isOpen=()=>page.locator('#catalogModal.open').count().then(n=>n>0);
  const button=mobile?'#bottomNavCatalog':'#btnCatalogHeader';
  const clickButton=()=>mobile?page.locator(button).tap():page.locator(button).click();

  // 1. Настоящий клик до ответа /api/me: меню открыто и после старта
  await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});
  await page.locator(button).waitFor({state:'visible'});
  assert.equal(meServed,0,label+': клик должен быть раньше /api/me');
  await clickButton();
  assert.ok(await isOpen(),label+': меню открылось');
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(400);
  assert.ok(meServed>=1,label+': старт дождался /api/me');
  assert.ok(await isOpen(),label+': стартовый маршрут не закрыл меню');
  assert.equal(await page.locator(button).getAttribute('aria-expanded'),mobile?null:'true',label);
  // Меню рабочее: пункты разделов на месте
  assert.ok(await page.locator('#catalogL1Column .catalog-l1-item').count()>0,label);

  // 2. Escape закрывает
  await page.keyboard.press('Escape');
  assert.equal(await isOpen(),false,label+': Escape');
  // 3. Кнопка закрытия
  await clickButton();assert.ok(await isOpen());
  await page.locator('.catalog-btn-close').click();
  assert.equal(await isOpen(),false,label+': кнопка закрытия');
  // 4. Обычный переход закрывает
  await clickButton();assert.ok(await isOpen());
  await page.evaluate(()=>navigateTo(null,'/deals'));
  assert.equal(await isOpen(),false,label+': переход закрывает меню');
  // 5. Назад в истории (popstate) тоже переход
  await clickButton();assert.ok(await isOpen());
  await page.goBack();await page.waitForTimeout(200);
  assert.equal(await isOpen(),false,label+': popstate закрывает меню');

  // 6. Закрытый сайт: ранний клик гостя не оставляет каталог поверх экрана входа
  maintenance=true;
  await page.goto(BASE+'/',{waitUntil:'domcontentloaded'});
  await page.locator(button).waitFor({state:'attached'});
  await page.evaluate(()=>openCatalogModal());
  await page.waitForLoadState('networkidle');await page.waitForTimeout(400);
  assert.equal(await isOpen(),false,label+': закрытый сайт');
  assert.ok(await page.locator('#viewClosedDev.active').count(),label+': экран закрытого сайта');

  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label);
  assert.deepEqual(errors,[],label);
  console.log(`PASS catalog early open ${label}`);
  await context.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
