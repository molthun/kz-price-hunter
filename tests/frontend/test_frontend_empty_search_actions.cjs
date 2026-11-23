const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const htmlPath = path.resolve(__dirname, '../../web/templates/search_first.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function extractFunc(src, name) {
  const re = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const m = re.exec(src);
  if (!m) throw new Error(`Function ${name} not found`);
  let i = src.indexOf('{', m.index);
  let depth = 0;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') {
      depth--;
      if (depth === 0) return src.slice(m.index, i + 1);
    }
  }
  throw new Error(`Unclosed function ${name}`);
}

assert(html.includes('id="resultsEmptyExtraActions"'), 'Missing #resultsEmptyExtraActions container');

const snippet = [
  extractFunc(html, 'showResultsMessage'),
  extractFunc(html, 'showResultsError'),
  extractFunc(html, 'buildSearchWatchPayload'),
  extractFunc(html, 'createSearchWatchFromEmpty'),
  extractFunc(html, 'buildEmptySearchActions'),
  extractFunc(html, 'renderLocalSearchHelp'),
].join('\n\n');

const helpBox = {
  children: [],
  style: { display: 'none' },
  replaceChildren() { this.children = []; },
  append(...els) { this.children.push(...els); },
};

const extraBox = {
  children: [],
  style: { display: 'none' },
  replaceChildren() { this.children = []; },
  appendChild(el) { this.children.push(el); },
};
const emptyTitle = { textContent: '' };
const emptyDesc = { textContent: '' };
const emptyAction = { textContent: '', onclick: null };
const emptyEl = {
  style: { display: 'none' },
  querySelector(sel) {
    if (sel === '.state-title') return emptyTitle;
    if (sel === '.state-desc') return emptyDesc;
    return null;
  },
};

let cityCalls = [];
let searchCalls = 0;
let urlCalls = 0;
let resetCalls = 0;
let watchPayloads = [];
let watchLoads = 0;
let toasts = [];
let watchFailNext = false;

const sandbox = {
  AppState: {
    query: 'кроссовки Nike',
    city: 'Астана',
    category: 'footwear',
    attributes: { size: '42' },
    page: 2,
  },
  onCityChanged: (c) => { cityCalls.push(c); },
  updateSearchUrl: () => { urlCalls++; },
  executeSearch: () => { searchCalls++; },
  resetAllFilters: () => { resetCalls++; },
  showToast: (m) => { toasts.push(m); },
  WatchManager: {
    async create(payload) {
      if (watchFailNext) {
        watchFailNext = false;
        throw new Error('offline');
      }
      watchPayloads.push(payload);
      return { watch: { id: 1 } };
    },
    async load() { watchLoads++; },
  },
  document: {
    getElementById(id) {
      if (id === 'resultsEmpty') return emptyEl;
      if (id === 'resultsEmptyAction') return emptyAction;
      if (id === 'resultsEmptyExtraActions') return extraBox;
      if (id === 'localSearchHelp') return helpBox;
      return null;
    },
    createElement(tag) {
      return {
        tagName: tag.toUpperCase(),
        attrs: {},
        textContent: '',
        disabled: false,
        onclick: null,
        style: {},
        setAttribute(k, v) { this.attrs[k] = String(v); },
      };
    },
  },
};

vm.runInNewContext(snippet, sandbox);

