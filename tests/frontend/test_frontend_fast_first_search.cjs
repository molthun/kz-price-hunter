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
].join('\n\n');

function makeEl(id) {
  return {
    id,
    style: { display: '' },
    innerHTML: '',
    textContent: '',
    value: '',
    disabled: false,
    children: [],
    replaceChildren() { this.children = []; this.textContent = ''; },
    append(...nodes) { this.children.push(...nodes); },
  };
}

function createHarness(fetchImpl) {
  const els = {
    resultsLoading: makeEl('resultsLoading'),
    resultsEmpty: makeEl('resultsEmpty'),
    productCardsContainer: makeEl('productCardsContainer'),
    paginationBar: makeEl('paginationBar'),
    localSearchHelp: makeEl('localSearchHelp'),
    resultsLiveWebSearchContainer: makeEl('resultsLiveWebSearchContainer'),
    resultsLiveWebSearchBtn: makeEl('resultsLiveWebSearchBtn'),
    resultsSearchInput: makeEl('resultsSearchInput'),
  };
  const rendered = [];
  const errors = [];
  const AppState = {
    query: 'кроссовки 42',
    city: 'Астана',
    category: '',
    brand: '',
    model: '',
    kind: '',
    condition: '',
    seller: '',
    min_price: '',
    max_price: '',
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
    currentUser: { id: 1 },
    URLSearchParams,
    AbortController,
    console,
    fetch: fetchImpl,
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl(tag),
    },
    renderResultsHeader: () => {},
    renderSearchResults: (data) => {
      rendered.push(data);
      ctx.renderLocalSearchHelp(data);
    },
    showResultsError: (title, desc) => { errors.push({ title, desc }); },
    updateSearchUrl: () => {},
    createSearchWatchFromEmpty: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els, rendered, errors, AppState };
}

async function runTests() {
  let passed = 0;

  // 1. Preview renders cards immediately while final is pending; then final updates results
  {
    const calls = [];
    let resolveFinal;
    const finalReady = new Promise((r) => { resolveFinal = r; });
    const h = createHarness(async (url, opts) => {
      calls.push(url);
      if (url.includes('phase=preview')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            search_phase: 'preview',
            ai_pending: true,
            total_products: 2,
            items: [{ product: { id: 'p1' } }, { product: { id: 'p2' } }],
            local_ai: { status: 'pending' },
            search_help: { ai_pending: true },
          }),
        };
      }
      await finalReady;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          search_phase: 'final',
          ai_pending: false,
          total_products: 3,
          items: [{ product: { id: 'p1' } }, { product: { id: 'p2' } }, { product: { id: 'p3' } }],
          local_ai: { status: 'ok' },
          search_help: { category_name: 'Обувь', ai_pending: false },
        }),
      };
    });

    const p = h.ctx.executeSearch();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(h.rendered.length, 1, 'Preview cards should render immediately');
    assert.equal(h.rendered[0].search_phase, 'preview');
    assert.equal(h.els.resultsLoading.style.display, 'none');
    assert.match(h.els.localSearchHelp.children[0].textContent, /ИИ уточняет запрос/);

    resolveFinal();
    await p;
    assert.equal(h.rendered.length, 2, 'Final should update results after AI finishes');
    assert.equal(h.rendered[1].search_phase, 'final');
    assert.equal(h.rendered[1].total_products, 3);
    assert.equal(calls.length, 2);
    passed++;
  }

  // 2. Empty preview does NOT render empty state or watch button until final arrives
  {
    let resolveFinal;
    const finalReady = new Promise((r) => { resolveFinal = r; });
    const h = createHarness(async (url) => {
      if (url.includes('phase=preview')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            search_phase: 'preview',
            ai_pending: true,
            total_products: 0,
            items: [],
            local_ai: { status: 'pending' },
            search_help: { ai_pending: true },
          }),
        };
      }
      await finalReady;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          search_phase: 'final',
          ai_pending: false,
          total_products: 0,
          items: [],
          local_ai: { status: 'ok' },
          search_help: { category_name: 'Обувь', watch_target: '@local-search:{}', ai_pending: false },
        }),
      };
    });

    const p = h.ctx.executeSearch();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(h.rendered.length, 0, 'Empty preview must not render final empty state prematurely');
    assert.equal(h.els.resultsEmpty.style.display, 'none');
    assert.match(h.els.localSearchHelp.children[0].textContent, /Ищем с ИИ-помощником/);

    resolveFinal();
    await p;
    assert.equal(h.rendered.length, 1);
    assert.equal(h.rendered[0].search_phase, 'final');
    passed++;
  }

  // 3. Stale response from query A never overwrites query B
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      const q = new URL('http://x' + url).searchParams.get('q');
      if (q === 'A' && url.includes('phase=preview')) {
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'preview', ai_pending: true, total_products: 1,
            items: [{ product: { id: 'A_prev' } }], local_ai: { status: 'pending' }, search_help: { ai_pending: true },
          }),
        };
      }
      if (q === 'A' && url.includes('phase=final')) {
        await gateA;
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'final', ai_pending: false, total_products: 99,
            items: [{ product: { id: 'A_late' } }], local_ai: { status: 'ok' }, search_help: { ai_pending: false },
          }),
        };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 5,
          items: [{ product: { id: 'B_final' } }], local_ai: { status: 'ok', cached: true }, search_help: { ai_pending: false },
        }),
      };
    });

    h.AppState.query = 'A';
    const pA = h.ctx.executeSearch();
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(h.rendered[h.rendered.length - 1].items[0].product.id, 'A_prev');

    // User switches to query B while A's final is still in flight
    h.AppState.query = 'B';
    await h.ctx.executeSearch();
    assert.equal(h.rendered[h.rendered.length - 1].items[0].product.id, 'B_final');

    // Now A's late final resolves — must be ignored!
    resolveA();
    await pA;
    assert.equal(h.rendered[h.rendered.length - 1].items[0].product.id, 'B_final');
    assert.equal(h.rendered[h.rendered.length - 1].total_products, 5);
    passed++;
  }

  // 4. Final AI error falls back cleanly to catalog preview promoted to final unavailable
  {
    const h = createHarness(async (url) => {
      if (url.includes('phase=preview')) {
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'preview', ai_pending: true, total_products: 1,
            items: [{ product: { id: 'cat_1' } }], local_ai: { status: 'pending' }, search_help: { ai_pending: true },
          }),
        };
      }
      return { ok: false, status: 504, json: async () => ({}) };
    });

    await h.ctx.executeSearch();
    assert.equal(h.rendered.length, 2);
    assert.equal(h.rendered[1].search_phase, 'final');
    assert.equal(h.rendered[1].ai_pending, false);
    assert.equal(h.rendered[1].local_ai.status, 'unavailable');
    assert.equal(h.rendered[1].items[0].product.id, 'cat_1');
    passed++;
  }

  // 5. Warm cache returns search_phase='final' on first request -> no second fetch
  {
    const calls = [];
    const h = createHarness(async (url) => {
      calls.push(url);
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 4,
          items: [{ product: { id: 'warm_1' } }], local_ai: { status: 'ok', cached: true }, search_help: { ai_pending: false },
        }),
      };
    });

    await h.ctx.executeSearch();
    assert.equal(calls.length, 1, 'Warm cache must finish in 1 HTTP request');
    assert.equal(h.rendered.length, 1);
    assert.equal(h.rendered[0].total_products, 4);
    passed++;
  }

  console.log(`PASS (${passed}/5) test_frontend_fast_first_search.cjs`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
