// v6.52.1: SZ-06 — выбранный фильтром и сохранённые размеры всегда видны на карточке выдачи,
// где бы они ни стояли в списке; лимит 6 чипов и корректный «+N». Настоящая функция шаблона в node:vm.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'web/templates/search_first.html'), 'utf8');
const start = html.indexOf('    function renderCardSizesHtml(');
const end = html.indexOf('\n    function ', start + 10);
assert.ok(start > 0 && end > start, 'renderCardSizesHtml найдена');
const code = html.slice(start, end);

function render(sizes, {shoe = '', clothing = '', kidsHeight = '', active = ''} = {}) {
  const ctx = vm.createContext({
    getUserSizes: () => ({shoe, clothing, kidsHeight}),
    AppState: {attributes: active ? {size: active} : {}},
    escapeHtml: s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
  });
  vm.runInContext(code, ctx);
  const out = vm.runInContext(`renderCardSizesHtml(${JSON.stringify(sizes)})`, ctx);
  const chips = [...out.matchAll(/data-size="([^"]*)"/g)].map(m => m[1]);
  const active_ = [...out.matchAll(/class="[^"]*\bactive\b[^"]*" data-size="([^"]*)"/g)].map(m => m[1]);
  const more = (out.match(/card-size-more">\+(\d+)</) || [])[1];
  return {chips, active: active_, more: more ? Number(more) : 0};
}

const SEVEN = ['36', '37', '38', '39', '40', '41', '42'];

// Сценарий Codex: профиль 42, выбран 41 — оба видны, активный можно снять
let r = render(SEVEN, {shoe: '42', active: '41'});
assert.ok(r.chips.includes('41'), 'активный 41 виден');
assert.ok(r.chips.includes('42'), 'сохранённый 42 виден');
assert.deepEqual(r.active, ['41']);
assert.equal(r.chips.length, 6);
assert.equal(r.more, 1);
assert.deepEqual(r.chips, ['36', '37', '38', '39', '41', '42'], 'исходный порядок сохраняется');

// Активный в конце длинного списка, сохранённые разбросаны
const TEN = ['35', '36', '37', '38', '39', '40', '41', '42', '43', '44'];
r = render(TEN, {shoe: '43', clothing: 'L', kidsHeight: 'рост 98', active: '44'});
assert.ok(r.chips.includes('44') && r.chips.includes('43'));
assert.equal(r.chips.length, 6);
assert.equal(r.more, 4);
assert.equal(r.chips.length + r.more, TEN.length);

// Приоритет в первых шести — ничего не вытесняется лишним
r = render(SEVEN, {shoe: '37', active: '36'});
assert.deepEqual(r.chips, ['36', '37', '38', '39', '40', '41']);
assert.equal(r.more, 1);

// Короткий список без «+N»; активный, которого нет в списке, не появляется
r = render(['40', '41'], {active: '45'});
assert.deepEqual(r.chips, ['40', '41']);
assert.equal(r.more, 0);
assert.deepEqual(r.active, []);

// Повторы в данных не съедают место и не ломают счётчик
r = render(['40', '40', '41', '42', '43', '44', '45', '46'], {active: '46'});
assert.ok(r.chips.includes('46'));
assert.equal(r.chips.length, 6);
assert.equal(new Set(r.chips).size, 6);
assert.equal(r.more, 1, 'повтор 40 не считается скрытым размером');

// Пусто
assert.equal(render([]).chips.length, 0);

console.log('PASS test_frontend_card_sizes_priority');
