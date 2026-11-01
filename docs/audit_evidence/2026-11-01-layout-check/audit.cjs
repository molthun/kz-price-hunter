const {chromium}=require('playwright');const fs=require('fs');
const S=process.env.OUT;const D=JSON.parse(fs.readFileSync(S+'/data.json','utf8'));const ADMIN=fs.readFileSync('web/templates/admin.html','utf8');
const B='https://shdev.molthun.ru';
const USERS={admin:{id:171013461,first_name:'Николай',last_name:'Богданец',username:'molthun',is_admin:true,is_confirmed:true,is_approved:true,photo_url:''},guest:null};
const ROUTES=[['home','/'],['cat','/search?cat='+encodeURIComponent('Смартфоны')],['empty','/search?q=qqqzzzxxx'],['prod','/product/'+D.pid],['prodlong','/product/'+D.pid2],['deals','/deals'],['fav','/favorites'],['profile','/profile'],['admin','/admin']];
const W=[[320,640,1],[360,740,1],[390,844,1],[430,932,1],[844,390,1],[768,1024,1],[834,1194,1],[900,700,0],[1024,768,0],[1280,800,0],[1366,768,0],[1440,900,0],[1920,1080,0],[2560,1440,0]];
const CHECK=()=>{const out={};const iw=innerWidth;out.hscroll=document.documentElement.scrollWidth>iw+1?document.documentElement.scrollWidth:0;
 const vis=e=>{const s=getComputedStyle(e);if(s.display==='none'||s.visibility==='hidden'||+s.opacity===0)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0};
 const clipped=e=>{for(let p=e.parentElement;p;p=p.parentElement){const o=getComputedStyle(p).overflowX;if(o!=='visible')return true}return false};
 const name=e=>e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(e.className&&e.className.baseVal===undefined&&e.className?'.'+String(e.className).trim().split(/\s+/)[0]:'');
 const all=[...document.querySelectorAll('body *')].filter(e=>!['SCRIPT','STYLE','OPTION'].includes(e.tagName)&&vis(e));
 const off=new Set();for(const e of all){const r=e.getBoundingClientRect();if((r.right>iw+1||r.left<-1)&&getComputedStyle(e).position!=='fixed'&&!clipped(e))off.add(name(e)+'['+Math.round(r.left)+'..'+Math.round(r.right)+']')}out.off=[...off].slice(0,6);
 const cut=new Set();for(const e of all){const s=getComputedStyle(e);if(e.children.length===0&&e.textContent.trim()&&s.overflowX!=='visible'&&s.textOverflow!=='ellipsis'&&e.scrollWidth>e.clientWidth+1&&e.clientWidth>0&&s.overflowX!=='auto'&&s.overflowX!=='scroll')cut.add(name(e)+':'+e.textContent.trim().slice(0,24))}
 out.cut=[...cut].slice(0,5);
 const ov=new Set();for(const e of all){if(e.children.length)continue;const t=e.textContent.trim();if(!t)continue;const r=e.getBoundingClientRect();const p=e.parentElement.getBoundingClientRect();if(r.right>p.right+2&&getComputedStyle(e.parentElement).overflow==='visible'&&!clipped(e))ov.add(name(e)+':'+t.slice(0,20))}out.spill=[...ov].slice(0,5);
 const small=new Set();if(iw<768)for(const e of document.querySelectorAll('a,button,select,input:not([type=radio]):not([type=checkbox])')){if(!vis(e))continue;const r=e.getBoundingClientRect();if(r.height<32||r.width<32)small.add(name(e)+':'+Math.round(r.width)+'x'+Math.round(r.height)+':'+(e.textContent||'').trim().slice(0,14))}out.small=[...small].slice(0,8);out.smalln=small.size;
 const hdr=document.querySelector('header');out.hdrH=hdr?Math.round(hdr.getBoundingClientRect().height):0;
 const hr=document.querySelector('.header-right');if(hr&&vis(hr)){const r=hr.getBoundingClientRect();if(r.right>iw+1)out.hdrOff=Math.round(r.right)}
 const nav=document.querySelector('.bottom-nav');if(nav&&vis(nav)){const f=document.querySelector('.site-footer');out.bottomNav=Math.round(nav.getBoundingClientRect().height);if(f&&vis(f)){out.footerPad=parseInt(getComputedStyle(f).paddingBottom)}}
 return out};
