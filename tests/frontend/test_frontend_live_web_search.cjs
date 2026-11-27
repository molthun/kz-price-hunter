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
  extractFn(html, 'getSearchContextKey'),
  extractFn(html, 'isCurrentLiveWebSearch'),
  extractFn(html, 'cancelLiveWebSearch'),
  extractFn(html, 'safeExternalUrl'),
  extractFn(html, 'setLiveWebSearchButtons'),
  extractFn(html, 'closeLiveWebSearch'),
  extractFn(html, 'triggerLiveWebSearch'),
  extractFn(html, 'isCurrentSearchRequest'),
  extractFn(html, 'executeSearch'),
].join('\n\n');

function makeEl(id) {
  return {
    id,
    style: { display: 'none' },
    innerHTML: '',
    textContent: '',
    disabled: false,
    attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    replaceChildren() { this.textContent = ''; },
  };
}

function createHarness(fetchImpl) {
  const els = {
    resultsLiveWebSearchContainer: makeEl('resultsLiveWebSearchContainer'),
    resultsLiveWebSearchBtn: makeEl('resultsLiveWebSearchBtn'),
    resultsHeaderWebSearchBtn: makeEl('resultsHeaderWebSearchBtn'),
    resultsLoading: makeEl('resultsLoading'),
    resultsEmpty: makeEl('resultsEmpty'),
    productCardsContainer: makeEl('productCardsContainer'),
    paginationBar: makeEl('paginationBar'),
    localSearchHelp: makeEl('localSearchHelp'),
  };
  const AppState = {
    query: 'редкий пылесос',
    city: 'Алматы',
    category: '',
    brand: '',
    model: '',
    kind: '',
    condition: '',
    seller: '',
    min_price: '',
    max_price: '50000',
    sort: 'relevance',
    limit: 20,
    page: 1,
    gift: false,
    in_stock: false,
    attributes: { size: '42' },
    searchAbortCtrl: null,
    searchSeq: 0,
    webSearchAbortCtrl: null,
    webSearchSeq: 0,
  };
  const ctx = {
    AppState,
    URLSearchParams,
    AbortController,
    console: { log: () => {}, warn: () => {}, error: () => {} },
    fetch: fetchImpl,
    escapeHtml: (s) => String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;'),
    formatPrice: (p) => String(p) + ' ₸',
    document: { getElementById: (id) => els[id] || null },
    renderResultsHeader: () => {},
    renderSearchResults: () => {},
    renderLocalSearchHelp: () => {},
    showResultsError: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els, AppState };
}

