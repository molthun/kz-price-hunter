const {chromium}=require('playwright'),fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/admin.html'),'utf8'),BASE='http://127.0.0.1:18117';
(async()=>{const b=await chromium.launch({channel:'chrome'});try{
for(const width of [390,1366])for(const scheme of ['light','dark']){
 const c=await b.newContext({viewport:{width,height:900},colorScheme:scheme}),p=await c.newPage(),errors=[];let deletes=0;
 let items=['checking','rejected'].map((status,i)=>({id:'r'+i,query:'Запрос '+i,city:'Все',status,plan:{category_name:'Раздел',fields:[]},demand:1}));
 p.on('pageerror',e=>errors.push(e.message));
 await p.route('**/*',async r=>{const u=new URL(r.request().url());if(u.origin!==BASE)return r.abort();const J=x=>r.fulfill({contentType:'application/json',body:JSON.stringify(x)});
 if(u.pathname==='/api/admin/catalog-expansions'){const snapshot=JSON.parse(JSON.stringify(items));await new Promise(resolve=>setTimeout(resolve,100));return J({items:snapshot});}
 if(u.pathname.startsWith('/api/admin/catalog-expansions/')&&r.request().method()==='DELETE'){deletes++;items=items.filter(x=>u.pathname!==('/api/admin/catalog-expansions/'+x.id));await new Promise(resolve=>setTimeout(resolve,100));return J({ok:true});}
 if(u.pathname==='/api/admin/users')return J([]);if(u.pathname==='/api/admin/sellers')return J({sellers:[]});if(u.pathname.startsWith('/api/'))return J({items:[],categories:[],summary:{}});
 return r.fulfill({contentType:'text/html',body:html});});
 await p.goto(BASE+'/admin#catalogExpansionTool');await p.getByText('Запрос 1 · Все',{exact:true}).waitFor();
 const row=q=>p.locator('#expansionList .user').filter({has:p.getByText(q+' · Все',{exact:true})});
 p.once('dialog',d=>d.dismiss());await row('Запрос 1').getByRole('button',{name:'Удалить',exact:true}).click();assert.equal(deletes,0);
 p.once('dialog',d=>d.accept());await row('Запрос 1').getByRole('button',{name:'Удалить',exact:true}).click();
 await p.getByText('Заявка «Запрос 1» удалена.',{exact:true}).waitFor();assert.equal(deletes,1);assert.equal(await row('Запрос 1').count(),0);
 assert.equal(await row('Запрос 0').count(),1);
 await p.evaluate(()=>{loadExpansions(true);});
 p.once('dialog',d=>d.accept());await row('Запрос 0').getByRole('button',{name:'Удалить',exact:true}).click();
 await p.getByText('Заявка «Запрос 0» удалена.',{exact:true}).waitFor();
 assert.equal(deletes,2);assert.equal(await p.locator('#expansionList .user').count(),0);
 assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);
 console.log(`PASS delete rejected/running request ${width}/${scheme}; cancel confirmation and stale poll`);await c.close();
}
}finally{await b.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
