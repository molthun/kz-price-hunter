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
  extractFn(html, 'isCurrentSearchRequest'),
  extractFn(html, 'executeSearch'),
  extractFn(html, 'renderLocalSearchHelp'),
  extractFn(html, 'renderSearchResults'),
  extractFn(html, 'renderPagination'),
  extractFn(html, 'showResultsMessage'),
  extractFn(html, 'buildSearchWatchPayload'),
  extractFn(html, 'buildEmptySearchActions'),
  extractFn(html, 'applyCurrentFilters'),
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
    setAttribute(k, v) { this.attrs[k] = v; },
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

function createHarness(fetchImpl) {
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
    filterMinPrice: makeEl('filterMinPrice'),
    filterMaxPrice: makeEl('filterMaxPrice'),
    filterGift: Object.assign(makeEl('filterGift'), { checked: false }),
    filterInStock: Object.assign(makeEl('filterInStock'), { checked: false }),
  };
  const condRadios = [
    { name: 'filterCondition', value: '', checked: true },
    { name: 'filterCondition', value: 'NEW', checked: false },
    { name: 'filterCondition', value: 'USED', checked: false },
    { name: 'filterCondition', value: 'OPEN_BOX', checked: false },
    { name: 'filterCondition', value: 'REFURBISHED', checked: false },
  ];
  const cityCalls = [];
  const watchCalls = [];
  const AppState = {
    query: 'кроссовки 42',
    city: 'Астана',
    category: '',
    brand: '',
    model: '',
    kind: '',
    condition: '',
    seller: 'kari',
    min_price: '',
    max_price: '22000',
    sort: 'relevance',
    limit: 20,
    page: 1,
    gift: false,
    in_stock: false,
    attributes: { size: '42' },
    searchAbortCtrl: null,
    searchSeq: 0,
  };
  els.filterMaxPrice.value = '22000';

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
    getSavedSizesList: () => [],
    renderCardSizesHtml: () => '',
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl(tag),
      querySelectorAll: (sel) => (sel === 'input[name="filterCondition"]' ? condRadios : []),
      querySelector: (sel) => (sel === 'input[name="filterCondition"]:checked' ? (condRadios.find((r) => r.checked) || null) : null),
    },
    closeMobileFilters: () => {},
    renderResultsHeader: () => {},
    renderFacets: () => {},
    renderActiveFilterChips: () => {},
    renderQuickSpecPills: () => {},
    showResultsError: () => {},
    updateSearchUrl: () => {},
    resetAllFilters: () => {},
    onCityChanged: (c) => { cityCalls.push(c); AppState.city = c; },
    createSearchWatchFromEmpty: (btn, snap, data) => { watchCalls.push({ snap, data }); },
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els, condRadios, AppState, cityCalls, watchCalls };
}