(async()=>{const b=await chromium.launch({channel:'chrome'});const res=[];const shots=new Set((process.env.SHOTS||'').split(','));
 async function ctx(w,h,mob,cs,who){const c=await b.newContext({viewport:{width:w,height:h},locale:'ru-RU',colorScheme:cs,hasTouch:!!mob,isMobile:!!mob,deviceScaleFactor:1,ignoreHTTPSErrors:true});
  const p=await c.newPage();p.errs=[];p.on('pageerror',e=>p.errs.push(e.message.slice(0,90)));
  await p.route('**/*',r=>{const u=new URL(r.request().url());const J=o=>r.fulfill({contentType:'application/json',body:JSON.stringify(o)});
   if(r.request().resourceType()==='image'&&!u.href.startsWith('data:'))return r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400"><rect width="400" height="400" fill="#8a94a6"/></svg>'});
   if(u.hostname!=='shdev.molthun.ru')return r.abort();
   const pa=u.pathname;
   if(pa==='/admin')return r.fulfill({contentType:'text/html',body:ADMIN});
   if(pa==='/api/me')return J({ok:true,authenticated:!!USERS[who],user:USERS[who],maintenance_mode:false,auth:{},settings:{}});
   if(pa==='/api/me/watches')return J({ok:true,watches:[]});
   if(pa==='/api/v2/search')return J(u.searchParams.get('q')==='qqqzzzxxx'?D.search_empty:D.search);
   if(pa==='/api/v2/categories')return J(D.cats);
   if(pa==='/api/v2/deals')return J({...D.deals,has_more:false});
   if(pa.startsWith('/api/v2/products/'))return J(pa.endsWith(D.pid2)?D.product_long:D.product);
   if(pa==='/api/admin/users')return J([USERS.admin,{id:2,first_name:'Константин-Александр Очень Длинное Имя',username:'very_long_username_for_check',is_approved:false},{id:3,first_name:'Анна',is_approved:true},{id:4,username:'bad',is_blocked:true}]);
   if(pa==='/api/version')return J({version:'6.12.1'});
   if(pa==='/api/changelog')return J({releases:[{version:'6.12.1',date:'2026-11-01',items:['Длинная запись об изменении, которая должна переноситься на узком экране и не выходить за край окна']}]});
   if(pa.startsWith('/api/'))return J({ok:true});
   return r.continue()});
  return [c,p]}
 const mode=process.env.MODE||'pages';
 if(mode==='pages'){for(const [w,h,mob] of W)for(const [cs,who] of [['dark','admin'],['light','guest']])for(const [n,path] of ROUTES){if(n==='admin'&&who==='guest')continue;
   const [c,p]=await ctx(w,h,mob,cs,who);try{await p.goto(B+path,{waitUntil:'domcontentloaded'});await p.waitForTimeout(700);
   const r=await p.evaluate(CHECK);r.w=w;r.h=h;r.page=n;r.cs=cs;r.who=who;r.errs=p.errs;res.push(r);
   if(shots.has(n+'-'+w)&&cs==='dark'){await p.screenshot({path:S+'/'+n+'_'+w+'.png',fullPage:false})}}catch(e){res.push({w,page:n,cs,fail:e.message.slice(0,90)})}await c.close()}}
 else{const OV={catalog:async p=>{await p.goto(B+'/search');await p.waitForTimeout(600);await p.evaluate(()=>openCatalogModal())},filters:async p=>{await p.goto(B+'/search?cat=x');await p.waitForTimeout(600);await p.evaluate(()=>openMobileFilters())},changelog:async p=>{await p.goto(B+'/');await p.waitForTimeout(500);await p.evaluate(()=>openChangelogModal())},seller:async p=>{await p.goto(B+'/');await p.waitForTimeout(500);await p.evaluate(()=>openRegisterSellerModal())},watch:async p=>{await p.goto(B+'/product/'+D.pid);await p.waitForTimeout(700);await p.evaluate(()=>openWatchModalFromDetail())},themeMenu:async p=>{await p.goto(B+'/profile');await p.waitForTimeout(600);await p.evaluate(()=>toggleThemeMenu(true))}};
  for(const [w,h,mob] of W)for(const n of Object.keys(OV)){const [c,p]=await ctx(w,h,mob,'dark','admin');try{await OV[n](p);await p.waitForTimeout(500);
   const r=await p.evaluate(CHECK);r.w=w;r.h=h;r.page='ov:'+n;r.errs=p.errs;
   r.box=await p.evaluate(()=>{const m=[...document.querySelectorAll('.modal-content,.modal-box,.modal-dialog,.catalog-modal,.catalog-panel,#filtersSidebar.mobile-open,.modal-backdrop.active>*,.modal-backdrop.show>*,#themeMenu:not([hidden])')].filter(e=>e.getBoundingClientRect().width>0)[0];if(!m)return null;const r=m.getBoundingClientRect();return {cls:m.className.split(' ')[0]||m.id,l:Math.round(r.left),t:Math.round(r.top),r:Math.round(r.right),b:Math.round(r.bottom),scroll:getComputedStyle(m).overflowY}});
   res.push(r);if(shots.has(n+'-'+w))await p.screenshot({path:S+'/ov_'+n+'_'+w+'.png'})}catch(e){res.push({w,page:'ov:'+n,fail:e.message.slice(0,90)})}await c.close()}}
 fs.writeFileSync(S+'/res_'+mode+'.json',JSON.stringify(res));await b.close()})().catch(e=>{console.error(e);process.exit(1)});
