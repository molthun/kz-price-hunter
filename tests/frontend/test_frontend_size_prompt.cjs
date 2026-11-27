const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const htmlPath = path.join(__dirname, '..', '..', 'web', 'templates', 'search_first.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function extractFn(src, name) {
  const re = new RegExp(`((?:async\\s+)?function\\s+${name}\\s*\\()`);
  const m = re.exec(src);
  assert.ok(m, `Function ${name} not found`);
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
  extractFn(html, 'getSavedSizesList'),
  extractFn(html, 'getSavedSizesForKind'),
  extractFn(html, 'isCurrentSearchRequest'),
  extractFn(html, 'executeSearch'),
  extractFn(html, 'normalizeTireSizeInput'),
  extractFn(html, 'applyTireSizeClarification'),
  extractFn(html, 'applySizeClarification'),
  extractFn(html, 'renderLocalSearchHelp'),
  extractFn(html, 'renderSearchResults'),
  extractFn(html, 'renderPagination'),
  extractFn(html, 'showResultsMessage'),
  extractFn(html, 'buildSearchWatchPayload'),
  extractFn(html, 'buildEmptySearchActions'),
].join('\n\n');

function makeEl(id) {
  const el = {
    id,
    style: { display: '' },
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    className: '',
    type: '',
    attrs: {},
    children: [],
    onclick: null,
    onsubmit: null,
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    replaceChildren() { this.children = []; this.textContent = ''; },
    append(...nodes) { this.children.push(...nodes); },
    appendChild(node) { this.children.push(node); return node; },
    querySelector(sel) {
      if (sel === '.state-title') return el._titleEl || (el._titleEl = makeEl('state-title'));
      if (sel === '.state-desc') return el._descEl || (el._descEl = makeEl('state-desc'));
      return null;
    },
  };
  return el;
}

function findAll(root, pred, out = []) {
  if (!root || typeof root !== 'object') return out;
  if (pred(root)) out.push(root);
  for (const c of root.children || []) findAll(c, pred, out);
  return out;
}

function createHarness(fetchImpl, userSizes = {}) {
  const normalizedUserSizes = {
    shoe: userSizes.shoe || '',
    clothing: userSizes.clothing || '',
    kidsHeight: userSizes.kidsHeight || '',
  };
  const els = {
    resultsLoading: makeEl('resultsLoading'),
    resultsEmpty: makeEl('resultsEmpty'),
    resultsEmptyAction: makeEl('resultsEmptyAction'),
    resultsEmptyExtraActions: makeEl('resultsEmptyExtraActions'),
    resultsCountText: makeEl('resultsCountText'),
    productCardsContainer: makeEl('productCardsContainer'),
    paginationBar: makeEl('paginationBar'),
    localSearchHelp: makeEl('localSearchHelp'),
    resultsLiveWebSearchContainer: makeEl('resultsLiveWebSearchContainer'),
    resultsLiveWebSearchBtn: makeEl('resultsLiveWebSearchBtn'),
    resultsSearchInput: makeEl('resultsSearchInput'),
  };
  const AppState = {
    query: 'кроссовки nike',
    city: 'Алматы',
    category: '',
    brand: '',
    model: '',
    kind: '',
    condition: '',
    seller: '',
    min_price: '',
    max_price: '45000',
    sort: 'relevance',
    limit: 20,
    page: 1,
    gift: false,
    in_stock: false,
    attributes: {},
    searchAbortCtrl: null,
    searchSeq: 0,
  };
  const ctx = {
    AppState,
    currentUser: { id: 7 },
    URLSearchParams,
    AbortController,
    console,
    fetch: fetchImpl,
    Favorites: { has: () => false },
    escapeHtml: (s) => String(s == null ? '' : s),
    upgradeImageUrl: (u) => u || '',
    getSvgPlaceholder: () => 'data:image/svg+xml,',
    formatPrice: (p) => String(p) + ' ₸',
    pluralizeSellers: (n) => `${n} прод.`,
    pluralizeOffers: (n) => `${n} предл.`,
    getUserSizes: () => normalizedUserSizes,
    renderCardSizesHtml: () => '',
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl(tag),
      querySelectorAll: () => [],
    },
    renderResultsHeader: () => {},
    renderFacets: () => {},
    renderActiveFilterChips: () => {},
    renderQuickSpecPills: () => {},
    showResultsError: () => {},
    updateSearchUrl: () => {},
    resetAllFilters: () => {},
    onCityChanged: (c) => { AppState.city = c; },
    createSearchWatchFromEmpty: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els, AppState };
}