async function runTests() {
  let passed = 0;

  // 1. Zero matches anywhere (recovery_actions: []) -> no blind all_cities button
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      { total_products: 0, recovery_actions: [], local_ai: { status: 'disabled' } },
      { id: 7 }
    );
    const ids = actions.map((a) => a.id);
    assert.ok(!ids.includes('all_cities'), 'Must not show all_cities when recovery_actions is []');
    assert.ok(!ids.includes('clear_price'));
    assert.ok(!ids.includes('clear_seller'));
    assert.ok(ids.includes('watch_search'));
    passed++;
  }

  // 2. Single useful action (all_cities with count 3) -> shows exact count and applies on click
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [{ id: 'all_cities', relax: 'city', count: 3, count_unit: 'products', label: 'Во всех городах — 3 товара' }],
        local_ai: { status: 'disabled' },
      },
      { id: 7 }
    );
    const cityAct = actions.find((a) => a.id === 'all_cities');
    assert.ok(cityAct);
    assert.equal(cityAct.label, '🌍 Во всех городах — 3 товара');
    cityAct.run();
    assert.deepEqual(h.cityCalls, ['Все']);
    assert.equal(h.AppState.attributes.size, '42');
    assert.equal(h.AppState.seller, 'kari');
    passed++;
  }

  // 3. Multiple useful actions (city + price + seller) -> all 3 shown separately; each relaxes only its own filter
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [{ product: { id: 'p1', title: 'Кроссовки' }, min_price: 30000, best_offer: {} }] }),
      };
    });
    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [
          { id: 'all_cities', relax: 'city', count: 1, label: 'Во всех городах — 1 предложение' },
          { id: 'clear_price', relax: 'price', count: 2, label: 'Без ограничения цены — 2 предложения' },
          { id: 'clear_seller', relax: 'seller', count: 4, label: 'У всех продавцов — 4 предложения' },
        ],
        local_ai: { status: 'disabled' },
      },
      { id: 7 }
    );
    assert.ok(actions.find((a) => a.id === 'all_cities'));
    const priceAct = actions.find((a) => a.id === 'clear_price');
    const sellerAct = actions.find((a) => a.id === 'clear_seller');
    const watchAct = actions.find((a) => a.id === 'watch_search');
    assert.ok(priceAct && sellerAct && watchAct);

    // Watch snapshot keeps original constraints even if we inspect before/after clicking price
    watchAct.run({});
    assert.equal(h.watchCalls[0].snap.max_price, '22000');
    assert.equal(h.watchCalls[0].snap.city, 'Астана');
    assert.equal(h.watchCalls[0].snap.seller, 'kari');
    assert.equal(h.watchCalls[0].snap.attributes.size, '42');

    // Click clear_price: clears only price, preserves city, seller, size
    priceAct.run();
    assert.equal(h.AppState.max_price, '');
    assert.equal(h.els.filterMaxPrice.value, '');
    assert.equal(h.AppState.city, 'Астана');
    assert.equal(h.AppState.seller, 'kari');
    assert.equal(h.AppState.attributes.size, '42');

    // Click clear_seller: clears only seller, preserves city and size
    sellerAct.run();
    assert.equal(h.AppState.seller, '');
    assert.equal(h.AppState.city, 'Астана');
    assert.equal(h.AppState.attributes.size, '42');
    assert.ok(fetchCount >= 2);
    passed++;
  }

  // 4. Switching query A -> B during empty recovery check ignores A's late response
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
            search_phase: 'final', ai_pending: false, total_products: 0, items: [],
            recovery_actions: [{ id: 'all_cities', relax: 'city', count: 9, label: 'Во всех городах — 9 предложений' }],
            local_ai: { status: 'ok' }, search_help: {},
          }),
        };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1,
          items: [{ product: { id: 'pB', title: 'Товар B' }, min_price: 10000, best_offer: {} }],
          recovery_actions: [], local_ai: { status: 'ok' }, search_help: {},
        }),
      };
    });

    h.AppState.query = 'QueryA';
    const pA = h.ctx.executeSearch();
    h.AppState.query = 'QueryB';
    await h.ctx.executeSearch();
    assert.ok(h.els.productCardsContainer.innerHTML.includes('Товар B'));
    assert.equal(h.els.resultsEmpty.style.display, 'none');

    resolveA();
    await pA;
    // A's late empty response with recovery actions must NOT overwrite B!
    assert.ok(h.els.productCardsContainer.innerHTML.includes('Товар B'));
    assert.equal(h.els.resultsEmpty.style.display, 'none');
    passed++;
  }

  // 5. open_category action opens category, clears query/brand/model/kind/attributes, preserves city/price/seller
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 3, items: [] }),
      };
    });
    h.AppState.query = 'зимняя резина';
    h.AppState.brand = 'Nokian';
    h.AppState.model = 'Hakkapeliitta';
    h.AppState.kind = 'шипы';
    h.AppState.attributes = { tire_size: '235/45 R19' };
    h.AppState.page = 2;
    h.els.resultsSearchInput.value = 'зимняя резина';

    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [],
        local_ai: { status: 'ok' },
        search_help: {
          category_action: {
            id: 'open_category',
            relax: 'category',
            category_id: 'tires',
            category_name: 'Шины и диски',
            count: 3,
            count_unit: 'products',
            label: 'Посмотреть раздел «Шины и диски» — 3 товара',
          },
        },
      },
      { id: 7 }
    );
    const catAct = actions.find((a) => a.id === 'open_category');
    assert.ok(catAct, 'open_category action must be present');
    assert.equal(catAct.label, '📂 Посмотреть раздел «Шины и диски» — 3 товара');

    // Watch created before clicking open_category still uses original query and tire_size
    const watchAct = actions.find((a) => a.id === 'watch_search');
    assert.ok(watchAct);
    watchAct.run({});
    assert.equal(h.watchCalls[0].snap.query, 'зимняя резина');
    assert.equal(h.watchCalls[0].snap.attributes.tire_size, '235/45 R19');

    // Click open_category
    catAct.run();
    assert.equal(h.AppState.query, '');
    assert.equal(h.els.resultsSearchInput.value, '');
    assert.equal(h.AppState.category, 'tires');
    assert.equal(h.AppState.brand, '');
    assert.equal(h.AppState.model, '');
    assert.equal(h.AppState.kind, '');
    assert.equal(Object.keys(h.AppState.attributes).length, 0);
    assert.equal(h.AppState.page, 1);
    // City, price and seller preserved
    assert.equal(h.AppState.city, 'Астана');
    assert.equal(h.AppState.max_price, '22000');
    assert.equal(h.AppState.seller, 'kari');
    assert.equal(fetchCount, 1);
    passed++;
  }

  // 6. Invalid or zero-count category_action is ignored
  {
    const h = createHarness(async () => ({ ok: true, status: 200, json: async () => ({}) }));
    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [],
        local_ai: { status: 'disabled' },
        search_help: { category_action: { id: 'open_category', category_id: 'tires', count: 0, label: 'x' } },
      },
      { id: 7 }
    );
    assert.ok(!actions.find((a) => a.id === 'open_category'));
    passed++;
  }

  // 7. ER-01: open_category transfers effective text-derived city and max_price into AppState & inputs without overriding explicit filters
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [] }),
      };
    });
    h.AppState.query = 'Galaxy S24+ в Астане до 350000 тенге';
    h.AppState.city = 'Все';
    h.AppState.max_price = '';
    h.AppState.min_price = '';
    h.AppState.seller = 'sulpak';
    h.els.filterMaxPrice.value = '';
    h.els.resultsSearchInput.value = h.AppState.query;

    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [],
        local_ai: { status: 'ok' },
        search_help: {
          category_action: {
            id: 'open_category',
            relax: 'category',
            category_id: 'phones',
            category_name: 'Смартфоны',
            city: 'Астана',
            min_price: null,
            max_price: 350000,
            condition: null,
            count: 1,
            count_unit: 'products',
            label: 'Посмотреть раздел «Смартфоны» — 1 товар',
          },
        },
      },
      { id: 7 }
    );
    const catAct = actions.find((a) => a.id === 'open_category');
    assert.ok(catAct);
    catAct.run();
    assert.equal(h.AppState.query, '');
    assert.equal(h.els.resultsSearchInput.value, '');
    assert.equal(h.AppState.category, 'phones');
    assert.equal(h.AppState.city, 'Астана');
    assert.equal(h.AppState.max_price, '350000');
    assert.equal(h.els.filterMaxPrice.value, '350000');
    assert.equal(h.AppState.seller, 'sulpak');
    assert.equal(fetchCount, 1);
    passed++;
  }

  // 8. ER-02: open_category syncs filterCondition radios so re-applying filters keeps condition, and explicit change applies
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [] }),
      };
    });
    h.AppState.query = 'Galaxy S25 новый';
    h.AppState.city = 'Астана';
    h.AppState.condition = '';
    h.AppState.max_price = '';
    h.AppState.seller = '';
    h.els.filterMaxPrice.value = '';

    const actions = h.ctx.buildEmptySearchActions(
      h.AppState,
      {
        total_products: 0,
        recovery_actions: [],
        local_ai: { status: 'disabled' },
        search_help: {
          category_action: {
            id: 'open_category',
            relax: 'category',
            category_id: 'phones',
            category_name: 'Смартфоны',
            city: 'Астана',
            min_price: null,
            max_price: null,
            condition: 'NEW',
            count: 1,
            count_unit: 'products',
            label: 'Посмотреть раздел «Смартфоны» — 1 товар',
          },
        },
      },
      { id: 7 }
    );
    const catAct = actions.find((a) => a.id === 'open_category');
    assert.ok(catAct);
    catAct.run();
    assert.equal(h.AppState.condition, 'NEW');
    assert.equal(h.condRadios[0].checked, false);
    assert.equal(h.condRadios[1].checked, true);
    assert.equal(h.condRadios[2].checked, false);

    // Re-applying filters without edits preserves condition=NEW
    h.ctx.applyCurrentFilters(true);
    assert.equal(h.AppState.condition, 'NEW');

    // Explicit switch to "Все" clears condition
    h.condRadios[0].checked = true;
    h.condRadios[1].checked = false;
    h.ctx.applyCurrentFilters(true);
    assert.equal(h.AppState.condition, '');
    assert.equal(fetchCount, 3);
    passed++;
  }

  // 9. ER-02: template has OPEN_BOX and REFURBISHED radios, and both are preserved across applyCurrentFilters until explicitly cleared
  {
    assert.ok(html.includes('name="filterCondition" value="OPEN_BOX"'), 'Missing OPEN_BOX radio in search_first.html');
    assert.ok(html.includes('name="filterCondition" value="REFURBISHED"'), 'Missing REFURBISHED radio in search_first.html');
    for (const [condVal, radioIdx] of [['OPEN_BOX', 3], ['REFURBISHED', 4]]) {
      const h = createHarness(async () => ({
        ok: true, status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [] }),
      }));
      h.AppState.query = condVal === 'OPEN_BOX' ? 'Galaxy S25 витринный' : 'Galaxy S25 восстановленный';
      h.AppState.city = 'Астана';
      h.AppState.condition = '';
      h.AppState.max_price = '';
      h.AppState.seller = '';
      h.els.filterMaxPrice.value = '';

      const actions = h.ctx.buildEmptySearchActions(
        h.AppState,
        {
          total_products: 0,
          recovery_actions: [],
          local_ai: { status: 'disabled' },
          search_help: {
            category_action: {
              id: 'open_category',
              relax: 'category',
              category_id: 'phones',
              category_name: 'Смартфоны',
              city: 'Астана',
              min_price: null,
              max_price: null,
              condition: condVal,
              count: 1,
              count_unit: 'products',
              label: 'Посмотреть раздел «Смартфоны» — 1 товар',
            },
          },
        },
        { id: 7 }
      );
      const catAct = actions.find((a) => a.id === 'open_category');
      assert.ok(catAct);
      catAct.run();
      assert.equal(h.AppState.condition, condVal);
      assert.equal(h.condRadios[radioIdx].checked, true);
      assert.equal(h.condRadios[0].checked, false);

      // Re-applying filters without edits preserves condition
      h.ctx.applyCurrentFilters(true);
      assert.equal(h.AppState.condition, condVal);

      // Explicit switch to "Все" clears condition
      h.condRadios.forEach((r, idx) => { r.checked = (idx === 0); });
      h.ctx.applyCurrentFilters(true);
      assert.equal(h.AppState.condition, '');
    }
    passed++;
  }

  console.log(`PASS (${passed}/9) test_frontend_empty_recovery.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
