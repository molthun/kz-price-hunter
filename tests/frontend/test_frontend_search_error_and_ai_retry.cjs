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
  extractFn(html, 'showResultsError'),
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
    setAttribute(k, v) { this.attrs[k] = String(v); },
    getAttribute(k) { return this.attrs[k]; },
    replaceChildren() { this.children = []; this.textContent = ''; },
    append(...nodes) { this.children.push(...nodes); },
    appendChild(node) { this.children.push(node); return node; },
    focus() { this._focused = true; },
    select() { this._selected = true; },
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
  };
  const AppState = {
    query: 'зимняя резина',
    city: 'Астана',
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
    resultItems: [],
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
    getSavedSizesList: () => [],
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

  // 1. showResultsError clears stale cards, pagination, localSearchHelp, live web search, and sets data-results-state="error"
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1,
          items: [{ product: { id: 'p1', title: 'Шина' }, min_price: 28000, best_offer: {} }],
          local_ai: { status: 'ok' }, search_help: {},
        }),
      };
    });
    h.els.productCardsContainer.innerHTML = '<article>Old card</article>';
    h.els.paginationBar.style.display = 'flex';
    h.els.paginationBar.innerHTML = '<button>1</button>';
    h.els.localSearchHelp.style.display = 'block';
    h.els.localSearchHelp.append(makeEl('old-help'));
    h.els.resultsLiveWebSearchContainer.style.display = 'block';
    h.AppState.resultItems = [{ product: { id: 'old' } }];

    h.ctx.showResultsError('Ошибка сервиса поиска', 'Код 500');
    assert.equal(h.els.productCardsContainer.innerHTML, '');
    assert.equal(h.els.paginationBar.style.display, 'none');
    assert.equal(h.els.paginationBar.innerHTML, '');
    assert.equal(h.els.localSearchHelp.style.display, 'none');
    assert.equal(h.els.localSearchHelp.children.length, 0);
    assert.equal(h.els.resultsLiveWebSearchContainer.style.display, 'none');
    assert.equal(h.AppState.resultItems.length, 0);
    assert.equal(h.els.resultsEmpty.getAttribute('data-results-state'), 'error');
    assert.equal(h.els.resultsEmpty.querySelector('.state-title').textContent, 'Ошибка сервиса поиска');
    assert.equal(h.els.resultsEmptyAction.textContent, 'Повторить');

    // Retry calls executeSearch()
    await h.els.resultsEmptyAction.onclick();
    assert.equal(fetchCount, 1);
    passed++;
  }

  // 2. HTTP 500 after previous search clears stale cards and shows error + Retry (never "Сейчас подходящих предложений нет")
  {
    let call = 0;
    const h = createHarness(async () => {
      call++;
      if (call === 1) {
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'final', ai_pending: false, total_products: 1,
            items: [{ product: { id: 'p1', title: 'Шина A' }, min_price: 25000, best_offer: {} }],
            local_ai: { status: 'ok' }, search_help: {},
          }),
        };
      }
      return { ok: false, status: 500, json: async () => ({}) };
    });
    await h.ctx.executeSearch();
    assert.ok(h.els.productCardsContainer.innerHTML.includes('Шина A'));

    await h.ctx.executeSearch();
    assert.equal(h.els.productCardsContainer.innerHTML, '');
    assert.equal(h.els.resultsEmpty.getAttribute('data-results-state'), 'error');
    assert.notEqual(h.els.resultsEmpty.querySelector('.state-title').textContent, 'Сейчас подходящих предложений нет');
    assert.equal(h.els.resultsEmptyAction.textContent, 'Повторить');
    passed++;
  }

  // 3. local_ai.status === 'unavailable' with 0 products shows "Помощник временно недоступен" + "Повторить", no duplicate in help, preserves recovery
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 0, items: [],
          local_ai: { status: 'unavailable' },
          recovery_actions: [{ id: 'all_cities', relax: 'city', count: 2, label: 'Во всех городах — 2 товара' }],
          search_help: {},
        }),
      };
    });
    await h.ctx.executeSearch();
    assert.equal(h.els.resultsEmpty.getAttribute('data-results-state'), 'ai-unavailable');
    assert.equal(h.els.resultsEmpty.querySelector('.state-title').textContent, 'Помощник временно недоступен');
    assert.equal(h.els.resultsEmptyAction.textContent, 'Повторить');
    // Recovery action still shown
    assert.ok(h.els.resultsEmptyExtraActions.children.some((b) => String(b.textContent).includes('Во всех городах')));
    // No duplicate retry button inside #localSearchHelp when total_products === 0
    assert.ok(!h.els.localSearchHelp.children.some((c) => c.getAttribute && c.getAttribute('data-ai-retry') === '1'));

    // Clicking "Повторить" re-runs executeSearch()
    await h.els.resultsEmptyAction.onclick();
    assert.equal(fetchCount, 2);
    passed++;
  }

  // 4. local_ai.status === 'unavailable' with catalog cards shows [data-ai-retry="1"] in #localSearchHelp and preserves filters
  {
    let fetchCount = 0;
    const h = createHarness(async () => {
      fetchCount++;
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 2,
          items: [{ product: { id: 'p1', title: 'Шина Sailun' }, min_price: 28000, best_offer: {} }],
          local_ai: { status: 'unavailable' },
          search_help: {},
        }),
      };
    });
    await h.ctx.executeSearch();
    assert.ok(h.els.productCardsContainer.innerHTML.includes('Шина Sailun'));
    const retryBtn = h.els.localSearchHelp.children.find((c) => c.getAttribute && c.getAttribute('data-ai-retry') === '1');
    assert.ok(retryBtn, 'Retry button must be shown in #localSearchHelp when AI unavailable and products > 0');
    assert.equal(retryBtn.textContent, 'Повторить');
    await retryBtn.onclick();
    assert.equal(fetchCount, 2);
    assert.equal(h.AppState.query, 'зимняя резина');
    assert.equal(h.AppState.city, 'Астана');
    assert.equal(h.AppState.max_price, '45000');
    passed++;
  }

  // 5. Normal empty result (local_ai.status === 'ok') still shows "Сейчас подходящих предложений нет" + "Уточнить запрос"
  {
    const h = createHarness(async () => ({
      ok: true, status: 200,
      json: async () => ({
        search_phase: 'final', ai_pending: false, total_products: 0, items: [],
        local_ai: { status: 'ok' }, recovery_actions: [], search_help: {},
      }),
    }));
    await h.ctx.executeSearch();
    assert.equal(h.els.resultsEmpty.getAttribute('data-results-state'), 'empty');
    assert.equal(h.els.resultsEmpty.querySelector('.state-title').textContent, 'Сейчас подходящих предложений нет');
    assert.equal(h.els.resultsEmptyAction.textContent, 'Уточнить запрос');
    passed++;
  }

  // 6. Late HTTP 500 from query A does NOT overwrite successful results of query B
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      const q = new URL('http://x' + url).searchParams.get('q');
      if (q === 'QueryA') {
        await gateA;
        return { ok: false, status: 500, json: async () => ({}) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1,
          items: [{ product: { id: 'pB', title: 'Товар B' }, min_price: 12000, best_offer: {} }],
          local_ai: { status: 'ok' }, search_help: {},
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
    assert.ok(h.els.productCardsContainer.innerHTML.includes('Товар B'));
    assert.equal(h.els.resultsEmpty.style.display, 'none');
    passed++;
  }

  console.log(`PASS (${passed}/6) test_frontend_search_error_and_ai_retry.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
