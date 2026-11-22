// v6.43.0: Браузерный тест загрузки прайс-листа/скриншота продавца через Vision в /admin (#shopDialog).
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

    // Вход администратора
    await page.goto(base + '/profile');
    const loginRes = await page.evaluate(async () => {
      const r = await fetch('/api/auth/dev-login', {method: 'POST'});
      return {status: r.status, data: await r.json()};
    });
    assert.equal(loginRes.status, 200);
    assert.equal(loginRes.data.user.is_admin, true);

    await page.goto(base + '/admin');
    await page.locator('.admin-nav a[href="#shopsTool"]').click();
    await page.waitForSelector('#shopsTool', {state: 'visible'});

    // 1. У нового магазина секция Vision скрыта
    await page.getByRole('button', {name: 'Добавить магазин'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open);
    assert.equal(await page.locator('#shopVisionSection').isHidden(), true);

    // Создаём Instagram/WhatsApp магазин
    await page.fill('#shopForm [name=name]', 'Мобильник Тест Vision');
    await page.fill('#shopForm [name=city]', 'Астана');
    await page.fill('#shopForm [name=instagram]', '@mobilnik_vision_test');
    await page.fill('#shopForm [name=whatsapp]', '+7 777 555 4433');
    await page.locator('#shopForm').getByRole('button', {name: 'Сохранить'}).click();
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);

    // 2. Открываем созданный магазин — секция Vision доступна
    const row = page.locator('#shopsList .user').filter({hasText: 'Мобильник Тест Vision'});
    await row.waitFor();
    await row.getByRole('button', {name: 'Изменить'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open);
    assert.equal(await page.locator('#shopVisionSection').isVisible(), true);

    // Раскрываем панель Vision
    await page.locator('#shopVisionSection summary').click();
    assert.equal(await page.locator('#shopVisionExtractBtn').isVisible(), true);

    // Мокаем только ответ /vision-extract (сам /vision-publish идёт в реальный backend!)
    await page.route('**/api/admin/sellers/*/vision-extract', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          city: 'Астана',
          total: 2,
          publishable: [
            {
              raw_title: 'Apple iPhone 16 Pro 256GB Desert Titanium',
              price: 629000,
              old_price: 679000,
              currency: 'KZT',
              confidence: 0.94,
              publishable: true,
            },
          ],
          rejected: [
            {
              raw_title: 'Samsung S24 Ultra',
              price: null,
              currency: 'KZT',
              confidence: 0.85,
              publishable: false,
              rejection_reason: 'occluded_price',
              rejection_label: 'Цена закрыта или нечитаема',
            },
          ],
        }),
      });
    });

    // Загружаем фиктивный PNG и нажимаем «Распознать прайс»
    const fakePng = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.alloc(32)]);
    await page.setInputFiles('#shopVisionFile', {
      name: 'price.png',
      mimeType: 'image/png',
      buffer: fakePng,
    });
    await page.click('#shopVisionExtractBtn');

    await page.waitForSelector('#shopVisionPreview', {state: 'visible'});
    const rowsText = await page.locator('#shopVisionRows').innerText();
    assert.match(rowsText, /Apple iPhone 16 Pro 256GB/);
    assert.match(rowsText, /629/);
    assert.equal(await page.locator('#shopVisionRejectedBox').isVisible(), true);
    assert.match(await page.locator('#shopVisionRejectedList').innerText(), /Цена закрыта или нечитаема/);

    // Проверка адаптивности в мобильном вьюпорте и обеих темах (light / dark)
    for (const scheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme: scheme});
      await page.setViewportSize({width: 390, height: 844});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 2, `Horizontal overflow ${overflow} in ${scheme} mobile viewport`);
    }
    await page.setViewportSize({width: 1366, height: 900});

    // 3. Публикуем выбранную позицию через реальный /vision-publish
    await page.click('#shopVisionPublishBtn');
    await page.waitForFunction(() => {
      const txt = (document.getElementById('shopVisionState') || {}).textContent || '';
      return /Опубликовано предложений: 1/.test(txt);
    });

    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);

    // Проверяем, что в списке магазинов обновился счётчик предложений
    const updatedRowText = await row.innerText();
    assert.match(updatedRowText, /предложений: 1/);

    // 4. Сквозной сценарий только по тексту описания/комментариев (БЕЗ мока /vision-extract!)
    await page.unroute('**/api/admin/sellers/*/vision-extract');
    await row.getByRole('button', {name: 'Изменить'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open);
    await page.locator('#shopVisionSection summary').click();
    await page.fill('#shopVisionText', 'Apple iPhone 15 128GB — 395 000 ₸');
    await page.click('#shopVisionExtractBtn');
    await page.waitForSelector('#shopVisionPreview', {state: 'visible'});
    const txtRows = await page.locator('#shopVisionRows').innerText();
    assert.match(txtRows, /iPhone 15 128GB/i);
    assert.match(txtRows, /Текст/);
    await page.click('#shopVisionPublishBtn');
    await page.waitForFunction(() => {
      const txt = (document.getElementById('shopVisionState') || {}).textContent || '';
      return /Опубликовано предложений: 1/.test(txt);
    });
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);
    assert.match(await row.innerText(), /предложений: 2/);

    assert.deepEqual(errors, [], 'Unexpected page errors: ' + errors.join('; '));

    console.log('test_frontend_admin_vision_upload PASS');
  } finally {
    if (browser) await browser.close();
    child.kill('SIGTERM');
  }
}

runTest().catch(err => {
  console.error(err);
  process.exit(1);
});
