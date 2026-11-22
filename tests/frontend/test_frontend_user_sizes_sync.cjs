// v6.46.1: синхронизация «Моих размеров» (возврат Codex SY-01/SY-02). Исполняются настоящие функции
// шаблона search_first.html в node:vm с управляемыми fetch и localStorage — без браузера.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'web/templates/search_first.html'), 'utf8');
const code = html.slice(html.indexOf('    const USER_SIZES_STORAGE_KEY ='), html.indexOf('    function onCityChanged'));
assert.ok(code.length > 1000, 'фрагмент размеров найден');

function makePage({values = new Map(), user = {id: 1}, blocked = false} = {}) {
  const page = {
    values, blocked, requests: [], server: {}, toasts: [],
    fields: {profileShoeSize: {value: ''}, profileClothingSize: {value: ''}, profileSizesState: {textContent: ''}},
  };
  const ctx = vm.createContext({
    localStorage: {
      getItem: k => page.values.get(k) || null,
      setItem: (k, v) => { if (page.blocked) throw Error('QuotaExceededError'); page.values.set(k, v); },
    },
    document: {getElementById: k => page.fields[k]},
    showToast: m => page.toasts.push(m),
    currentUser: user,
    // Запрос «обрабатывается сервером» только когда тест вызывает его resolve — так задаётся порядок сети
    fetch: (_url, opts) => new Promise(resolve => page.requests.push({
      body: JSON.parse(opts.body),
      ok: () => { page.server = JSON.parse(opts.body); resolve({ok: true, json: async () => ({})}); },
      fail: () => resolve({ok: false, json: async () => ({message: 'HTTP 500'})}),
    })),
  });
  vm.runInContext(code, ctx);
  page.run = expr => vm.runInContext(expr, ctx);
  page.sizes = () => JSON.parse(page.run('JSON.stringify(getUserSizes())'));
  page.change = (shoe, clothing) => {
    page.fields.profileShoeSize.value = shoe;
    page.fields.profileClothingSize.value = clothing;
    return page.run('onUserSizesChanged()');
  };
  return page;
}

const flush = () => new Promise(r => setImmediate(r));

async function sy01_last_change_wins() {
  const p = makePage();
  const first = p.change('43', 'L');
  const second = p.change('43', 'XL');
  await flush();
  assert.equal(p.requests.length, 1, 'второй запрос не уходит, пока первый в пути');
  p.requests[0].ok();
  await flush();
  assert.equal(p.requests.length, 2, 'после ответа уходит последний снимок');
  assert.deepEqual(p.requests[1].body, {preferred_shoe_size: '43', preferred_clothing_size: 'XL'});
  p.requests[1].ok();
  await Promise.all([first, second]);
  assert.deepEqual(p.server, {preferred_shoe_size: '43', preferred_clothing_size: 'XL'});
  assert.deepEqual(JSON.parse(p.values.get('kzph_user_sizes')), {shoe: '43', clothing: 'XL', uid: '1'});
  assert.deepEqual(p.sizes(), {shoe: '43', clothing: 'XL'});
  assert.match(p.fields.profileSizesState.textContent, /одежда XL \(синхронизировано/);
  assert.doesNotMatch(p.fields.profileSizesState.textContent, /одежда L\b/);

  // После перезагрузки восстанавливается последнее значение сервера
  const reloaded = makePage({values: p.values});
  reloaded.run(`syncUserSizesFromServer(${JSON.stringify(p.server)})`);
  assert.deepEqual(reloaded.sizes(), {shoe: '43', clothing: 'XL'});
}

async function sy01_first_request_fails() {
  const p = makePage();
  const first = p.change('43', 'L');
  const second = p.change('44', 'XL');
  await flush();
  p.requests[0].fail();
  await flush();
  assert.equal(p.requests.length, 2);
  p.requests[1].ok();
  await Promise.all([first, second]);
  assert.deepEqual(p.server, {preferred_shoe_size: '44', preferred_clothing_size: 'XL'});
  assert.match(p.fields.profileSizesState.textContent, /обувь 44, одежда XL \(синхронизировано/);
}

async function sy02_blocked_cache_still_updates_screen() {
  const values = new Map([['kzph_user_sizes', JSON.stringify({shoe: '40', clothing: 'S', uid: '1'})]]);
  const p = makePage({values, blocked: true});
  p.run(`syncUserSizesFromServer({preferred_shoe_size: '43', preferred_clothing_size: 'XL'})`);
  assert.deepEqual(p.sizes(), {shoe: '43', clothing: 'XL'}, 'сервер виден экрану без кеша');
  assert.equal(p.fields.profileShoeSize.value, '43');
  assert.equal(p.fields.profileClothingSize.value, 'XL');

  const saving = p.change('44', 'M');
  assert.deepEqual(p.sizes(), {shoe: '44', clothing: 'M'}, 'фильтр и подсветка видят новое значение сразу');
  await flush();
  p.requests[0].ok();
  await saving;
  assert.deepEqual(p.server, {preferred_shoe_size: '44', preferred_clothing_size: 'M'});
  assert.deepEqual(p.sizes(), {shoe: '44', clothing: 'M'});
  assert.match(p.fields.profileSizesState.textContent, /синхронизировано/);
  assert.equal(JSON.parse(values.get('kzph_user_sizes')).shoe, '40', 'кеш недоступен и не менялся');

  // Очистка на сервере очищает экран и при недоступном кеше
  p.run(`syncUserSizesFromServer({preferred_shoe_size: '', preferred_clothing_size: ''})`);
  assert.deepEqual(p.sizes(), {shoe: '', clothing: ''});
}

async function sy02_blocked_cache_without_server() {
  const p = makePage({user: null, blocked: true});
  await p.change('42', 'L');
  assert.deepEqual(p.sizes(), {shoe: '42', clothing: 'L'}, 'гость видит выбор до перезагрузки');
  assert.equal(p.requests.length, 0);
  assert.match(p.fields.profileSizesState.textContent, /хранилище недоступно/);
}

async function sy02_other_account_cache_is_not_shown() {
  const values = new Map([['kzph_user_sizes', JSON.stringify({shoe: '40', clothing: 'S', uid: '1'})]]);
  const other = makePage({values, user: {id: 2}});
  assert.deepEqual(other.sizes(), {shoe: '', clothing: ''}, 'размеры аккаунта 1 не видны аккаунту 2');
  const guest = makePage({values, user: null});
  assert.deepEqual(guest.sizes(), {shoe: '', clothing: ''}, 'после выхода размеры аккаунта не видны гостю');
  const same = makePage({values, user: {id: 1}});
  assert.deepEqual(same.sizes(), {shoe: '40', clothing: 'S'});
  // Сервер аккаунта 2 очищает значения даже при недоступном кеше
  const blocked = makePage({values, user: {id: 2}, blocked: true});
  blocked.run(`syncUserSizesFromServer({preferred_shoe_size: '', preferred_clothing_size: ''})`);
  assert.deepEqual(blocked.sizes(), {shoe: '', clothing: ''});
}

(async () => {
  for (const t of [sy01_last_change_wins, sy01_first_request_fails, sy02_blocked_cache_still_updates_screen,
                   sy02_blocked_cache_without_server, sy02_other_account_cache_is_not_shown]) {
    await t();
    console.log('ok', t.name);
  }
  console.log('PASS test_frontend_user_sizes_sync');
})().catch(e => { console.error(e); process.exit(1); });
