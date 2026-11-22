// v6.45.0: Браузерный тест «Мои размеры» в профиле, быстрой кнопки фильтра в поиске и подсветки размера в карточке товара.
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

    // 1. В профиле выбираем размер обуви 42 и одежды L, проверяем сохранение после перезагрузки
    await page.goto(base + '/profile');
    await page.waitForSelector('#profileSizesCard', {state: 'visible'});
    await page.selectOption('#profileShoeSize', '42');
    await page.selectOption('#profileClothingSize', 'L');
    await page.waitForFunction(() => /42/.test((document.getElementById('profileSizesState') || {}).textContent || ''));

    // Проверка адаптивности в светлой и тёмной теме на мобильном экране
    for (const scheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme: scheme});
      await page.setViewportSize({width: 390, height: 844});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 2, `Profile overflow ${overflow} in ${scheme} mobile viewport`);
    }
    await page.setViewportSize({width: 1366, height: 900});

    await page.reload();
    await page.waitForSelector('#profileSizesCard', {state: 'visible'});
    assert.equal(await page.inputValue('#profileShoeSize'), '42');
    assert.equal(await page.inputValue('#profileClothingSize'), 'L');

    // US-01: при сбое localStorage.setItem показывается честное сообщение ошибки, а не «Сохранено»
    const errText = await page.evaluate(() => {
      const orig = Storage.prototype.setItem;
      Storage.prototype.setItem = () => { throw new Error('QuotaExceededError'); };
      try {
        onUserSizesChanged();
        return (document.getElementById('profileSizesState') || {}).textContent || '';
      } finally {
        Storage.prototype.setItem = orig;
      }
    });
    assert.match(errText, /Не удалось сохранить/);

    // 2. В поиске при наличии фасета «Размер» появляется быстрая кнопка «Мой размер: 42»
    let capturedAttrSize = null;
    await page.route('**/api/v2/search**', async route => {
      const u = new URL(route.request().url());
      let parsedAttrs = {};
      try { parsedAttrs = JSON.parse(u.searchParams.get('attributes') || '{}'); } catch (e) {}
      capturedAttrSize = parsedAttrs.size || null;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          outcome: 'FOUND',
          query: 'кроссовки',
          parsed_query: {raw_query: 'кроссовки', clean_query: 'кроссовки'},
          total_products: 1,
          total_offers: 1,
          items: [{
            product: {id: 'p_shoe_1', title: 'Vans Кроссовки Тест', category: 'Одежда и обувь'},
            min_price: 34000,
            offers_count: 1,
            has_purchasable_offers: true,
            available_sizes: ['36', '37', '38', '39', '40', '41', '42', '43'],
            offers: [{
              id: 'off_shoe_1',
              product_id: 'p_shoe_1',
              seller_name: 'Интертоп',
              channel_name: 'Интертоп',
              price: 34000,
              condition: 'NEW',
              availability: 'in_stock',
              city: 'Алматы',
              url: 'https://intertop.kz/product/1',
              is_purchasable: true,
              available_sizes: ['40', '41', '42', '43'],
            }],
          }],
          attribute_facets: [{
            key: 'size',
            label: 'Размер',
            values: [{value: '40', count: 1}, {value: '42', count: 1}, {value: '43', count: 1}],
          }],
          selected_attributes: capturedAttrSize ? {size: capturedAttrSize} : {},
        }),
      });
    });

    await page.goto(base + '/search?q=%D0%BA%D1%80%D0%BE%D1%81%D1%81%D0%BE%D0%B2%D0%BA%D0%B8');
    // v6.50.0: на карточке выдачи видны размеры в наличии, а размер 42 (индекс 6 > 5) поднят в видимые и подсвечен
    const cardSizesRow = page.locator('.product-card .card-sizes-row');
    await cardSizesRow.waitFor({state: 'visible'});
    const cardMyChip = cardSizesRow.locator('.offer-size-chip.is-my-size');
    await cardMyChip.waitFor({state: 'visible'});
    assert.match(await cardMyChip.innerText(), /42/);
    assert.equal(await cardSizesRow.locator('.card-size-more').innerText(), '+2');

    const quickBtn = page.locator('.my-size-quick-btn').filter({hasText: 'Мой размер: 42'});
    await quickBtn.waitFor({state: 'visible'});
    await quickBtn.click();
    await page.waitForFunction(() => document.querySelector('.my-size-quick-btn.active') !== null);
    assert.equal(capturedAttrSize, '42');

    // 3. В карточке товара сохранённый размер 42 подсвечен классом .is-my-size
    await page.route('**/api/v2/products/p_shoe_1**', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          product: {id: 'p_shoe_1', title: 'Vans Кроссовки Тест', category: 'Одежда и обувь'},
          offers: [{
            id: 'off_shoe_1',
            product_id: 'p_shoe_1',
            seller_name: 'Интертоп',
            channel_name: 'Интертоп',
            price: 34000,
            condition: 'NEW',
            availability: 'in_stock',
            city: 'Алматы',
            url: 'https://intertop.kz/product/1',
            is_purchasable: true,
            available_sizes: ['40', '41', '42', '43'],
          }],
        }),
      });
    });
    await page.goto(base + '/product/p_shoe_1');
    const myChip = page.locator('.offer-size-chip.is-my-size');
    await myChip.waitFor({state: 'visible'});
    assert.match(await myChip.innerText(), /42/);

    // 4. Серверная синхронизация (v6.46.0): вход -> сохранение размеров -> очистка localStorage -> восстановление с /api/me
    await page.goto(base + '/profile');
    const loginRes = await page.evaluate(async () => {
      const r = await fetch('/api/auth/dev-login', {method: 'POST'});
      return {status: r.status, data: await r.json()};
    });
    assert.equal(loginRes.status, 200);
    await page.reload();
    await page.waitForSelector('#profileSizesCard', {state: 'visible'});
    await page.selectOption('#profileShoeSize', '43');
    await page.waitForFunction(() => /синхронизировано/i.test((document.getElementById('profileSizesState') || {}).textContent || ''));
    await page.selectOption('#profileClothingSize', 'XL');
    await page.waitForFunction(() => /XL/.test((document.getElementById('profileSizesState') || {}).textContent || ''));

    // Имитация другого устройства: полностью очищаем localStorage и перезагружаем страницу
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.waitForFunction(() => document.getElementById('profileShoeSize').value === '43');
    assert.equal(await page.inputValue('#profileClothingSize'), 'XL');

    assert.deepEqual(errors, [], 'Unexpected page errors: ' + errors.join('; '));

    console.log('test_frontend_user_sizes PASS');
  } finally {
    if (browser) await browser.close();
    child.kill('SIGTERM');
  }
}

runTest().catch(err => {
  console.error(err);
  process.exit(1);
});
