// CA-Admin-Sellers: Браузерный тест редактора магазинов для администратора (search_first.html + API v2).
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
    const guestAdminArea = await page.locator('#profileAdminArea').evaluate(el => getComputedStyle(el).display);
    assert.equal(guestAdminArea, 'none', 'Блок администратора должен быть скрыт для гостя');

    // 2. Вход администратора через dev-login
    const loginRes = await page.evaluate(async () => {
      const r = await fetch('/api/auth/dev-login', {method: 'POST'});
      return {status: r.status, data: await r.json()};
    });
    assert.equal(loginRes.status, 200);
    assert.equal(loginRes.data.user.is_admin, true);

    // 3. Переход в профиль под администратором
    await page.goto(base + '/profile');
    await page.waitForSelector('#viewProfile.active');
    await page.waitForFunction(() => {
      const el = document.getElementById('profileAdminArea');
      return el && getComputedStyle(el).display !== 'none';
    });

    const adminAuthBadge = await page.locator('#profileAuthBadge').innerText();
    assert.match(adminAuthBadge, /Администратор/);

    // 4. Открытие модального окна добавления нового магазина
    await page.locator('#profileAdminArea button:has-text("+ Новый магазин")').click();
    await page.waitForSelector('#modalAdminSeller.open');
    assert.match(await page.locator('#adminSellerModalTitle').innerText(), /Новый магазин/);

    // 5. Заполнение формы магазина с несколькими маркетплейсами
    await page.locator('#adminSellerName').fill('Тестовый Магазин Электроники');
    await page.locator('#adminSellerLegalName').fill('ТОО Тест Электроникс');
    await page.locator('#adminSellerCity').fill('Алматы');
    await page.locator('#adminSellerBin').fill('123456789012');
    await page.locator('#adminSellerWebsite').fill('https://test-elec.kz');
    await page.locator('#adminSellerPhone').fill('+7 777 999 8877');
    await page.locator('#adminSellerInstagram').fill('@testelec_kz');
    await page.locator('#adminSellerTelegram').fill('@testelec_tg');

    // Ссылки на маркетплейсы: 1-я строка уже есть, заполняем её
    const firstMpInput = page.locator('.admin-mp-input').first();
    await firstMpInput.fill('https://kaspi.kz/shop/m/12345/products/');

    // Добавляем 2-й маркетплейс по кнопке "+ Добавить маркетплейс"
    await page.locator('button:has-text("+ Добавить маркетплейс")').click();
    assert.equal(await page.locator('.admin-mp-input').count(), 2);
    await page.locator('.admin-mp-input').nth(1).fill('https://forte.kz/merchant/67890');

    // Сохранение магазина
    await page.locator('#btnSaveAdminSeller').click();
    await page.waitForFunction(() => !document.getElementById('modalAdminSeller').classList.contains('open'));

    // 6. Проверка, что новый магазин появился в списке
    await page.waitForFunction(() => {
      const list = document.getElementById('adminSellersList');
      return list && list.textContent.includes('Тестовый Магазин Электроники');
    });

    const shopCard = page.locator('.admin-shop-card').filter({hasText: 'Тестовый Магазин Электроники'});
    assert.match(await shopCard.innerText(), /Алматы/);
    assert.match(await shopCard.innerText(), /ТОО Тест Электроникс/);
    assert.match(await shopCard.innerText(), /123456789012/);
    assert.match(await shopCard.innerText(), /Активен/);

    // 7. Открытие магазина на редактирование и проверка каналов сбора
    await shopCard.locator('button:has-text("Изменить")').click();
    await page.waitForSelector('#modalAdminSeller.open');
    await page.waitForFunction(() => document.getElementById('adminSellerName').value === 'Тестовый Магазин Электроники');

    // Проверяем статус каналов в карточке
    await page.waitForSelector('#adminSellerChannelsBlock:not([style*="display: none"])');
    const channelsText = await page.locator('#adminSellerChannelsList').innerText();
    assert.match(channelsText, /Нужен сканер витрины/);
    assert.match(channelsText, /В сборе/);

    // Редактируем юридическое название
    await page.locator('#adminSellerLegalName').fill('ТОО Тест Электроникс Обновлённый');
    await page.locator('#btnSaveAdminSeller').click();
    await page.waitForFunction(() => !document.getElementById('modalAdminSeller').classList.contains('open'));

    // Проверяем обновление в списке
    await page.waitForFunction(() => {
      const list = document.getElementById('adminSellersList');
      return list && list.textContent.includes('ТОО Тест Электроникс Обновлённый');
    });

    // 8. Поиск по названию
    await page.locator('#adminSellerSearchInput').fill('НесуществующийXYZМагазин');
    await page.waitForFunction(() => {
      const list = document.getElementById('adminSellersList');
      return list && list.textContent.includes('Магазины не найдены');
    });

    await page.locator('#adminSellerSearchInput').fill('Тестовый');
    await page.waitForFunction(() => {
      const list = document.getElementById('adminSellersList');
      return list && list.textContent.includes('Тестовый Магазин Электроники');
    });

    // 9. Переключение на вкладку Заявок
    await page.locator('#tabBtnAdminOnboarding').click();
    assert.ok(await page.locator('#tabBtnAdminOnboarding').evaluate(el => el.classList.contains('active')));
    const onboardingDisplay = await page.locator('#adminTabOnboardingContent').evaluate(el => getComputedStyle(el).display);
    assert.notEqual(onboardingDisplay, 'none');

    // 10. Переключение темы (тёмная / светлая)
    await page.evaluate(() => toggleTheme());
    const darkTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
    assert.equal(darkTheme, 'dark');

    await page.evaluate(() => toggleTheme());
    const lightTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
    assert.equal(lightTheme, 'light');

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
