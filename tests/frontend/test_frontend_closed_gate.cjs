// Закрытая страница входа: ссылка бота, виджет-iframe, bot-start/bot-poll, ожидание, выход,
// истечение и отказ. Отрицательные проверки: на странице нет оболочки приложения, имя экранируется,
// запросы уходят только к ожидаемым адресам (A02 аудита 18.11.2026).
const {chromium}=require('playwright');
const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../../web/templates/closed_login.html'),'utf8');
const ORIGIN='http://127.0.0.1:18110';
const ALLOWED=['/search','/api/me','/api/auth/logout','/api/auth/bot-start','/api/auth/bot-poll'];
(async()=>{const browser=await chromium.launch({channel:'chrome'});try{
 for(const width of [390,1366])for(const colorScheme of ['light','dark']){
 const context=await browser.newContext({viewport:{width,height:900},colorScheme});
 const page=await context.newPage(),errors=[],api=[],external=[];
 const st={pending:false,fail:false,start:'ok',poll:'pending',starts:0,polls:0,loads:0};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{
  const u=new URL(route.request().url());
  if(u.origin!==ORIGIN){external.push(u.host);return route.abort();}
  api.push(u.pathname);
  const json=(body,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
  if(u.pathname==='/api/me'){
   if(st.fail)return route.fulfill({status:500,body:'error'});
   return json({maintenance_mode:true,user:st.pending?{first_name:'Тест <img src=x>',is_confirmed:false}:null,auth:{bot_username:'fixture_bot',dev_login:false}});
  }
  if(u.pathname==='/api/auth/logout'){st.pending=false;return json({status:'ok'});}
  if(u.pathname==='/api/auth/bot-start'){
   st.starts++;
   if(st.start==='error')return json({status:'error',message:'Слишком много запросов'},429);
   const token='tok'+st.starts;return json({status:'ok',token,bot_username:'fixture_bot',bot_url:'https://t.me/fixture_bot?start=login_'+token});
  }
  if(u.pathname==='/api/auth/bot-poll'){
   st.polls++;
   assert.equal(u.searchParams.get('token'),'tok'+st.starts,'опрашивается последний выданный токен');
   if(st.poll==='ok'){st.poll='pending';st.pending=true;return json({status:'ok'});}
   if(st.poll==='expired'){st.poll='pending';return json({status:'expired',message:'Срок действия ссылки истёк'},410);}
   if(st.poll==='blocked')return json({status:'error',message:'Аккаунт заблокирован администратором'},403);
   return json({status:'pending'});
  }
  st.loads++;return route.fulfill({contentType:'text/html',body:html});
 });
 const link=page.locator('#directBotLink'),status=page.locator('#status');
 const shell=async()=>{
  // Оболочки приложения нет; единственная ссылка — кнопка бота, и только с настоящим адресом
  assert.equal(await page.locator('nav,header,footer').count(),0);
  assert.equal(await page.locator('a:not(#directBotLink)').count(),0);
  if(await link.isVisible())assert.match(await link.getAttribute('href'),/^https:\/\/t\.me\/fixture_bot\?start=login_tok\d+$/);
 };
 const heading=name=>page.getByRole('heading',{name,exact:true}).waitFor();
 const pollsStopped=async()=>{const n=st.polls;await page.waitForTimeout(1800);assert.equal(st.polls,n,'опрос бота остановлен');};

 // 1. Гость: ссылка бота с токеном, виджет Telegram в iframe, опрос идёт
 await page.goto(ORIGIN+'/search?q=private');
 await page.getByText('После входа потребуется подтверждение владельца.',{exact:true}).waitFor();
 await link.waitFor({state:'visible'});
 assert.equal(await link.getAttribute('href'),'https://t.me/fixture_bot?start=login_tok1');
 assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
 const src=await page.locator('#widget iframe').getAttribute('src');
 assert.ok(src.startsWith('https://oauth.telegram.org/embed/fixture_bot?'),src);
 assert.equal(new URL(src).searchParams.get('origin'),ORIGIN);
 assert.equal(await page.locator('#widget script').count(),0);
 assert.equal(await page.locator('#pendingActions').isVisible(),false);
 await shell();
 await page.waitForTimeout(1700);assert.ok(st.polls>=1,'бот опрашивается');
 assert.equal(st.starts,1,'повторные проверки доступа не плодят токены');

 // 2. Истечение ссылки: новый токен, новая ссылка, опрос продолжается по новому токену
 st.poll='expired';
 await page.waitForFunction(()=>document.getElementById('directBotLink').href.endsWith('login_tok2'),null,{timeout:6000});
 assert.equal(st.starts,2);await link.waitFor({state:'visible'});await shell();

 // 3. Бот подтвердил вход → перезагрузка → ожидание подтверждения владельцем, имя экранировано
 const loadsBefore=st.loads;st.poll='ok';
 await heading('Ожидание подтверждения');
 assert.ok(st.loads>loadsBefore,'после входа страница перезагружена');
 assert.equal(await page.locator('#identity').innerText(),'Тест <img src=x>');
 assert.equal(await page.locator('#identity img').count(),0);
 assert.equal(await link.isVisible(),false);assert.equal(await page.locator('#widget').isVisible(),false);
 await pollsStopped();await shell();

 // 4. Выход → снова форма входа с новой ссылкой бота
 await page.getByRole('button',{name:'Выйти',exact:true}).click();
 await heading('Вход на закрытый сайт');
 await link.waitFor({state:'visible'});await shell();

 // 5. Отказ (заблокированный аккаунт): ссылка скрыта, причина показана, опрос прекращён
 st.poll='blocked';
 await page.getByText('Аккаунт заблокирован администратором',{exact:true}).waitFor({timeout:6000});
 assert.equal(await link.isVisible(),false);assert.equal(await link.getAttribute('href'),'#');
 await pollsStopped();await shell();

 // 6. bot-start отказал: кнопки бота нет, виджет остаётся
 st.poll='pending';st.start='error';const startsBefore=st.starts;
 await page.reload();await heading('Вход на закрытый сайт');
 await page.waitForFunction(()=>document.getElementById('status').textContent.includes('подтверждение'));await page.waitForTimeout(300);
 assert.ok(st.starts>startsBefore);assert.equal(await link.isVisible(),false);
 assert.equal(await page.locator('#widget iframe').count(),1);await shell();

 // 7. /api/me недоступен → «Повторить», без оболочки и прокрутки
 st.fail=true;await page.evaluate(()=>checkAccess());
 await page.getByRole('button',{name:'Повторить',exact:true}).waitFor();
 await shell();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight),false);

 assert.deepEqual(errors,[]);
 assert.ok(api.every(p=>ALLOWED.includes(p)),api.join(','));
 assert.ok(external.every(h=>['telegram.org','oauth.telegram.org'].includes(h)),external.join(','));
 console.log(`PASS closed login ${width}/${colorScheme}`);await context.close();
 }
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
