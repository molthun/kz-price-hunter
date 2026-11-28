const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const htmlPath = path.join(__dirname, '..', '..', 'web', 'templates', 'admin.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function extractFn(src, name) {
  const re = new RegExp(`((?:async\\s+)?function\\s+${name}\\s*\\()`);
  const m = re.exec(src);
  assert.ok(m, `Function ${name} not found in admin.html`);
  const start = m.index;
  let i = src.indexOf('{', start);
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error(`Unbalanced braces for ${name}`);
}

const code = [
  'let matchingOffset = 0; let matchingTotal = 0; let matchingSerial = 0; let matchingCatalogSerial = 0; const MATCHING_LIMIT = 20; let matchingPickedA = { product_id: "", title: "" }; let matchingPickedB = { product_id: "", title: "" };',
  extractFn(html, 'matchingSafeUrl'),
  extractFn(html, 'matchingFormatPrice'),
  extractFn(html, 'matchingRenderDiffTitle'),
  extractFn(html, 'matchingDefaultPrimarySide'),
  extractFn(html, 'matchingBuildSide'),
  extractFn(html, 'renderMatchingCard'),
  extractFn(html, 'loadAdminMatchingWebHelp'),
  extractFn(html, 'submitAdminMatchingVerdict'),
  extractFn(html, 'clearStalePickedCard'),
  extractFn(html, 'pickCatalogCardForEnqueue'),
  extractFn(html, 'searchAdminMatchingCatalog'),
  extractFn(html, 'enqueueAdminMatchingPair'),
  extractFn(html, 'loadMatchingPairs'),
].join('\n\n');

function makeEl(tagOrId) {
  return {
    tagName: String(tagOrId || '').toUpperCase(),
    id: '',
    className: '',
    textContent: '',
    value: '',
    href: '',
    src: '',
    alt: '',
    type: '',
    hidden: false,
    disabled: false,
    style: {},
    attrs: {},
    children: [],
    onclick: null,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    replaceChildren() { this.children = []; this.textContent = ''; },
    append(...nodes) { this.children.push(...nodes); },
    appendChild(node) { this.children.push(node); return node; },
  };
}

function findAll(root, pred, out = []) {
  if (!root || typeof root !== 'object') return out;
  if (pred(root)) out.push(root);
  for (const c of root.children || []) findAll(c, pred, out);
  return out;
}

function createHarness(fetchImpl) {
  const els = {
    matchingStatusFilter: Object.assign(makeEl('select'), { value: 'pending' }),
    matchingSearchInput: Object.assign(makeEl('input'), { value: '' }),
    matchingState: makeEl('div'),
    matchingPairsList: makeEl('div'),
    matchingStatPending: makeEl('span'),
    matchingStatSame: makeEl('span'),
    matchingStatDiff: makeEl('span'),
    matchingStatTotal: makeEl('span'),
    matchingSummaryBadge: makeEl('span'),
    matchingPrevBtn: makeEl('button'),
    matchingNextBtn: makeEl('button'),
    matchingEnqueueA: makeEl('input'),
    matchingEnqueueB: makeEl('input'),
    matchingEnqueueReason: makeEl('input'),
    matchingEnqueueState: makeEl('div'),
    matchingCatalogSearchInput: makeEl('input'),
    matchingCatalogSearchState: makeEl('div'),
    matchingCatalogSearchResults: makeEl('div'),
  };
  const ctx = {
    URLSearchParams,
    console,
    fetch: fetchImpl,
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl(tag),
      createTextNode: (txt) => ({ nodeType: 3, textContent: String(txt) }),
    },
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els };
}

