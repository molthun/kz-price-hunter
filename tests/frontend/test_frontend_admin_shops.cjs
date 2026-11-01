// Редактор магазинов на /admin: список, добавление с несколькими маркетплейсами, правка, ошибка сервера.
// Офлайн: API подменён, проверяется только страница.
const {chromium}=require('playwright');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..','..');
const html=fs.readFileSync(path.join(ROOT,'web/templates/admin.html'),'utf8');
const BASE='http://127.0.0.1:18099';

(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  for(const viewport of [{width:1280,height:800},{width:360,height:640}]){
    const page=await (await browser.newContext({viewport,locale:'ru-RU'})).newPage();
    const errors=[];
    page.on('pageerror',e=>errors.push(String(e)));
    let saved=null;
    const shop={id:'s1',slug:'s1',name:'Белый <b>Ветер</b>',domain:'shop.kz',city:'Алматы',is_active:true,channels_count:2,offers_count:5};
    const full={...shop,bin:'',phone:'',website_url:'https://shop.kz',whatsapp:'',instagram:'',telegram:'',legal_name:'',contact_person:'',comment:'',
      marketplace_links:['https://kaspi.kz/shop/a','https://flip.kz/shop/b'],collection_channels:['Сайт Белый Ветер']};
    await page.route('**/*',async route=>{
      const req=route.request(), url=new URL(req.url());
      const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
      if(url.pathname==='/admin')return route.fulfill({contentType:'text/html',body:html});
      if(url.pathname==='/api/admin/sellers/s1'&&req.method()==='GET')return json({ok:true,seller:full});
      if(url.pathname.startsWith('/api/admin/sellers')&&req.method()!=='GET'){
        saved={method:req.method(),path:url.pathname,body:req.postDataJSON()};
        if(saved.body.bin==='12')return json({ok:false,errors:['БИН/ИИН должен содержать ровно 12 цифр.']},400);
        return json({ok:true,seller:full});
      }
      if(url.pathname==='/api/admin/sellers')return json({ok:true,total:1,sellers:[shop]});
      if(url.pathname==='/api/admin/catalog-expansions')return json({items:[]});
      return json({ok:true,users:[],items:[]});
    });
    await page.goto(BASE+'/admin');
    await page.waitForSelector('#shopsList .user');
    assert.equal(await page.locator('#shopsList .user-name').textContent(),'Белый <b>Ветер</b>'); // текст, не разметка

    // Новый магазин с тремя ссылками
    await page.getByRole('button',{name:'Добавить магазин'}).click();
    await page.fill('[name=name]','Новый');
    for(const link of ['https://kaspi.kz/shop/x','https://halykmarket.kz/merchant/y','https://ozon.kz/seller/z']){
      await page.getByRole('button',{name:'Добавить ссылку'}).click();
      await page.locator('input[name=marketplace_link]').last().fill(link);
    }
    await page.getByRole('button',{name:'Добавить ссылку'}).click(); // пустая строка не уходит
    await page.getByRole('button',{name:'Сохранить'}).click();
    await page.waitForFunction(()=>!document.getElementById('shopDialog').open);
    assert.equal(saved.method,'POST');
    assert.equal(saved.body.marketplace_links.length,3);
    assert.equal(saved.body.is_active,true);

    // Правка: форма заполнена, ссылку можно убрать, ошибка остаётся в окне
    await page.getByRole('button',{name:'Изменить'}).click();
    await page.waitForFunction(()=>document.getElementById('shopDialog').open);
    assert.equal(await page.locator('input[name=marketplace_link]').count(),2);
    assert.equal(await page.inputValue('[name=website_url]'),'https://shop.kz');
    assert.match(await page.textContent('#shopCollection'),/Сайт Белый Ветер/);
    await page.getByRole('button',{name:'Убрать'}).first().click();
    await page.fill('[name=bin]','12');
    await page.getByRole('button',{name:'Сохранить'}).click();
    await page.waitForFunction(()=>document.getElementById('shopFormError').textContent.includes('12 цифр'));
    assert.ok(await page.evaluate(()=>document.getElementById('shopDialog').open));
    await page.fill('[name=bin]','');
    await page.uncheck('[name=is_active]');
    await page.getByRole('button',{name:'Сохранить'}).click();
    await page.waitForFunction(()=>!document.getElementById('shopDialog').open);
    assert.equal(saved.method,'PUT');
    assert.equal(saved.path,'/api/admin/sellers/s1');
    assert.deepEqual(saved.body.marketplace_links,['https://flip.kz/shop/b']);
    assert.equal(saved.body.is_active,false);

    await page.getByRole('button',{name:'Изменить'}).click();
    await page.waitForFunction(()=>document.getElementById('shopDialog').open);
    const overflow=await page.evaluate(()=>{
      const d=document.getElementById('shopDialog');
      return {page:document.documentElement.scrollWidth-innerWidth,dialog:d.scrollWidth-d.clientWidth,bottom:d.getBoundingClientRect().bottom-innerHeight};
    });
    assert.ok(overflow.page<=0&&overflow.dialog<=0&&overflow.bottom<=0,JSON.stringify(overflow));
    assert.deepEqual(errors,[]);
    console.log('OK',viewport.width);
    await page.screenshot({path:process.env.SHOT_DIR?path.join(process.env.SHOT_DIR,`shop_editor_${viewport.width}.png`):path.join(require('os').tmpdir(),'shop_editor.png')});
    await page.context().close();
  }
  await browser.close();
  console.log('admin shops: passed');
})().catch(e=>{console.error(e);process.exit(1)});
