// S2-E19: Frontend test for Seller Page Showcase Modal
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..', '..');
const assert = require('node:assert/strict');

const mockSellerData = {
  ok: true,
  seller: {
    id: 'seller_4mobile',
    slug: '4mobile',
    name: '4mobile',
    legal_name: 'ТОО 4mobile Казахстан',
    rating: 4.8,
    domain: '4mobile.kz',
    phone: '+7 701 000 0000',
  },
  channels: [
    { id: 'ch_web', channel_type: 'website', name: 'Официальный сайт', base_url: 'https://4mobile.kz' },
    { id: 'ch_kaspi', channel_type: 'kaspi', name: 'Kaspi Магазин', base_url: 'https://kaspi.kz/shop/4mobile' },
  ],
  stats: {
    total_offers: 5,
    products_count: 2,
    min_price: 380000,
    max_price: 470000,
    conditions_breakdown: { NEW: 4, USED: 1 },
    available_cities: ['Астана', 'Алматы'],
    available_categories: ['smartphones'],
  },
  products: [
    {
      product_id: 'prod_ip15',
      title: 'Apple iPhone 15 128GB',
      category: 'smartphones',
      brand: 'Apple',
      min_price: 440000,
      max_price: 470000,
      channels: [
        { channel_name: 'Официальный сайт', channel_type: 'website', price: 440000, condition: 'NEW', url: 'https://4mobile.kz/ip15' },
        { channel_name: 'Kaspi Магазин', channel_type: 'kaspi', price: 470000, condition: 'NEW', url: 'https://kaspi.kz/p/ip15' },
      ],
    },
  ],
};

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const html = fs.readFileSync(path.join(ROOT, 'web/templates/index.html'), 'utf8');
  try {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 }, locale: 'en-US' });
    await page.addInitScript(() => { window.tailwind = { config: {} }; });
    page.on('dialog', d => d.accept());

    await page.route('**/*', async route => {
      const u = new URL(route.request().url());
      if (u.hostname === 'cdn.tailwindcss.com') {
        return route.fulfill({ contentType: 'application/javascript', body: 'window.tailwind=window.tailwind||{config:{}};' });
      }
      if (u.hostname !== '127.0.0.1') return route.abort();
      if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (u.pathname.startsWith('/static/')) {
        return route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(path.join(ROOT, 'web', u.pathname), 'utf8') });
      }
      if (u.pathname === '/api/v2/seller/4mobile') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify(mockSellerData) });
      }
      return route.fulfill({ contentType: 'application/json', body: '{}' });
    });

    await page.goto('http://127.0.0.1:18099/');
    await page.waitForFunction(() => typeof openSellerModal === 'function' && typeof closeSellerModal === 'function');

    // Check modal starts hidden
    const isHiddenBefore = await page.evaluate(() => {
      const el = document.getElementById('modalSellerShowcase');
      return el && el.classList.contains('hidden');
    });
    assert.equal(isHiddenBefore, true, 'модалка продавца изначально скрыта');

    // Open seller modal
    await page.evaluate(() => openSellerModal('4mobile'));
    await page.waitForFunction(() => {
      const el = document.getElementById('modalSellerName');
      return el && el.textContent.includes('4mobile');
    });

    // Check rendered content
    const modalContent = await page.evaluate(() => {
      const modal = document.getElementById('modalSellerShowcase');
      const name = document.getElementById('modalSellerName').textContent;
      const rating = document.getElementById('modalSellerRating').textContent;
      const body = document.getElementById('modalSellerBody').innerHTML;
      const isVisible = !modal.classList.contains('hidden');
      return { name, rating, body, isVisible };
    });

    assert.equal(modalContent.isVisible, true, 'модалка открыта');
    assert.equal(modalContent.name, '4mobile');
    assert.match(modalContent.rating, /4\.8/);
    assert.match(modalContent.body, /Официальный сайт/);
    assert.match(modalContent.body, /Kaspi Магазин/);
    assert.match(modalContent.body, /Apple iPhone 15/);
    assert.match(modalContent.body, /440\s*000/);

    // Close modal via closeSellerModal
    await page.evaluate(() => closeSellerModal());
    const isHiddenAfter = await page.evaluate(() => {
      return document.getElementById('modalSellerShowcase').classList.contains('hidden');
    });
    assert.equal(isHiddenAfter, true, 'модалка закрывается через closeSellerModal');

    // Reopen and test Escape key closes it
    await page.evaluate(() => openSellerModal('4mobile'));
    await page.keyboard.press('Escape');
    const isHiddenEscape = await page.evaluate(() => {
      return document.getElementById('modalSellerShowcase').classList.contains('hidden');
    });
    assert.equal(isHiddenEscape, true, 'нажатие Escape закрывает модалку продавца');

    console.log('OK: Витрина продавца (Seller Modal) успешно проверена');
  } finally {
    await browser.close();
  }
})();
