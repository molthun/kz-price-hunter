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
  extractFn(html, 'normalizeTireSizeInput'),
  extractFn(html, 'applyTireSizeClarification'),
  extractFn(html, 'renderLocalSearchHelp'),
  extractFn(html, 'buildSearchWatchPayload'),
  extractFn(html, 'isCurrentSearchRequest'),
  extractFn(html, 'executeSearch'),
].join('\n\n');

function makeEl(tag) {
  return {
    tagName: String(tag || 'div').toUpperCase(),
    id: '',
    className: '',
    type: '',
    value: '',
    placeholder: '',
    textContent: '',
    innerHTML: '',
    disabled: false,
    style: { display: '' },
    attrs: {},
    children: [],
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k]; },
    append(...nodes) { this.children.push(...nodes); },
    appendChild(node) { this.children.push(node); return node; },
    replaceChildren() { this.children = []; this.textContent = ''; },
    focus() { this._focused = true; },
  };
}

function findAll(node, pred, out = []) {
  if (!node || typeof node !== 'object') return out;
  if (pred(node)) out.push(node);
  for (const ch of node.children || []) findAll(ch, pred, out);
  return out;
}

function createHarness(fetchImpl) {
  const els = {
    localSearchHelp: makeEl('div'),
    resultsLoading: makeEl('div'),
    resultsEmpty: makeEl('div'),
    productCardsContainer: makeEl('div'),
    paginationBar: makeEl('div'),
    resultsLiveWebSearchContainer: makeEl('div'),
    resultsLiveWebSearchBtn: makeEl('button'),
    resultsSearchInput: makeEl('input'),
  };
  let urlUpdates = 0;
  const AppState = {
    query: 'зимняя резина',
    city: 'Алматы',
    category: '',
    brand: '',
    model: '',
    kind: '',
    condition: '',
    seller: 'turbotyres',
    min_price: '',
    max_price: '45000',
    sort: 'relevance',
    limit: 20,
    page: 2,
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
    console: { log: () => {}, warn: () => {}, error: () => {} },
    fetch: fetchImpl || (async () => ({
      ok: true,
      status: 200,
      json: async () => ({ search_phase: 'final', ai_pending: false, total_products: 1, items: [], local_ai: { status: 'ok' }, search_help: {} }),
    })),
    document: {
      getElementById: (id) => els[id] || null,
      createElement: (tag) => makeEl(tag),
    },
    updateSearchUrl: () => { urlUpdates++; },
    renderResultsHeader: () => {},
    renderSearchResults: () => {},
    showResultsError: () => {},
    createSearchWatchFromEmpty: () => {},
  };
  vm.createContext(ctx);
  vm.runInContext(code, ctx);
  return { ctx, els, AppState, getUrlUpdates: () => urlUpdates };
}

