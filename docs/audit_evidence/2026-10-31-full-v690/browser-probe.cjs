// Offline independent probe with the real monitoring.overview JSON on a fresh DB.
const {chromium} = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const root = process.argv[2];
const evidence = __dirname;
(async () => {
  const browser = await chromium.launch({channel:'chrome', headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1366,height:900}});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.addInitScript(() => {window.tailwind={config:{}}});
    const html = fs.readFileSync(path.join(root,'web/templates/monitoring.html'),'utf8');
    const response = fs.readFileSync(path.join(evidence,'monitoring-response.json'),'utf8');
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if(u.hostname !== '127.0.0.1')return route.abort();
      if(u.pathname === '/monitoring')return route.fulfill({contentType:'text/html',body:html});
      if(u.pathname === '/api/admin/monitoring')return route.fulfill({contentType:'application/json',body:response});
      if(u.pathname === '/api/v2/admin/discovery/stats')return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,candidates:{}})});
      if(u.pathname === '/api/v2/admin/discovery/candidates')return route.fulfill({contentType:'application/json',body:JSON.stringify({ok:true,candidates:[
        {id:'audit_profiled',name:'Audit store',status:'PROFILED',source_type:'website',profiles:[]},
        {id:'audit_rejected',name:'Rejected store',status:'REJECTED',source_type:'website',profiles:[]}
      ]})});
      return route.fulfill({contentType:'application/json',body:'{}'});
    });
    await page.goto('http://127.0.0.1:18199/monitoring#overview');
    await page.waitForFunction(() => document.getElementById('generatedAt').textContent.includes('Данные на'));
    await page.waitForTimeout(100);
    const overview={errors:[...errors],assistant:await page.locator('#btnAsk').count(),daily:await page.locator('#dailyBox').count()};
    assert(overview.errors.some(e=>e.includes('reason')));
    assert.equal(overview.assistant,0);assert.equal(overview.daily,0);
    await page.goto('http://127.0.0.1:18199/monitoring#discovery');
    await page.reload();
    await page.waitForSelector('[data-discovery-decision="audit_profiled"]');
    const discovery={
      approve_profiled:await page.locator('[data-discovery-decision="audit_profiled"][data-decision="APPROVED"]').count(),
      profile_rejected:await page.locator('[data-discovery-profile="audit_rejected"]').count()
    };
    assert.equal(discovery.approve_profiled,1);assert.equal(discovery.profile_rejected,1);
    console.log(JSON.stringify({overview,discovery},null,2));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