async function runTests() {
  let passed = 0;

  // 1. Toggle opens size panel and clicking a chip sets AppState.attributes.size while preserving query/city/price
  {
    const urls = [];
    const h = createHarness(async (url) => {
      urls.push(url);
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1,
          items: [{ product: { id: 'p_s1', title: 'Кроссовки Nike' }, min_price: 35000, best_offer: {} }],
          local_ai: { status: 'ok' },
          selected_attributes: { size: '42' },
          search_help: { size_prompt: null },
        }),
      };
    });

    h.ctx.renderLocalSearchHelp({
      local_ai: { status: 'ok' },
      total_products: 2,
      search_help: {
        category_name: 'Обувь',
        size_prompt: {
          action_label: 'Указать размер',
          attribute_key: 'size',
          options: [{ value: '41', count: 1 }, { value: '42', count: 2 }],
        },
      },
    });
    const toggle = findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt-toggle') === '1')[0];
    assert.ok(toggle);
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    toggle.onclick();
    assert.equal(toggle.getAttribute('aria-expanded'), 'true');

    const chip42 = findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-option') === '42')[0];
    assert.ok(chip42);
    await chip42.onclick();
    assert.equal(h.AppState.attributes.size, '42');
    assert.equal(h.AppState.query, 'кроссовки nike');
    assert.equal(h.AppState.city, 'Алматы');
    assert.equal(h.AppState.max_price, '45000');
    assert.equal(urls.length, 1);
    passed++;
  }

  // 2. Hidden when size is already selected in AppState or selected_attributes
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    h.AppState.attributes = { size: '42' };
    h.ctx.renderLocalSearchHelp({
      local_ai: { status: 'ok' },
      total_products: 1,
      search_help: {
        size_prompt: { action_label: 'Указать размер', attribute_key: 'size', options: [{ value: '42', count: 1 }] },
      },
    });
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt') === '1').length, 0);
    passed++;
  }

  // 3. Positive case for shoes: highlights matching saved shoe size without inventing sizes missing from catalog
  {
    const h = createHarness(
      async () => ({ ok: true, status: 200, json: async () => ({}) }),
      { shoe: '42', clothing: '', kidsHeight: '' }
    );
    h.ctx.renderLocalSearchHelp({
      local_ai: { status: 'ok' },
      total_products: 2,
      search_help: {
        size_prompt: {
          action_label: 'Указать размер',
          attribute_key: 'size',
          size_kind: 'shoe',
          options: [{ value: '41', count: 1 }, { value: '42', count: 2 }],
        },
      },
    });
    const chips = findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-option'));
    assert.equal(chips.length, 2, 'Must not invent sizes not in options');
    assert.equal(chips[0].getAttribute('data-saved-size'), undefined);
    assert.equal(chips[1].getAttribute('data-saved-size'), '1');
    assert.ok(chips[1].children[0].textContent.includes('★ 42'));
    passed++;
  }

  // 4. Not rendered when tire_size_prompt is active
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    h.ctx.renderLocalSearchHelp({
      local_ai: { status: 'ok' },
      total_products: 2,
      search_help: {
        tire_size_prompt: { action_label: 'Указать размер', attribute_key: 'tire_size', options: [{ value: '205/55 R16', count: 2 }] },
        size_prompt: { action_label: 'Указать размер', attribute_key: 'size', options: [{ value: '42', count: 1 }] },
      },
    });
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-tire-size-prompt') === '1').length, 1);
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt') === '1').length, 0);
    passed++;
  }

  // 5. Works when local_ai is disabled or unavailable
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    h.ctx.renderLocalSearchHelp({
      local_ai: { status: 'disabled' },
      total_products: 2,
      search_help: {
        size_prompt: { action_label: 'Указать размер', attribute_key: 'size', options: [{ value: '41', count: 1 }] },
      },
    });
    assert.equal(h.els.localSearchHelp.style.display, 'block');
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt') === '1').length, 1);
    passed++;
  }

  // 6. Stale response A -> B does not overwrite B's help with A's size_prompt
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      const q = new URL('http://x' + url).searchParams.get('q');
      if (q === 'QueryA') {
        await gateA;
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'final', ai_pending: false, total_products: 2,
            items: [{ product: { id: 'pA', title: 'Кроссовки A' }, min_price: 20000, best_offer: {} }],
            local_ai: { status: 'ok' },
            search_help: { size_prompt: { action_label: 'Указать размер', attribute_key: 'size', options: [{ value: '42', count: 2 }] } },
          }),
        };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1,
          items: [{ product: { id: 'pB', title: 'Ноутбук B' }, min_price: 300000, best_offer: {} }],
          local_ai: { status: 'ok' },
          search_help: { category_name: 'Ноутбуки', size_prompt: null },
        }),
      };
    });
    h.AppState.query = 'QueryA';
    const pA = h.ctx.executeSearch();
    h.AppState.query = 'QueryB';
    await h.ctx.executeSearch();
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt') === '1').length, 0);
    resolveA();
    await pA;
    assert.equal(findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-prompt') === '1').length, 0);
    passed++;
  }

  // 7. SZ-01 regression: saved shoe size 42 must NOT star jacket size 42 (apparel), while saved clothing size DOES star apparel
  {
    const hShoeOnly = createHarness(
      async () => ({ ok: true, status: 200, json: async () => ({}) }),
      { shoe: '42', clothing: '', kidsHeight: '' }
    );
    hShoeOnly.ctx.renderLocalSearchHelp({
      local_ai: { status: 'unavailable' },
      total_products: 2,
      search_help: {
        category_name: 'Женская одежда',
        size_prompt: {
          action_label: 'Указать размер',
          attribute_key: 'size',
          size_kind: 'apparel',
          options: [{ value: '42', count: 2 }, { value: '44', count: 1 }],
        },
      },
    });
    const chips = findAll(hShoeOnly.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-option'));
    assert.equal(chips.length, 2);
    assert.equal(chips[0].getAttribute('data-saved-size'), undefined);
    assert.equal(chips[0].children[0].textContent, '42');
    assert.equal(chips[1].getAttribute('data-saved-size'), undefined);

    const hApparel = createHarness(
      async () => ({ ok: true, status: 200, json: async () => ({}) }),
      { shoe: '', clothing: '42', kidsHeight: '' }
    );
    hApparel.ctx.renderLocalSearchHelp({
      local_ai: { status: 'unavailable' },
      total_products: 2,
      search_help: {
        category_name: 'Женская одежда',
        size_prompt: {
          action_label: 'Указать размер',
          attribute_key: 'size',
          size_kind: 'apparel',
          options: [{ value: '42', count: 2 }, { value: '44', count: 1 }],
        },
      },
    });
    const appChips = findAll(hApparel.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-option'));
    assert.equal(appChips[0].getAttribute('data-saved-size'), '1');
    assert.ok(appChips[0].children[0].textContent.includes('★ 42'));
    passed++;
  }

  // 8. Unconfirmed/missing/mixed size_kind must NEVER show ★ even if user has saved sizes
  {
    for (const badKind of [null, undefined, '', 'mixed']) {
      const h = createHarness(
        async () => ({ ok: true, status: 200, json: async () => ({}) }),
        { shoe: '42', clothing: '42', kidsHeight: '122' }
      );
      h.ctx.renderLocalSearchHelp({
        local_ai: { status: 'ok' },
        total_products: 2,
        search_help: {
          size_prompt: {
            action_label: 'Указать размер',
            attribute_key: 'size',
            size_kind: badKind,
            options: [{ value: '42', count: 2 }],
          },
        },
      });
      const chips = findAll(h.els.localSearchHelp, (e) => e.getAttribute && e.getAttribute('data-size-option'));
      assert.equal(chips.length, 1);
      assert.equal(chips[0].getAttribute('data-saved-size'), undefined);
      assert.equal(chips[0].children[0].textContent, '42');
    }
    passed++;
  }

  console.log(`PASS (${passed}/8) test_frontend_size_prompt.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