async function runTests() {
  let passed = 0;

  // 1. normalizeTireSizeInput accepts common variants and rejects invalid strings
  {
    const h = createHarness();
    for (const raw of ['205/55 R16', '205/55r16', '205/55 р16', '205 55 16', '205/55-16', '205/55/16', '205/55 ZR16']) {
      assert.equal(h.ctx.normalizeTireSizeInput(raw), '205/55 R16', `Failed for ${raw}`);
    }
    assert.equal(h.ctx.normalizeTireSizeInput(''), null);
    assert.equal(h.ctx.normalizeTireSizeInput('абракадабра'), null);
    assert.equal(h.ctx.normalizeTireSizeInput('99/55 R16'), null);
    passed++;
  }

  // 2. Prompt renders "Указать размер", expands facet chips, clicking chip runs search preserving constraints
  {
    const fetchedUrls = [];
    const h = createHarness(async (url) => {
      fetchedUrls.push(url);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1, items: [],
          local_ai: { status: 'ok' }, selected_attributes: { tire_size: '205/55 R16', season: 'Зимние' },
          search_help: { category_id: 'tires', category_name: 'Шины и диски', tire_size_prompt: null },
        }),
      };
    });
    h.ctx.renderLocalSearchHelp({
      total_products: 5,
      local_ai: { status: 'ok' },
      selected_attributes: { season: 'Зимние' },
      search_help: {
        category_id: 'tires',
        category_name: 'Шины и диски',
        clarification: 'Укажите размер шин.',
        tire_size_prompt: {
          action_label: 'Указать размер',
          attribute_key: 'tire_size',
          placeholder: 'Например, 205/55 R16',
          options: [
            { value: '205/55 R16', count: 3 },
            { value: '215/65 R16', count: 2 },
          ],
        },
      },
    });
    const toggleBtn = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-toggle-btn')[0];
    const panel = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-panel')[0];
    assert.ok(toggleBtn && panel);
    assert.equal(toggleBtn.textContent, 'Указать размер');
    assert.equal(panel.style.display, 'none');

    toggleBtn.onclick();
    assert.equal(panel.style.display, 'flex');
    assert.equal(toggleBtn.getAttribute('aria-expanded'), 'true');

    const chips = findAll(panel, (n) => n.className === 'tire-size-chip');
    assert.equal(chips.length, 2);
    chips[0].onclick();
    await new Promise((r) => setTimeout(r, 10));

    assert.equal(h.AppState.attributes.tire_size, '205/55 R16');
    assert.equal(h.AppState.query, 'зимняя резина');
    assert.equal(h.AppState.city, 'Алматы');
    assert.equal(h.AppState.max_price, '45000');
    assert.equal(h.AppState.seller, 'turbotyres');
    assert.equal(h.AppState.page, 1);
    assert.ok(h.getUrlUpdates() >= 1);
    assert.ok(fetchedUrls.some((u) => {
      const sp = new URL('http://x' + u).searchParams;
      return JSON.parse(sp.get('attributes') || '{}').tire_size === '205/55 R16'
        && sp.get('max_price') === '45000'
        && sp.get('city') === 'Алматы'
        && sp.get('seller') === 'turbotyres';
    }));
    passed++;
  }

  // 3. Manual input validates bad input and applies normalized size
  {
    const h = createHarness();
    h.ctx.renderLocalSearchHelp({
      total_products: 3,
      local_ai: { status: 'unavailable' },
      search_help: {
        tire_size_prompt: { action_label: 'Указать размер', options: [] },
      },
    });
    const form = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-form')[0];
    const input = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-input')[0];
    const errEl = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-error')[0];
    assert.ok(form && input && errEl);

    input.value = 'неверный размер';
    form.onsubmit({ preventDefault: () => {} });
    assert.equal(errEl.style.display, 'block');
    assert.equal(h.AppState.attributes.tire_size, undefined);

    input.value = '215/65р16';
    form.onsubmit({ preventDefault: () => {} });
    assert.equal(errEl.style.display, 'none');
    assert.equal(h.AppState.attributes.tire_size, '215/65 R16');
    passed++;
  }

  // 4. Already selected tire_size hides prompt
  {
    const h = createHarness();
    h.AppState.attributes = { tire_size: '205/55 R16' };
    h.ctx.renderLocalSearchHelp({
      total_products: 2,
      local_ai: { status: 'ok' },
      selected_attributes: { tire_size: '205/55 R16' },
      search_help: {
        category_id: 'tires',
        category_name: 'Шины и диски',
        tire_size_prompt: { action_label: 'Указать размер', options: [{ value: '205/55 R16', count: 2 }] },
      },
    });
    const prompts = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-prompt');
    assert.equal(prompts.length, 0);
    passed++;
  }

  // 5. Late response for query A (tires with prompt) does not overwrite query B
  {
    let resolveA;
    const gateA = new Promise((r) => { resolveA = r; });
    const h = createHarness(async (url) => {
      const q = new URL('http://x' + url).searchParams.get('q');
      if (q === 'зимняя резина') {
        await gateA;
        return {
          ok: true, status: 200,
          json: async () => ({
            search_phase: 'final', ai_pending: false, total_products: 3, items: [],
            local_ai: { status: 'ok' },
            search_help: {
              category_name: 'Шины и диски',
              tire_size_prompt: { action_label: 'Указать размер', options: [{ value: '205/55 R16', count: 3 }] },
            },
          }),
        };
      }
      return {
        ok: true, status: 200,
        json: async () => ({
          search_phase: 'final', ai_pending: false, total_products: 1, items: [],
          local_ai: { status: 'ok' },
          search_help: { category_name: 'Смартфоны', tire_size_prompt: null },
        }),
      };
    });

    h.AppState.query = 'зимняя резина';
    const pA = h.ctx.executeSearch();
    h.AppState.query = 'iPhone 15';
    await h.ctx.executeSearch();
    resolveA();
    await pA;

    const prompts = findAll(h.els.localSearchHelp, (n) => n.className === 'tire-size-prompt');
    assert.equal(prompts.length, 0);
    passed++;
  }

  // 6. Watch payload after selecting tire_size keeps tire_size, city, max_price, and uses new_offer
  {
    const h = createHarness();
    h.AppState.query = 'зимняя резина';
    h.AppState.city = 'Алматы';
    h.AppState.max_price = '45000';
    h.AppState.attributes = { tire_size: '235/45 R19' };
    const payload = h.ctx.buildSearchWatchPayload(h.AppState, {
      selected_attributes: { season: 'Зимние', tire_size: '235/45 R19' },
      search_help: {
        watch_city: 'Алматы',
        watch_target: '@local-search:' + JSON.stringify({
          attributes: { tire_size: '235/45 R19' },
          category: 'tires',
          inferred_attributes: { season: 'Зимние' },
          max_price: 45000,
          original_query: 'зимняя резина',
          query: 'зимн',
        }),
      },
    });
    assert.equal(payload.condition, 'new_offer');
    assert.equal(payload.city, 'Алматы');
    assert.equal(payload.title, 'зимняя резина (235/45 R19)');
    const spec = JSON.parse(payload.target.slice('@local-search:'.length));
    assert.equal(spec.attributes.tire_size, '235/45 R19');
    assert.equal(spec.inferred_attributes.season, 'Зимние');
    assert.equal(spec.max_price, 45000);
    passed++;
  }

  console.log(`PASS: test_frontend_tire_size_prompt (${passed}/6)`);
}

runTests().catch((err) => {
  console.error(err);
  process.exit(1);
});