async function runTests() {
  let passed = 0;

  // 1. Explicit "checking" -> "found" state with external badge, disclaimer, and safe href
  {
    let resolveFetch;
    const fetchGate = new Promise((r) => { resolveFetch = r; });
    const h = createHarness(async (url, opts) => {
      assert.ok(url.startsWith('/api/v2/web-search?'));
      assert.ok(opts && opts.signal);
      await fetchGate;
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          total: 2,
          from_cache: false,
          results: [
            {
              title: 'Пылесос X100',
              url: 'https://kaspi.kz/shop/p/x100/',
              snippet: 'В наличии 49 900 ₸',
              domain: 'kaspi.kz',
              shop_name: 'Kaspi Магазин',
              badge: 'Kaspi',
              color: '#f14635',
              price: 49900,
            },
            {
              title: 'Подозрительная ссылка',
              url: 'javascript:alert(1)',
              snippet: '',
              domain: 'bad.example',
              shop_name: 'Bad',
              badge: 'Bad',
              price: null,
            },
          ],
        }),
      };
    });

    const p = h.ctx.triggerLiveWebSearch();
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'checking');
    assert.ok(h.els.resultsLiveWebSearchContainer.innerHTML.includes('Проверяем внешние магазины'));
    assert.equal(h.els.resultsLiveWebSearchBtn.disabled, true);
    assert.equal(h.els.resultsHeaderWebSearchBtn.disabled, true);

    resolveFetch();
    await p;

    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'found');
    const htmlOut = h.els.resultsLiveWebSearchContainer.innerHTML;
    assert.ok(htmlOut.includes('Найдены внешние ссылки'));
    assert.ok(htmlOut.includes('Внешняя ссылка'));
    assert.ok(htmlOut.includes('Наличие, актуальную цену и условия подтверждает продавец'));
    assert.ok(htmlOut.includes('rel="noopener noreferrer external"'));
    assert.ok(htmlOut.includes('href="https://kaspi.kz/shop/p/x100/"'));
    assert.ok(!htmlOut.includes('javascript:alert(1)'));
    assert.equal(h.els.resultsLiveWebSearchBtn.disabled, false);
    assert.equal(h.els.resultsHeaderWebSearchBtn.disabled, false);
    passed++;
  }

  // 2. Explicit "empty" state when ok: true and results: []
  {
    const h = createHarness(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true, total: 0, results: [], from_cache: false }),
    }));
    await h.ctx.triggerLiveWebSearch();
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'empty');
    const htmlOut = h.els.resultsLiveWebSearchContainer.innerHTML;
    assert.ok(htmlOut.includes('Ссылок не найдено'));
    assert.ok(!htmlOut.includes('Источник временно недоступен'));
    passed++;
  }

  // 3. Explicit "unavailable" state on !data.ok (HTTP 503), HTTP 200 + ok:false, non-JSON 502, and network error
  {
    const cases = [
      async () => ({
        ok: false,
        status: 503,
        json: async () => ({ ok: false, error: 'Поисковый узел SearXNG временно недоступен', results: [] }),
      }),
      async () => ({
        ok: true,
        status: 200,
        json: async () => ({ ok: false, error: 'Исчерпан суточный лимит веб-поиска (300 запросов/сутки).', results: [] }),
      }),
      async () => ({
        ok: false,
        status: 502,
        json: async () => { throw new SyntaxError('Unexpected token <'); },
      }),
      async () => {
        throw new TypeError('Failed to fetch');
      },
    ];
    for (const impl of cases) {
      const h = createHarness(impl);
      await h.ctx.triggerLiveWebSearch();
      assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'unavailable');
      const htmlOut = h.els.resultsLiveWebSearchContainer.innerHTML;
      assert.ok(htmlOut.includes('Источник временно недоступен'));
      assert.ok(htmlOut.includes('Повторить'));
      assert.ok(!htmlOut.includes('Ссылок не найдено'));
      assert.equal(h.els.resultsLiveWebSearchBtn.disabled, false);
    }
    passed++;
  }

  // 4. Stale response ignored when query changes (executeSearch aborts & hides old web search)
  {
    let resolveWeb;
    const webGate = new Promise((r) => { resolveWeb = r; });
    let capturedSignal = null;
    const h = createHarness(async (url, opts) => {
      if (url.startsWith('/api/v2/web-search')) {
        capturedSignal = opts.signal;
        await webGate;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            total: 1,
            results: [{ title: 'Старый товар', url: 'https://kaspi.kz/old', domain: 'kaspi.kz', badge: 'Kaspi' }],
          }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [] }),
      };
    });

    const webPromise = h.ctx.triggerLiveWebSearch();
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'checking');

    h.AppState.query = 'другой запрос';
    await h.ctx.executeSearch();
    assert.equal(capturedSignal.aborted, true);
    assert.equal(h.els.resultsLiveWebSearchContainer.style.display, 'none');
    assert.equal(h.els.resultsLiveWebSearchContainer.innerHTML, '');

    resolveWeb();
    await webPromise;
    assert.equal(h.els.resultsLiveWebSearchContainer.style.display, 'none');
    assert.equal(h.els.resultsLiveWebSearchContainer.innerHTML, '');
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'idle');
    passed++;
  }

  // 5. Stale response ignored when filter or city changes even without executeSearch
  {
    for (const mutate of [
      (st) => { st.city = 'Астана'; },
      (st) => { st.max_price = '30000'; },
      (st) => { st.seller = 'sulpak'; },
      (st) => { st.attributes = { size: '43' }; },
    ]) {
      let resolveWeb;
      const webGate = new Promise((r) => { resolveWeb = r; });
      const h = createHarness(async () => {
        await webGate;
        return {
          ok: true,
          status: 200,
          json: async () => ({
            ok: true,
            total: 1,
            results: [{ title: 'Устаревшая ссылка', url: 'https://shop.kz/stale', domain: 'shop.kz', badge: 'Белый Ветер' }],
          }),
        };
      });

      const webPromise = h.ctx.triggerLiveWebSearch();
      mutate(h.AppState);
      h.els.resultsLiveWebSearchContainer.innerHTML = 'SENTINEL_NEW_SCREEN';
      resolveWeb();
      await webPromise;
      assert.equal(h.els.resultsLiveWebSearchContainer.innerHTML, 'SENTINEL_NEW_SCREEN');
    }
    passed++;
  }

  // 6. Second triggerLiveWebSearch cancels the first in-flight request
  {
    let resolveFirst;
    const firstGate = new Promise((r) => { resolveFirst = r; });
    let call = 0;
    const h = createHarness(async () => {
      call++;
      if (call === 1) {
        await firstGate;
        return {
          ok: false,
          status: 503,
          json: async () => ({ ok: false, error: 'Старая ошибка', results: [] }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          total: 1,
          results: [{ title: 'Свежий результат', url: 'https://sulpak.kz/fresh', domain: 'sulpak.kz', badge: 'Sulpak' }],
        }),
      };
    });

    const p1 = h.ctx.triggerLiveWebSearch();
    const p2 = h.ctx.triggerLiveWebSearch();
    await p2;
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'found');
    assert.ok(h.els.resultsLiveWebSearchContainer.innerHTML.includes('Свежий результат'));

    resolveFirst();
    await p1;
    assert.equal(h.els.resultsLiveWebSearchContainer.getAttribute('data-web-search-state'), 'found');
    assert.ok(h.els.resultsLiveWebSearchContainer.innerHTML.includes('Свежий результат'));
    assert.ok(!h.els.resultsLiveWebSearchContainer.innerHTML.includes('Старая ошибка'));
    passed++;
  }

  console.log(`PASS: test_frontend_live_web_search (${passed}/6)`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
