// CA-Admin-Sellers: Браузерный тест редактора магазинов администратора: профиль (search_first.html) → /admin, реальный API v2.
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

    // 1. Гость: в профиле блок админки скрыт
    await page.goto(base + '/profile');
    await page.waitForSelector('#viewProfile.active');
    const guestAdminArea = await page.locator('#profileAdminShortcut').evaluate(el => getComputedStyle(el).display);
    assert.equal(guestAdminArea, 'none', 'Ссылка на панель администратора должна быть скрыта для гостя');

    // 2. Вход администратора через dev-login
    const loginRes = await page.evaluate(async () => {
      const r = await fetch('/api/auth/dev-login', {method: 'POST'});
      return {status: r.status, data: await r.json()};
    });
    assert.equal(loginRes.status, 200);
    assert.equal(loginRes.data.user.is_admin, true);

    // 3. Переход в профиль под администратором: отображается только аккуратная ссылка, тяжеловесного блока нет
    await page.goto(base + '/profile');
    await page.waitForSelector('#viewProfile.active');
    await page.waitForFunction(() => {
      const el = document.getElementById('profileAdminShortcut');
      return el && getComputedStyle(el).display !== 'none';
    });

    const adminAuthBadge = await page.locator('#profileAuthBadge').innerText();
    assert.match(adminAuthBadge, /Администратор/);

    const oldAreaExists = await page.locator('#profileAdminArea').count();
    assert.equal(oldAreaExists, 0, 'Тяжеловесный блок управления магазинами удалён из профиля пользователя');

    // 4. Переход в админку /admin: раздел «Магазины и источники» доступен администратору
    // Админка открывает разделы через навигацию: выбираем раздел так же, как администратор
    await page.goto(base + '/admin');
    await page.locator('.admin-nav a[href="#shopsTool"]').click();
    await page.waitForSelector('#shopsTool', { state: 'visible' });
    assert.ok(await page.locator('#shopsTool').isVisible());

    // 5. Новый магазин через окно «Добавить магазин» с двумя маркетплейсами (реальный API v2)
    await page.getByRole('button', {name: 'Добавить магазин'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open);
    await page.fill('#shopForm [name=name]', 'Тестовый Магазин Электроники');
    await page.fill('#shopForm [name=legal_name]', 'ТОО Тест Электроникс');
    await page.fill('#shopForm [name=city]', 'Алматы');
    await page.fill('#shopForm [name=bin]', '123456789012');
    await page.fill('#shopForm [name=website_url]', 'https://test-elec.kz');
    await page.fill('#shopForm [name=phone]', '+7 777 999 8877');
    await page.fill('#shopForm [name=instagram]', '@testelec_kz');
    await page.fill('#shopForm [name=telegram]', '@testelec_tg');
    for (const link of ['https://kaspi.kz/shop/m/12345/products/', 'https://forte.kz/merchant/67890']) {
      await page.getByRole('button', {name: 'Добавить ссылку'}).click();
      await page.locator('#shopForm input[name=marketplace_link]').last().fill(link);
    }
    assert.equal(await page.locator('#shopForm input[name=marketplace_link]').count(), 2);
    await page.locator('#shopForm').getByRole('button', {name: 'Сохранить'}).click();
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);

    // 6. Новый магазин появился в списке
    const shopRow = page.locator('#shopsList .user').filter({hasText: 'Тестовый Магазин Электроники'});
    await shopRow.waitFor();
    assert.match(await shopRow.innerText(), /Алматы/);
    assert.doesNotMatch(await shopRow.innerText(), /Отключён/);

    // 7. Правка: сохранённые реквизиты и ссылки возвращаются в форму, каналы сбора показаны
    await shopRow.getByRole('button', {name: 'Изменить'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open
      && document.querySelector('#shopForm [name=name]').value === 'Тестовый Магазин Электроники');
    assert.equal(await page.inputValue('#shopForm [name=legal_name]'), 'ТОО Тест Электроникс');
    assert.equal(await page.inputValue('#shopForm [name=bin]'), '123456789012');
    assert.equal(await page.inputValue('#shopForm [name=website_url]'), 'https://test-elec.kz');
    const links = await page.locator('#shopForm input[name=marketplace_link]').evaluateAll(els => els.map(e => e.value));
    assert.ok(links.some(l => l.includes('kaspi.kz')) && links.some(l => l.includes('forte.kz')), JSON.stringify(links));
    await page.waitForSelector('#shopChannelsSection', {state: 'visible'});
    const channelsText = await page.locator('#shopChannelsList').innerText();
    assert.match(channelsText, /kaspi/i);
    assert.match(channelsText, /forte/i);

    await page.fill('#shopForm [name=legal_name]', 'ТОО Тест Электроникс Обновлённый');
    await page.locator('#shopForm').getByRole('button', {name: 'Сохранить'}).click();
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);
    await shopRow.getByRole('button', {name: 'Изменить'}).click();
    await page.waitForFunction(() => document.getElementById('shopDialog').open
      && document.querySelector('#shopForm [name=legal_name]').value === 'ТОО Тест Электроникс Обновлённый');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.getElementById('shopDialog').open);

    // 8. Поиск по названию
    await page.fill('#shopSearch', 'НесуществующийXYZМагазин');
    await page.waitForFunction(() => document.getElementById('shopsState').textContent.includes('Магазины не найдены'));
    await page.fill('#shopSearch', 'Тестовый');
    await shopRow.waitFor();

    // 9. Тёмная тема админки сохраняет читаемый список
    await page.emulateMedia({colorScheme: 'dark'});
    await page.evaluate(() => { localStorage.removeItem('kz_theme_mode'); localStorage.removeItem('kz_theme'); });
    await page.reload();
    await page.locator('.admin-nav a[href="#shopsTool"]').click();
    await page.waitForSelector('#shopsTool', {state: 'visible'});
    assert.equal(await page.evaluate(() => document.documentElement.getAttribute('data-theme')), 'dark');
    await page.locator('#shopsList .user').first().waitFor();

    assert.equal(errors.length, 0, 'Ошибки JS в консоли: ' + errors.join('; '));
    console.log('test_frontend_admin_sellers PASS');
  } finally {
    if (browser) await browser.close();
    child.kill('SIGKILL');
  }
}

runTest().catch(err => {
  console.error(err);
  process.exit(1);
});
