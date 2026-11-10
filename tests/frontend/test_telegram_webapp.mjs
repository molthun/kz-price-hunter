// Frontend test: Telegram Mini App integration (TG-10)
// Tests: tg.ready, tg.expand, closing confirmation, BackButton lifecycle,
// HapticFeedback calls, theme synchronization, and shareMessage workflow.
import { chromium } from "npm:playwright";
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert/strict";

const ROOT = path.resolve(import.meta.dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'web/templates/search_first.html'), 'utf8');
const BASE = 'http://127.0.0.1:18099';

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 }, // iPhone 13/14 size
    colorScheme: 'dark'
  });

  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', m => {
    if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) {
      errors.push(m.text());
    }
  });

  // Inject Telegram WebApp mock before scripts run
  await page.addInitScript(() => {
    const backBtnListeners = [];
    const eventListeners = {};
    window.__tgMock = {
      readyCalled: false,
      expandCalled: false,
      closingConfirmationCalled: false,
      headerColors: [],
      bgColors: [],
      haptics: [],
      backBtnVisible: false,
      sharedIds: []
    };

    window.Telegram = {
      WebApp: {
        initData: 'query_id=test_qid&user=%7B%22id%22%3A999%2C%22first_name%22%3A%22Shopper%22%7D',
        initDataUnsafe: {
          user: { id: 999, first_name: 'Shopper', username: 'shopper999' }
        },
        colorScheme: 'dark',
        themeParams: {
          bg_color: '#0b1120',
          text_color: '#f8fafc'
        },
        ready: () => { window.__tgMock.readyCalled = true; },
        expand: () => { window.__tgMock.expandCalled = true; },
        enableClosingConfirmation: () => { window.__tgMock.closingConfirmationCalled = true; },
        setHeaderColor: (c) => { window.__tgMock.headerColors.push(c); },
        setBackgroundColor: (c) => { window.__tgMock.bgColors.push(c); },
        setBottomBarColor: () => {},
        BackButton: {
          isVisible: false,
          show: () => {
            window.Telegram.WebApp.BackButton.isVisible = true;
            window.__tgMock.backBtnVisible = true;
          },
          hide: () => {
            window.Telegram.WebApp.BackButton.isVisible = false;
            window.__tgMock.backBtnVisible = false;
          },
          onClick: (cb) => { backBtnListeners.push(cb); },
          click: () => { backBtnListeners.forEach(cb => cb()); }
        },
        HapticFeedback: {
          impactOccurred: (style) => { window.__tgMock.haptics.push({ type: 'impact', style }); },
          notificationOccurred: (type) => { window.__tgMock.haptics.push({ type: 'notification', notifType: type }); },
          selectionChanged: () => { window.__tgMock.haptics.push({ type: 'selection' }); }
        },
        shareMessage: (msgId, cb) => {
          window.__tgMock.sharedIds.push(msgId);
          if (cb) cb(true);
        },
        onEvent: (evt, cb) => {
          if (!eventListeners[evt]) eventListeners[evt] = [];
          eventListeners[evt].push(cb);
        },
        emitEvent: (evt) => {
          if (eventListeners[evt]) eventListeners[evt].forEach(cb => cb());
        }
      }
    };
  });

  // Mock network requests
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== BASE) return route.abort();

    if (url.pathname === '/') {
      return route.fulfill({ status: 200, contentType: 'text/html', body: html });
    }
    if (url.pathname === '/api/me') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          maintenance_mode: false,
          user: { id: 999, first_name: 'Shopper', username: 'shopper999', is_admin: false, is_confirmed: true },
          auth: { bot_username: 'kzph_test_bot' }
        })
      });
    }
    if (url.pathname === '/api/v2/search/facets') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ categories: [], sellers: [], brands: [], cities: [] })
      });
    }
    if (url.pathname.startsWith('/api/v2/products/')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          product: { id: 'prod_test', title: 'Флагманский телефон' },
          best_price: 350000,
          offers: [{ id: 'off_1', seller_name: 'Белый Ветер', price: 350000, condition: 'NEW' }]
        })
      });
    }
    if (url.pathname === '/api/telegram/prepared-message') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', prepared_message_id: 'prep_mock_test_123' })
      });
    }
    if (url.pathname === '/api/me/favorites') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ favorites: [] }) });
    }
    if (url.pathname === '/api/me/watches') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ watches: [] }) });
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.goto(BASE + '/');
  await page.waitForFunction(() => typeof TelegramApp !== 'undefined' && TelegramApp.isInside);

  // 1. Verify TG WebApp Lifecycle methods called
  const mockState = await page.evaluate(() => window.__tgMock);
  assert.equal(mockState.readyCalled, true, 'tg.ready() should be called');
  assert.equal(mockState.expandCalled, true, 'tg.expand() should be called');
  assert.equal(mockState.closingConfirmationCalled, true, 'tg.enableClosingConfirmation() should be called');

  // 2. Verify Theme synchronization
  const currentTheme = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  assert.equal(currentTheme, 'dark', 'Dark theme from Telegram should be applied');

  // 3. Verify BackButton lifecycle
  // On home view (/), back button should be hidden
  let backBtnVisible = await page.evaluate(() => window.__tgMock.backBtnVisible);
  assert.equal(backBtnVisible, false, 'BackButton must be hidden on home page');

  // Open mobile filters -> back button should show
  await page.evaluate(() => openMobileFilters());
  backBtnVisible = await page.evaluate(() => window.__tgMock.backBtnVisible);
  assert.equal(backBtnVisible, true, 'BackButton must be visible when modal/filters open');

  // Click native BackButton -> should close filters and hide back button
  await page.evaluate(() => window.Telegram.WebApp.BackButton.click());
  const filtersOpen = await page.evaluate(() => isFiltersDialogOpen());
  assert.equal(filtersOpen, false, 'BackButton click must close filters modal');
  backBtnVisible = await page.evaluate(() => window.__tgMock.backBtnVisible);
  assert.equal(backBtnVisible, false, 'BackButton must hide after closing modal on home');

  // Navigate to product detail -> back button should show
  await page.evaluate(() => navigateTo(null, '/product/prod_test'));
  await page.waitForFunction(() => document.getElementById('viewDetail').classList.contains('active'));
  backBtnVisible = await page.evaluate(() => window.__tgMock.backBtnVisible);
  assert.equal(backBtnVisible, true, 'BackButton must be visible on product detail view');

  // Click native BackButton on product detail -> should navigate back to home
  await page.evaluate(() => window.Telegram.WebApp.BackButton.click());
  await page.waitForFunction(() => document.getElementById('viewHome').classList.contains('active'));
  backBtnVisible = await page.evaluate(() => window.__tgMock.backBtnVisible);
  assert.equal(backBtnVisible, false, 'BackButton must hide when returning to home view');

  // 4. Verify HapticFeedback integration
  // Trigger tab switch
  await page.evaluate(() => updateNavState('deals'));
  let haptics = await page.evaluate(() => window.__tgMock.haptics);
  assert.ok(haptics.some(h => h.type === 'selection'), 'Haptic selectionChanged must fire on tab change');

  // Trigger favorite toggle
  await page.evaluate(() => {
    Favorites.toggle({ id: 'p_fav_1', title: 'Тестовый товар', new_price: 10000 });
  });
  haptics = await page.evaluate(() => window.__tgMock.haptics);
  assert.ok(haptics.some(h => h.type === 'notification' && h.notifType === 'success'), 'Haptic success must fire on favorite add');

  // 5. Verify Telegram shareMessage integration via prepared-message endpoint
  await page.evaluate(async () => {
    await shareDeal('Супер скидка', 'https://shdev.molthun.ru/product/prod_test', {
      title: 'Супер скидка',
      current_price: 350000,
      shop: 'Белый Ветер'
    });
  });
  const sharedIds = await page.evaluate(() => window.__tgMock.sharedIds);
  assert.equal(sharedIds.length, 1, 'shareDeal in WebApp should call tg.shareMessage');
  assert.equal(sharedIds[0], 'prep_mock_test_123', 'tg.shareMessage should receive prepared_message_id');

  assert.equal(errors.length, 0, 'No console/page errors expected: ' + errors.join('; '));
  console.log('✓ Telegram Mini App (TG-10) Playwright verification passed successfully!');
  await browser.close();
})().catch(err => {
  console.error('FAIL:', err);
  process.exit(1);
});
