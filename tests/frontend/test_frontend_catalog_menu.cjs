// Меню «Все категории»: каждый пункт обеих колонок достижим прокруткой — колёсиком на компьютере и
// касанием на телефоне, в обеих темах (A03 аудита 18.11.2026: сетка обрезала список без прокрутки).
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/search_first.html'),'utf8');
const BASE='http://127.0.0.1:18112';
const SIZES=[[800,600],[1024,768],[1366,768],[390,844]];

// Пункт виден и не перекрыт: в его центре на экране находится он сам (или его потомок)
const reachable=(page,sel,i)=>page.evaluate(([sel,i])=>{
 const el=document.querySelectorAll(sel)[i];const r=el.getBoundingClientRect();
 if(r.height<=0||r.bottom>innerHeight||r.top<0)return false;
 const hit=document.elementFromPoint(r.left+Math.min(20,r.width/2),r.top+r.height/2);
 return !!hit&&(hit===el||el.contains(hit));
},[sel,i]);

async function scrollToLast(page,colSel,itemSel,touch){
 const col=page.locator(colSel);const box=await col.boundingBox();
 const count=await page.locator(itemSel).count();assert.ok(count>0,itemSel);
 for(let step=0;step<40&&!(await reachable(page,itemSel,count-1));step++){
  if(touch){
   // Настоящий жест касанием: протяжка пальцем вверх внутри колонки
   const cdp=await page.context().newCDPSession(page);
   const x=box.x+box.width/2,y0=box.y+box.height*0.8,y1=box.y+box.height*0.2;
   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y:y0}]});
   for(let k=1;k<=8;k++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y0+(y1-y0)*k/8}]});
   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
   await cdp.detach();
  }else{await page.mouse.move(box.x+box.width/2,box.y+box.height/2);await page.mouse.wheel(0,300);}
  await page.waitForTimeout(120);
 }
 assert.ok(await reachable(page,itemSel,count-1),`последний пункт ${itemSel} недостижим`);
 return count;
}

(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 for(const [width,height] of SIZES)for(const colorScheme of ['light','dark']){
  const mobile=width<=768;
  const context=await browser.newContext({viewport:{width,height},colorScheme,hasTouch:mobile,isMobile:mobile,locale:'ru-RU'});
  const page=await context.newPage();
  await page.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.origin!==BASE)return route.abort();
   if(u.pathname.startsWith('/api/'))return route.fulfill({contentType:'application/json',body:'{}'});
   return route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto(BASE+'/');
  // Старт страницы ждёт checkAuth() и затем зовёт handleRoute(), который закрывает каталог:
  // открытое раньше меню на медленной машине CI закрывалось (1366x768, первый прогон CI 19.11)
  await page.waitForLoadState('networkidle');
  await page.evaluate(()=>openCatalogModal());
  await page.locator('#catalogModal.open').waitFor();
  await page.waitForTimeout(250);
  assert.ok(await page.locator('#catalogModal.open').count(),'меню закрылось само');
  const label=`${width}x${height}/${colorScheme}`;

  // Сетка и колонки не выходят за окно: то, что видно, — то и прокручивается
  const geo=await page.evaluate(()=>{const c=document.querySelector('.catalog-modal-container').getBoundingClientRect();
   const g=document.getElementById('catalogModalGrid').getBoundingClientRect();return {c:c.bottom,g:g.bottom,h:innerHeight};});
  assert.ok(geo.c<=geo.h+1&&geo.g<=geo.c+1,`${label}: меню выходит за окно ${JSON.stringify(geo)}`);

  const roots=await scrollToLast(page,'#catalogL1Column','#catalogL1Column .catalog-l1-item',mobile);
  // Раздел с самым длинным списком подкатегорий («Электроника»)
  if(mobile){
   await page.evaluate(()=>{document.getElementById('catalogL1Column').scrollTop=0;});
   await page.locator('#catalogL1Column .catalog-l1-item').first().tap();
   await page.locator('#catalogModalGrid.mobile-subcats').waitFor();
  }else{
   // На компьютере раздел открывается наведением (mouseenter перерисовывает колонку, поэтому мышью, а не click())
   // Мышь уводится с колонки: после прокрутки колёсиком она стоит над пунктами, сброс scrollTop
   // подводит под неё другой пункт, наведение перерисовывает колонку и найденный элемент исчезает
   // (boundingBox() === null, CI тега v6.40.26)
   await page.mouse.move(1,1);
   await page.evaluate(()=>{document.getElementById('catalogL1Column').scrollTop=0;});
   let b=null;
   for(let i=0;i<20&&!b;i++){b=await page.locator('#catalogL1Column .catalog-l1-item').first().boundingBox();if(!b)await page.waitForTimeout(50);}
   assert.ok(b,`${label}: первый раздел не найден`);
   await page.mouse.move(b.x+b.width/2,b.y+b.height/2);await page.waitForTimeout(150);
  }
  const heading=await page.locator('#catalogContentColumn .catalog-content-heading').innerText();
  assert.match(heading,/Электроника/,label);
  const col=await page.evaluate(()=>{const c=document.getElementById('catalogContentColumn');return {sh:c.scrollHeight,ch:c.clientHeight};});
  const items='#catalogContentColumn a, #catalogContentColumn .catalog-subcat-item';
  const links=await scrollToLast(page,'#catalogContentColumn',items,mobile);
  // Колонка либо целиком помещается, либо прокручивается до самого низа
  await page.evaluate(()=>{const c=document.getElementById('catalogContentColumn');c.scrollTop=c.scrollHeight;});
  assert.ok(await page.evaluate(()=>{const c=document.getElementById('catalogContentColumn');return c.scrollTop+c.clientHeight>=c.scrollHeight-1;}),label);

  // Длинные названия переносятся, счётчики не сжимаются в ноль
  const clipped=await page.evaluate(()=>[...document.querySelectorAll('.catalog-subcat-count, #catalogL1Column .catalog-subcat-count')]
   .filter(e=>e.getBoundingClientRect().width<8).length);
  assert.equal(clipped,0,`${label}: счётчики сжаты`);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${label}: горизонтальная прокрутка`);
  console.log(`PASS catalog menu ${label}: ${roots} разделов, ${links} пунктов, колонка ${col.ch}/${col.sh}px`);
  await context.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