async function runTests() {
  let passed = 0;

  // 1. Side-by-side card renders photo, thumbs, description, attributes, sellers/links, diff marks, and fallback when photo/desc missing
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_1',
      status: 'pending',
      reason: 'diff_color',
      title_a: 'Apple iPhone 15 128GB Black',
      title_b: 'Apple iPhone 15 128GB Blue',
      key_a: 'k_a',
      key_b: 'k_b',
      same_key: false,
      card_a: {
        image_url: 'https://cdn.example/a.jpg',
        images: ['https://cdn.example/a.jpg', 'https://cdn.example/a2.jpg'],
        category: 'phones',
        brand: 'Apple',
        model: 'iPhone 15',
        description: 'OLED 6.1 дюйма, 128 ГБ',
        attributes: { storage: '128GB', color: 'Black' },
        offers: [{ shop: 'Technodom', city: 'Алматы', current_price: 389990, url: 'https://technodom.kz/p/1' }],
      },
      card_b: {
        image_url: '',
        images: [],
        description: '',
        attributes: {},
        offers: [],
      },
    });
    const imgs = findAll(card, (n) => n.tagName === 'IMG');
    assert.equal(imgs.length, 2, 'Primary image + 1 extra thumbnail');
    assert.equal(imgs[0].src, 'https://cdn.example/a.jpg');
    assert.equal(imgs[1].src, 'https://cdn.example/a2.jpg');

    const noPhotos = findAll(card, (n) => n.className === 'matching-no-photo');
    assert.equal(noPhotos.length, 1);

    const descs = findAll(card, (n) => String(n.className || '').startsWith('matching-desc'));
    assert.equal(descs[0].textContent, 'OLED 6.1 дюйма, 128 ГБ');
    assert.ok(descs[1].textContent.includes('не указано'));

    const marks = findAll(card, (n) => n.tagName === 'MARK' && n.className === 'matching-diff');
    assert.ok(marks.some((m) => m.textContent === 'Black'));
    assert.ok(marks.some((m) => m.textContent === 'Blue'));

    const offerLinks = findAll(card, (n) => n.tagName === 'A' && n.href === 'https://technodom.kz/p/1');
    assert.equal(offerLinks.length, 1);
    passed++;
  }

  // 2. Clicking "Склеить" sends POST /api/admin/matching/verdict with exact pair_hash, decision, notes and reloads
  {
    const calls = [];
    const h = createHarness(async (url, opts) => {
      calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
      if (url.startsWith('/api/admin/matching/stats')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', stats: { pending: 0, human_same: 1, human_different: 0, total: 1 } }) };
      }
      if (url.startsWith('/api/admin/matching/verdict')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', ok: true }) };
      }
      return { ok: true, status: 200, json: async () => ({ items: [], total: 0 }) };
    });
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_merge',
      status: 'pending',
      title_a: 'Товар A',
      title_b: 'Товар B',
      card_a: {},
      card_b: {},
    });
    const noteInput = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-note') === 'ph_merge')[0];
    noteInput.value = 'Одинаковая модель';
    const sameBtn = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-action') === 'same')[0];
    await sameBtn.onclick();

    const postCall = calls.find((c) => c.url === '/api/admin/matching/verdict');
    assert.ok(postCall);
    assert.equal(postCall.method, 'POST');
    assert.deepEqual(postCall.body, { pair_hash: 'ph_merge', decision: 'same', notes: 'Одинаковая модель', primary_side: 'a' });
    assert.ok(calls.some((c) => c.url.startsWith('/api/admin/matching/disputed?')));
    passed++;
  }

  // 3. Server HTTP 400 with conflicts shows inline error message inside card without alert()
  {
    const h = createHarness(async () => ({
      ok: false,
      status: 400,
      json: async () => ({ status: 'error', message: 'Конфликт групп', conflicts: ['Товар X', 'Товар Y'] }),
    }));
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_conflict',
      status: 'pending',
      title_a: 'Товар A',
      title_b: 'Товар B',
      card_a: {},
      card_b: {},
    });
    const sameBtn = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-action') === 'same')[0];
    await sameBtn.onclick();
    const errEl = findAll(card, (n) => n.className === 'state error')[0];
    assert.ok(errEl, 'Error must be rendered inline');
    assert.ok(errEl.textContent.includes('Конфликт групп'));
    assert.ok(errEl.textContent.includes('Товар X'));
    assert.equal(sameBtn.disabled, false, 'Button must be re-enabled after error');
    passed++;
  }

  // 4. Manual enqueue sends POST /api/admin/matching/enqueue and clears form
  {
    const calls = [];
    const h = createHarness(async (url, opts) => {
      calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
      if (url.startsWith('/api/admin/matching/stats')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', stats: { pending: 1, human_same: 0, human_different: 0, total: 1 } }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: 'ok', items: [], total: 0 }) };
    });
    h.els.matchingEnqueueA.value = 'Ноутбук Asus A';
    h.els.matchingEnqueueB.value = 'Asus A Ноутбук';
    h.els.matchingEnqueueReason.value = 'дубль';
    const ok = await h.ctx.enqueueAdminMatchingPair();
    assert.equal(ok, true);
    const enq = calls.find((c) => c.url === '/api/admin/matching/enqueue');
    assert.deepEqual(enq.body, { title_a: 'Ноутбук Asus A', title_b: 'Asus A Ноутбук', reason: 'дубль' });
    assert.equal(h.els.matchingEnqueueA.value, '');
    passed++;
  }

  // 5. Stale fetch response A does not overwrite newer response B
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      if (url.startsWith('/api/admin/matching/stats')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', stats: { pending: 1, human_same: 0, human_different: 0, total: 1 } }) };
      }
      if (url.includes('search=First')) {
        await gateA;
        return { ok: true, status: 200, json: async () => ({ items: [{ pair_hash: 'old_a', status: 'pending', title_a: 'Old A', title_b: 'Old B', card_a: {}, card_b: {} }], total: 1 }) };
      }
      return { ok: true, status: 200, json: async () => ({ items: [{ pair_hash: 'new_b', status: 'pending', title_a: 'New A', title_b: 'New B', card_a: {}, card_b: {} }], total: 1 }) };
    });
    h.els.matchingSearchInput.value = 'First';
    const pA = h.ctx.loadMatchingPairs();
    h.els.matchingSearchInput.value = 'Second';
    await h.ctx.loadMatchingPairs();
    resolveA();
    await pA;
    const cards = findAll(h.els.matchingPairsList, (n) => n.getAttribute && n.getAttribute('data-matching-pair'));
    assert.equal(cards.length, 1);
    assert.equal(cards[0].getAttribute('data-matching-pair'), 'new_b');
    passed++;
  }

  // 6. Unsafe URLs (javascript:) are blocked from images and offer links
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_xss',
      status: 'pending',
      title_a: '<b>Bad</b>',
      title_b: 'Safe',
      card_a: {
        image_url: 'javascript:alert(1)',
        offers: [{ shop: 'Shop', current_price: 1000, url: 'javascript:evil()' }],
      },
      card_b: {},
    });
    assert.equal(findAll(card, (n) => n.tagName === 'IMG').length, 0);
    assert.equal(findAll(card, (n) => n.tagName === 'A').length, 0);
    passed++;
  }

  // 7. MM-04: ambiguous_title / key_not_found renders explicit warning and hides unconfirmed photo/offers
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_ambig',
      status: 'pending',
      title_a: 'Samsung Galaxy S24',
      title_b: 'Смартфон Samsung S24',
      key_a: '',
      key_b: 'samsung_s24_128',
      offers_a: [{ shop: 'WrongShop', current_price: 500000, url: 'https://wrong.kz/1', image_url: 'https://wrong.kz/img.jpg' }],
      card_a: {
        match_status: 'ambiguous_title',
        ambiguous_count: 2,
        image_url: 'https://wrong.kz/img.jpg',
        description: 'Wrong description',
        attributes: { storage: '256GB' },
        offers: [{ shop: 'WrongShop', current_price: 500000, url: 'https://wrong.kz/1' }],
      },
      card_b: {
        match_status: 'canonical_key',
        image_url: 'https://right.kz/img.jpg',
        description: 'Right description',
        attributes: { storage: '128GB' },
        offers: [{ shop: 'RightShop', current_price: 380000, url: 'https://right.kz/1' }],
      },
    });
    const warnings = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-ambiguous') === 'ambiguous_title');
    assert.equal(warnings.length, 1);
    assert.ok(warnings[0].textContent.includes('Неоднозначная привязка'));
    const imgs = findAll(card, (n) => n.tagName === 'IMG');
    assert.equal(imgs.length, 1, 'Only confirmed card_b image must render');
    assert.equal(imgs[0].src, 'https://right.kz/img.jpg');
    const links = findAll(card, (n) => n.tagName === 'A' && n.href === 'https://wrong.kz/1');
    assert.equal(links.length, 0, 'Unconfirmed offers must not render');
    passed++;
  }

  // 8. v6.70.0: primary card selector switches primary_side to 'b', highlights active side, and omits primary_side for 'different'
  {
    const calls = [];
    const h = createHarness(async (url, opts) => {
      calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
      if (url.startsWith('/api/admin/matching/stats')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', stats: { pending: 0, human_same: 1, human_different: 0, total: 1 } }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: 'ok', ok: true, items: [], total: 0 }) };
    });
    const card = h.ctx.renderMatchingCard({
      pair_hash: 'ph_prim',
      status: 'pending',
      same_key: false,
      title_a: 'Карточка 1',
      title_b: 'Карточка 2',
      key_a: 'k_a',
      key_b: 'k_b',
      card_a: { match_status: 'canonical_key', image_url: 'https://a.test/1.jpg', offers: [{ shop: 'A', current_price: 100 }] },
      card_b: { match_status: 'canonical_key', image_url: 'https://b.test/1.jpg', offers: [{ shop: 'B', current_price: 100 }] },
    });
    const radioB = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-primary') === 'b')[0];
    const sideA = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-side') === 'a')[0];
    const sideB = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-side') === 'b')[0];
    assert.ok(radioB);
    assert.ok(sideA.className.includes('is-primary'));
    assert.ok(!sideB.className.includes('is-primary'));

    radioB.checked = true;
    radioB.onchange();
    assert.ok(!sideA.className.includes('is-primary'));
    assert.ok(sideB.className.includes('is-primary'));

    const sameBtn = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-action') === 'same')[0];
    await sameBtn.onclick();
    const sameCall = calls.find((c) => c.url === '/api/admin/matching/verdict' && c.body.decision === 'same');
    assert.equal(sameCall.body.primary_side, 'b');

    const diffBtn = findAll(card, (n) => n.getAttribute && n.getAttribute('data-matching-action') === 'different')[0];
    await diffBtn.onclick();
    const diffCall = calls.find((c) => c.url === '/api/admin/matching/verdict' && c.body.decision === 'different');
    assert.equal('primary_side' in diffCall.body, false);
    passed++;
  }

  // 9. v6.71.0: catalog search picker renders cards, fills Товар 1 / Товар 2, and blocks identical titles
  {
    const h = createHarness(async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        status: 'ok',
        items: [
          {
            product_id: 'p1', title: 'Кондиционер LG P09', canonical_key: 'k2:lg:p09',
            category: 'air_conditioners', image_url: 'https://cdn.test/lg1.jpg',
            offers_count: 2, min_price: 280000, shops: ['Technodom'],
          },
          {
            product_id: 'p2', title: 'Сплит LG P09 Dualcool', canonical_key: 'k2:lg:p09_alt',
            category: 'air_conditioners', image_url: 'javascript:evil()',
            offers_count: 1, min_price: 275000, shops: ['Mechta'],
          },
        ],
      }),
    }));
    h.els.matchingCatalogSearchInput.value = 'lg p09';
    await h.ctx.searchAdminMatchingCatalog();

    const rows = findAll(h.els.matchingCatalogSearchResults, (n) => n.getAttribute && n.getAttribute('data-picker-product'));
    assert.equal(rows.length, 2);
    const imgs = findAll(h.els.matchingCatalogSearchResults, (n) => n.tagName === 'IMG');
    assert.equal(imgs.length, 1, 'Unsafe javascript: image URL must be blocked');
    assert.equal(imgs[0].src, 'https://cdn.test/lg1.jpg');

    const pickA1 = findAll(rows[0], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'a')[0];
    const pickB1 = findAll(rows[0], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'b')[0];
    const pickB2 = findAll(rows[1], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'b')[0];

    assert.equal(pickA1.onclick(), true);
    assert.equal(h.els.matchingEnqueueA.value, 'Кондиционер LG P09');
    assert.equal(pickB1.onclick(), false, 'Must refuse picking exact same title for both sides');
    assert.equal(h.els.matchingEnqueueB.value, '');
    assert.equal(pickB2.onclick(), true);
    assert.equal(h.els.matchingEnqueueB.value, 'Сплит LG P09 Dualcool');
    passed++;
  }

  // 10. v6.71.0: stale catalog picker response A does not overwrite newer search B
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      if (url.includes('q=First')) {
        await gateA;
        return { ok: true, status: 200, json: async () => ({ status: 'ok', items: [{ product_id: 'old_p', title: 'Old' }] }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: 'ok', items: [{ product_id: 'new_p', title: 'New' }] }) };
    });
    h.els.matchingCatalogSearchInput.value = 'First';
    const pA = h.ctx.searchAdminMatchingCatalog();
    h.els.matchingCatalogSearchInput.value = 'Second';
    await h.ctx.searchAdminMatchingCatalog();
    resolveA();
    await pA;
    const rows = findAll(h.els.matchingCatalogSearchResults, (n) => n.getAttribute && n.getAttribute('data-picker-product'));
    assert.equal(rows.length, 1);
    assert.equal(rows[0].getAttribute('data-picker-product'), 'new_p');
    passed++;
  }

  // 11. v6.71.1 (MM-09): same-title distinct product_ids can be picked & enqueued with IDs; manual edit clears stale product_id
  {
    const calls = [];
    const h = createHarness(async (url, opts) => {
      calls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null });
      if (url.startsWith('/api/admin/matching/catalog-search')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            status: 'ok',
            items: [
              { product_id: 'p_ac1', title: 'Кондиционер LG Dualcool P09EP2', canonical_key: 'k2:lg:v1', offers_count: 1, min_price: 289990, shops: ['Technodom'] },
              { product_id: 'p_ac1_second', title: 'Кондиционер LG Dualcool P09EP2', canonical_key: 'k2:lg:v2', offers_count: 1, min_price: 279990, shops: ['Mechta'] },
            ],
          }),
        };
      }
      if (url.startsWith('/api/admin/matching/stats')) {
        return { ok: true, status: 200, json: async () => ({ status: 'ok', stats: { pending: 1, human_same: 0, human_different: 0, total: 1 } }) };
      }
      return { ok: true, status: 200, json: async () => ({ status: 'ok', items: [], total: 0 }) };
    });

    h.els.matchingCatalogSearchInput.value = 'LG Dualcool';
    await h.ctx.searchAdminMatchingCatalog();
    const rows = findAll(h.els.matchingCatalogSearchResults, (n) => n.getAttribute && n.getAttribute('data-picker-product'));
    assert.equal(rows.length, 2);

    const pickA1 = findAll(rows[0], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'a')[0];
    const pickB1 = findAll(rows[0], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'b')[0];
    const pickB2 = findAll(rows[1], (n) => n.getAttribute && n.getAttribute('data-pick-side') === 'b')[0];

    assert.equal(pickA1.onclick(), true);
    assert.equal(pickB1.onclick(), false, 'Same product_id on both sides must be blocked');
    assert.equal(pickB2.onclick(), true, 'Different product_id with identical title must be allowed');

    const ok = await h.ctx.enqueueAdminMatchingPair();
    assert.equal(ok, true);
    const enq = calls.find((c) => c.url === '/api/admin/matching/enqueue');
    assert.ok(enq);
    assert.equal(enq.body.title_a, 'Кондиционер LG Dualcool P09EP2');
    assert.equal(enq.body.title_b, 'Кондиционер LG Dualcool P09EP2');
    assert.equal(enq.body.product_id_a, 'p_ac1');
    assert.equal(enq.body.product_id_b, 'p_ac1_second');

    // Manual edit clears stale picked product_id
    assert.equal(pickA1.onclick(), true);
    h.els.matchingEnqueueA.value = 'Другое ручное название';
    h.ctx.clearStalePickedCard('a');
    h.els.matchingEnqueueB.value = 'Второе ручное название';
    calls.length = 0;
    const ok2 = await h.ctx.enqueueAdminMatchingPair();
    assert.equal(ok2, true);
    const enq2 = calls.find((c) => c.url === '/api/admin/matching/enqueue');
    assert.ok(enq2);
    assert.equal('product_id_a' in enq2.body, false, 'Stale product_id_a must not be sent after manual edit');
    passed++;
  }

  console.log(`PASS (${passed}/11) test_frontend_admin_matching.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
