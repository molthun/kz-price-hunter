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
  'let matchingOffset = 0; let matchingTotal = 0; let matchingSerial = 0; const MATCHING_LIMIT = 20;',
  extractFn(html, 'matchingSafeUrl'),
  extractFn(html, 'matchingFormatPrice'),
  extractFn(html, 'matchingRenderDiffTitle'),
  extractFn(html, 'matchingBuildSide'),
  extractFn(html, 'renderMatchingCard'),
  extractFn(html, 'loadAdminMatchingWebHelp'),
  extractFn(html, 'submitAdminMatchingVerdict'),
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
    assert.deepEqual(postCall.body, { pair_hash: 'ph_merge', decision: 'same', notes: 'Одинаковая модель' });
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

  console.log(`PASS (${passed}/6) test_frontend_admin_matching.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
