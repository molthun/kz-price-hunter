// v6.44.4: Браузерный тест раздела «Рекомендуемые нормы объёма источников» в /admin#monitoringTool.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');
const ROOT = path.resolve(__dirname, '..', '..');

async function startServer() {
  const child = spawn(process.env.TEST_PYTHON || path.join(ROOT, 'venv/bin/python'), ['-u', '-m', 'tests.web.test_browser_server'],
    {cwd: ROOT, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', BROWSER_TEST_SEED: 'deals'}, stdio: ['ignore', 'pipe', 'pipe']});
  const base = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(Error('Test server startup timeout')), 20000);
    child.stdout.on('data', c => {
      output += c;
      const m = output.match(/BROWSER_TEST_URL=(http:\/\/127\.0\.0\.1:\d+)/);
      if (m) {
        clearTimeout(timer);
        resolve(m[1]);
      }
    });
    child.on('exit', code => {
      clearTimeout(timer);
      reject(Error('Test server exited ' + code));
    });
  });
  return {child, base};
}

async function runTest() {
  const {child, base} = await startServer();
  let browser;
  try {
    browser = await chromium.launch({channel: 'chrome', headless: true});
    const context = await browser.newContext({viewport: {width: 1366, height: 900}, locale: 'ru-RU'});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));

    await page.goto(base + '/profile');
    const loginRes = await page.evaluate(async () => {
      const r = await fetch('/api/auth/dev-login', {method: 'POST'});
      return {status: r.status, data: await r.json()};
    });
    assert.equal(loginRes.status, 200);

    let confirmed = false;
    await page.route('**/api/admin/source-norm/recommendations', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          items: confirmed ? [] : [{
            shop_key: 'fortemarket',
            source_url: 'https://market.forte.kz/catalog?facet=Smartphones',
            category: 'Forte: Смартфоны',
            recommended_valid: 4282,
            samples: 5,
            current_baseline: 12592,
            current_basis: 'active_offers',
            active_offers: 12592,
          }],
        }),
      });
    });
    await page.route('**/api/admin/source-norm/confirm', async route => {
      const payload = route.request().postDataJSON();
      assert.equal(payload.shop_key, 'fortemarket');
      assert.equal(payload.source_url, 'https://market.forte.kz/catalog?facet=Smartphones');
      confirmed = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ok: true, confirmed: {valid: 4282, samples: 5}}),
      });
    });

    await page.goto(base + '/admin#monitoringTool');
    await page.waitForSelector('#sourceNormSection', {state: 'visible'});
    await page.waitForSelector('#sourceNormList button', {state: 'visible'});
    const listText = await page.locator('#sourceNormList').innerText();
    assert.match(listText, /Forte: Смартфоны/);
    assert.match(listText, /4282/);

    // Проверка светлой и тёмной темы на мобильном экране
    for (const scheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme: scheme});
      await page.setViewportSize({width: 390, height: 844});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 2, `Horizontal overflow ${overflow} in ${scheme} mobile viewport`);
    }
    await page.setViewportSize({width: 1366, height: 900});

    await page.locator('#sourceNormList').getByRole('button', {name: 'Подтвердить новую норму'}).click();
    await page.waitForFunction(() => {
      const txt = (document.getElementById('sourceNormState') || {}).textContent || '';
      return /подтверждена/i.test(txt);
    });
    assert.equal(await page.locator('#sourceNormList button').count(), 0);
    assert.deepEqual(errors, [], 'Unexpected page errors: ' + errors.join('; '));

    console.log('test_frontend_admin_source_norm PASS');
  } finally {
    if (browser) await browser.close();
    child.kill('SIGTERM');
  }
}

runTest().catch(err => {
  console.error(err);
  process.exit(1);
});
