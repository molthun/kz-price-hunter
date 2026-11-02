// Offline UI contract: real API/auth/SQLite are tested separately.
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const BASE='http://127.0.0.1:18109';
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8');
(async()=>{
 const browser=await chromium.launch({channel:'chrome'});
 try{for(const width of [390,1366])for(const colorScheme of ['light','dark']){
  const context=await browser.newContext({viewport:{width,height:900},colorScheme});
  const page=await context.newPage(),errors=[],posts=[];page.on('pageerror',e=>errors.push(e.message));
  const title='Комплект памяти <img src=x onerror=alert(1)> DDR5';
  let cats=[{id:'electronics',name:'Электроника',parent_id:null},{id:'ram',name:'Оперативная память',parent_id:'electronics'},{id:'hdd',name:'Жесткие диски HDD',parent_id:'electronics'}];
  let item={id:'p',title,category:'SSD накопители',source_hash:'a'.repeat(64),edit_token:'b'.repeat(64),
   job:{generation:7,status:'review',message:'Нужна помощь',suggestion:{category_id:'ram',confidence:.85,reason:'Вероятно оперативная память',fields:[]}},
   tag:null,history:[],sources:[{name:'Источник',url:'javascript:alert(1)'},{name:'Магазин',url:'https://example.com/p'}]};
  let conflict=false;
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!==BASE)return route.abort();
   const J=data=>route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   if(u.pathname==='/api/admin/classification')return J({items:[{id:'p',title,category:item.category,status:item.job.status,locked:item.tag?.locked}],categories:cats,total:1,summary:{total_products:1,tagged:item.tag?1:0,review:1},worker:{enabled:true}});
   if(u.pathname==='/api/admin/classification/p'){
    if(route.request().method()==='GET')return J(item);
    const body=route.request().postDataJSON();posts.push(body);
    assert.equal(body.title,undefined,'title is never submitted');assert.equal(body.source_hash.length,64);assert.equal(body.edit_token.length,64);
    if(conflict){conflict=false;return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({error:'Карточка обновилась. Обновите данные.'})});}
    await new Promise(r=>setTimeout(r,100));
    item={...item,job:{...item.job,generation:item.job.generation+1}};
    if(body.action==='retry')item.job={...item.job,status:'queued',message:'Запланирована проверка'};
    else if(body.action==='unlock'){item.tag={...item.tag,locked:false};item.job.status='queued';}
    else{
     let id=body.category_id;
     if(body.action==='create'){id='new_category';cats.push({id,name:body.new_category.name,parent_id:body.new_category.parent_id});}
     item={...item,category:cats.find(c=>c.id===id).name,edit_token:'c'.repeat(64),tag:{category_id:id,name:cats.find(c=>c.id===id).name,source:'admin',locked:true},job:{...item.job,status:'done'},history:[{event:'manual',category_id:id,created_at:1700000000,reason:'Ручная метка'}]};
    }
    return J({ok:true,item});
   }
   if(u.pathname==='/api/admin/users')return J([]);
   if(u.pathname==='/api/admin/catalog-expansions')return J({items:[]});
   if(u.pathname.startsWith('/api/'))return J({});
   return route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto(BASE+'/admin');await page.locator('#classificationList button').click();
  await page.locator('#classificationDialog').waitFor({state:'visible'});
  assert.equal(await page.locator('#classificationTitle').innerText(),title);
  assert.equal(await page.locator('#classificationTitle img').count(),0);
  assert.equal(await page.locator('#classificationSources a').count(),1);
  await page.selectOption('#classificationCategory','ram');
  await page.locator('#classificationAssign').evaluate(button=>{button.click();button.click();});
  await page.getByText('Готово. Название товара сохранено.',{exact:true}).waitFor();
  assert.equal(posts.length,1);assert.equal(posts[0].category_id,'ram');assert.equal(item.title,title);
  assert.match(await page.locator('#classificationCurrent').innerText(),/закреплено администратором/);
  await page.locator('#classificationRetry').click();await page.getByText('Проверка ИИ поставлена в очередь.',{exact:true}).waitFor();
  item.job={...item.job,status:'review',suggestion:{category_id:'hdd',confidence:.99,reason:'Второе мнение'}};
  await page.locator('#classificationSuggestion').getByText('Второе мнение',{exact:true}).waitFor({timeout:8000});
  assert.equal(await page.inputValue('#classificationCategory'),'ram','polling preserves administrator choice');
  assert.equal(item.tag.category_id,'ram','model suggestion cannot replace manual tag');
  conflict=true;await page.locator('#classificationAssign').click();await page.getByText('Карточка обновилась. Обновите данные.',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Обновить данные',exact:true}).click();await page.waitForTimeout(60);
  await page.locator('#classificationNew summary').click();
  await page.fill('#classificationNewName','Память для оборудования');await page.selectOption('#classificationNewParent','electronics');await page.fill('#classificationNewFields','Объём, Интерфейс');
  await page.getByRole('button',{name:'Создать раздел и назначить метку',exact:true}).click();
  await page.getByText('Готово. Название товара сохранено.',{exact:true}).waitFor();
  assert.equal(posts.at(-1).action,'create');assert.deepEqual(posts.at(-1).new_category.fields,['Объём','Интерфейс']);assert.equal(item.title,title);
  assert.equal(await page.locator('#classificationTitle').innerText(),title);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.equal(await page.evaluate(()=>{const d=document.getElementById('classificationDialog');return d.scrollWidth>d.clientWidth+1;}),false);
  assert.deepEqual(errors,[]);console.log(`PASS classification tags ${width}/${colorScheme}`);await context.close();
 }}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