(async () => {
  // 1. Builds all 4 contextual actions when city, size, filters, and logged-in user are present
  const actions = sandbox.buildEmptySearchActions(
    sandbox.AppState,
    { local_ai: { status: 'disabled' } },
    { id: 10 },
  );
  assert.deepStrictEqual(
    Array.from(actions, a => a.id),
    ['all_cities', 'clear_size', 'reset_filters', 'watch_search'],
  );
  assert(actions[1].label.includes('42'));
  assert(actions[3].label.includes('42'));

  // 2. showResultsMessage renders extra buttons; showResultsError clears them completely
  sandbox.showResultsMessage('Пусто', 'Нет', 'Уточнить', () => {}, actions);
  assert.strictEqual(extraBox.style.display, 'flex');
  assert.strictEqual(extraBox.children.length, 4);

  // Trigger all_cities, clear_size, reset_filters
  extraBox.children[0].onclick();
  assert.deepStrictEqual(cityCalls, ['Все']);

  extraBox.children[1].onclick();
  assert.strictEqual(sandbox.AppState.attributes.size, undefined);
  assert.strictEqual(sandbox.AppState.page, 1);
  assert.strictEqual(urlCalls, 1);
  assert.strictEqual(searchCalls, 1);

  extraBox.children[2].onclick();
  assert.strictEqual(resetCalls, 1);

  // Watch action handles network failure by re-enabling button, then succeeds
  watchFailNext = true;
  await actions[3].run(extraBox.children[3]);
  assert.strictEqual(extraBox.children[3].disabled, false);
  assert.strictEqual(toasts.length, 1);

  await actions[3].run(extraBox.children[3]);
  assert.strictEqual(watchPayloads.length, 1);
  assert.strictEqual(watchPayloads[0].kind, 'search');
  assert.strictEqual(watchPayloads[0].condition, 'size_in_stock');
  assert.strictEqual(watchPayloads[0].target_size, '42');
  assert.strictEqual(watchPayloads[0].city, 'Астана');
  assert.strictEqual(extraBox.children[3].textContent, '✓ Уведомление включено');
  assert.strictEqual(watchLoads, 1);

  // 3. Error state clears stale extra actions
  sandbox.showResultsError('Ошибка', 'Сбой');
  assert.strictEqual(extraBox.style.display, 'none');
  assert.strictEqual(extraBox.children.length, 0);

  // 4. Does not duplicate watch button when localSearchHelp already shows watch_target
  const dedupActions = sandbox.buildEmptySearchActions(
    { query: 'зимняя резина', city: 'Все', attributes: {} },
    { local_ai: { status: 'ok' }, search_help: { watch_target: '@local-search:abc' } },
    { id: 10 },
  );
  assert.deepStrictEqual(Array.from(dedupActions, a => a.id), []);

  // 5. Guest user does not get broken watch button
  const guestActions = sandbox.buildEmptySearchActions(
    { query: 'зимняя резина', city: 'Астана', attributes: {} },
    { local_ai: { status: 'disabled' } },
    null,
  );
  assert.deepStrictEqual(Array.from(guestActions, a => a.id), ['all_cities']);

  // 6. ES-01: наблюдение хранит предел цены, состояние, размер и город в структурированной цели
  watchPayloads.length = 0;
  const es01State = { query: 'кроссовки Nike', city: 'Астана', attributes: { size: '42' },
                      max_price: '30000', min_price: '', condition: 'NEW' };
  const es01 = sandbox.buildEmptySearchActions(es01State, { local_ai: { status: 'disabled' }, total_products: 0 }, { id: 10 });
  const watchAct = es01.find(a => a.id === 'watch_search');
  assert(watchAct, 'watch button expected when nothing found');
  await watchAct.run({ disabled: false, textContent: '' });
  const pl = watchPayloads[0];
  assert(pl.target.startsWith('@local-search:'), 'structured target expected: ' + pl.target);
  const spec = JSON.parse(pl.target.slice('@local-search:'.length));
  assert.strictEqual(spec.max_price, 30000);
  assert.strictEqual(spec.min_price, null);
  assert.strictEqual(spec.query, 'кроссовки Nike');
  assert.strictEqual(spec.attributes.size, '42');
  assert.deepStrictEqual(Array.from(pl.item_conditions), ['new']);
  assert.strictEqual(pl.condition, 'size_in_stock');
  assert.strictEqual(pl.target_size, '42');
  assert.strictEqual(pl.city, 'Астана');

  // 6b. Цель сервера главнее собранной из фильтров; его состояния и город тоже
  const serverTarget = '@local-search:{"query":"nike","max_price":30000}';
  const fromServer = sandbox.buildSearchWatchPayload(es01State,
    { search_help: { watch_target: serverTarget, watch_conditions: ['used'], watch_city: 'Алматы' } });
  assert.strictEqual(fromServer.target, serverTarget);
  assert.deepStrictEqual(Array.from(fromServer.item_conditions), ['used']);
  assert.strictEqual(fromServer.city, 'Алматы');

  // 7. ES-02: «Сбросить фильтры» при каждом одиночном ограничении
  for (const [key, val] of [['min_price', '1000'], ['max_price', '30000'], ['gift', true], ['in_stock', true]]) {
    const ids = Array.from(sandbox.buildEmptySearchActions(
      { query: 'x', city: 'Все', attributes: {}, [key]: val }, { total_products: 0 }, null), a => a.id);
    assert(ids.includes('reset_filters'), 'reset_filters expected for ' + key + ': ' + ids.join(','));
  }
  const noFilter = Array.from(sandbox.buildEmptySearchActions(
    { query: 'x', city: 'Все', attributes: {}, gift: false, in_stock: false, min_price: '', max_price: '' },
    { total_products: 0 }, null), a => a.id);
  assert(!noFilter.includes('reset_filters'), 'no reset without filters');

  // 8. ES-03: пустая страница при ненулевом общем счётчике — кнопки наблюдения нет
  const pageBeyond = Array.from(sandbox.buildEmptySearchActions(
    { query: 'кроссовки', city: 'Все', attributes: {} }, { local_ai: { status: 'disabled' }, total_products: 12 },
    { id: 10 }), a => a.id);
  assert(!pageBeyond.includes('watch_search'), 'no watch when results exist on other pages');

  // 9. ES-03: кнопка помощника создаёт то же наблюдение — размер, цель сервера, состояние
  watchPayloads.length = 0;
  sandbox.currentUser = { id: 10 };
  sandbox.AppState = { query: 'кроссовки Nike', city: 'Астана', attributes: { size: '42' }, max_price: '30000' };
  sandbox.renderLocalSearchHelp({
    total_products: 0, local_ai: { status: 'ok' },
    search_help: { watch_target: serverTarget, watch_conditions: ['new'], category_name: null },
  });
  const aiBtn = helpBox.children.find(el => el.tagName === 'BUTTON');
  assert(aiBtn, 'AI watch button expected');
  assert(aiBtn.textContent.includes('42'), 'AI button shows the size: ' + aiBtn.textContent);
  await aiBtn.onclick();
  const aiPl = watchPayloads[0];
  assert.strictEqual(aiPl.condition, 'size_in_stock');
  assert.strictEqual(aiPl.target_size, '42');
  assert.strictEqual(aiPl.target, serverTarget);
  assert.deepStrictEqual(Array.from(aiPl.item_conditions), ['new']);
  // и при этом пустая выдача не дублирует кнопку
  const dup = Array.from(sandbox.buildEmptySearchActions(sandbox.AppState,
    { total_products: 0, local_ai: { status: 'ok' }, search_help: { watch_target: serverTarget } }, { id: 10 }), a => a.id);
  assert(!dup.includes('watch_search'));
  // пустая страница при ненулевом счётчике: помощник тоже не показывает кнопку
  sandbox.renderLocalSearchHelp({ total_products: 12, local_ai: { status: 'ok' }, search_help: { watch_target: serverTarget } });
  assert(!helpBox.children.some(el => el.tagName === 'BUTTON'), 'AI hides watch button too');

  console.log('PASS: test_frontend_empty_search_actions.cjs (all 9 checks OK)');
})().catch(err => {
  console.error(err);
  process.exit(1);
});
