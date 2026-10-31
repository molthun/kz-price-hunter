// FA69-01, FA69-10: обзор мониторинга на настоящем JSON сервера и кнопки Discovery по настоящим правилам.
const {chromium}=require('playwright');
const {spawnSync}=require('node:child_process');
const fs=require('fs');
const os=require('os');
const path=require('path');
const assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..','..');
const PY=process.env.TEST_PYTHON||path.join(ROOT,'venv/bin/python');
const SCRIPT=`
import json, database, monitoring
from domain.discovery import CandidateStatus, candidate_actions
database.init_db()
print("JSON:" + json.dumps({
    "overview": monitoring.overview({}, [], {}, 3600),
    "candidates": [{"id": "c_" + s, "name": "Кандидат " + s, "status": s, "source_type": "website",
                    "actions": candidate_actions(s), "profiles": []} for s in sorted(CandidateStatus.ALL)],
}, ensure_ascii=False))
`;
(async()=>{
 const dataDir=fs.mkdtempSync(path.join(os.tmpdir(),'kzph-discovery-'));
 const run=spawnSync(PY,['-c',SCRIPT],{cwd:ROOT,encoding:'utf8',env:{...process.env,DATA_DIR:dataDir,PYTHONPATH:ROOT,PYTHONDONTWRITEBYTECODE:'1'},maxBuffer:1<<26});
 assert.equal(run.status,0,run.stderr);
 const line=run.stdout.split('\n').find(l=>l.startsWith('JSON:'));
 const real=JSON.parse(line.slice(5));
 assert.ok(real.overview.sections.discovery,'сервер отдаёт раздел discovery');
 const html=fs.readFileSync(path.join(ROOT,'web/templates/monitoring.html'),'utf8');
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1366,height:900},locale:'en-US'});
  const errors=[]; let sections=real.overview; const posted=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.tailwind={config:{}}});
  await page.route('**/*',route=>{
   const u=new URL(route.request().url());
   if(u.hostname!=='127.0.0.1')return route.abort();
   if(u.pathname==='/monitoring')return route.fulfill({contentType:'text/html',body:html});
   if(u.pathname==='/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:JSON.stringify(sections)});
   if(u.pathname==='/api/v2/admin/discovery/candidates')return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,candidates:real.candidates,total:real.candidates.length})});
   if(route.request().method()==='POST'){posted.push(u.pathname);return route.fulfill({contentType:'application/json',body:'{"ok":true}'});}
   return route.fulfill({contentType:'application/json',body:'{}'});
  });
  await page.goto('http://127.0.0.1:18193/monitoring#overview');
  await page.waitForSelector('#btnAsk');
  await page.waitForSelector('#dailyBox');
  assert.equal(await page.locator('#view button[data-section="discovery"]').count(),1);
  assert.ok((await page.locator('#view button[data-section="discovery"]').textContent()).includes('Кандидатов пока нет'));

  // Сервер старой версии без раздела: карточка «нет данных», остальной обзор жив
  sections=JSON.parse(JSON.stringify(real.overview)); delete sections.sections.discovery;
  await page.reload();
  await page.waitForSelector('#btnAsk');
  assert.ok((await page.locator('#view button[data-section="discovery"]').textContent()).includes('Нет данных'));
  sections=real.overview;

  await page.goto('http://127.0.0.1:18193/monitoring#discovery');
  await page.reload();
  await page.waitForSelector('[data-discovery-decision="c_CATALOG_FOUND"]');
  const has=(sel)=>page.locator(sel).count();
  for(const c of real.candidates){
   assert.equal(await has(`[data-discovery-profile="${c.id}"]`),c.actions.profile?1:0,'profile '+c.status);
   assert.equal(await has(`[data-discovery-review="${c.id}"]`),c.actions.review?1:0,'review '+c.status);
   assert.equal(await has(`[data-discovery-decision="${c.id}"][data-decision="APPROVED"]`),c.actions.approve?1:0,'approve '+c.status);
   assert.equal(await has(`[data-discovery-decision="${c.id}"][data-decision="REJECTED"]`),c.actions.reject?1:0,'reject '+c.status);
  }
  assert.equal(await has('[data-discovery-profile="c_REJECTED"]'),0);
  assert.equal(await has('[data-discovery-decision="c_PROFILED"][data-decision="APPROVED"]'),0);
  await page.click('[data-discovery-review="c_PROFILED"]');
  await page.waitForFunction(()=>true);
  await page.waitForTimeout(100);
  assert.ok(posted.includes('/api/v2/admin/discovery/candidates/c_PROFILED/review'));
  assert.deepEqual(errors,[]);
  console.log('Discovery monitoring PASS');
 }finally{await browser.close();fs.rmSync(dataDir,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exit(1)});
