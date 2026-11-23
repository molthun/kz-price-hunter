const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const htmlPath = path.resolve(__dirname, '../../web/templates/search_first.html');
const html = fs.readFileSync(htmlPath, 'utf8');

function extractFunc(src, name) {
  const re = new RegExp(`(?:async\\s+)?function\\s+${name}\\s*\\(`);
  const m = re.exec(src);
  if (!m) throw new Error(`Function ${name} not found in search_first.html`);
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

// Check markup and CSS exist
assert(html.includes('id="dealsMySizesBtn"'), 'Missing #dealsMySizesBtn button in deals filter bar');
assert(html.includes('.deal-my-sizes-pill.active'), 'Missing .deal-my-sizes-pill.active CSS');
assert(html.includes('.deal-sizes-row'), 'Missing .deal-sizes-row CSS');

const snippet = [
  extractFunc(html, 'escapeHtml'),
  extractFunc(html, 'getSavedSizesList'),
  extractFunc(html, 'getSavedSizesForKind'),
  extractFunc(html, 'renderDealSizesRow'),
  extractFunc(html, 'updateDealsMySizesButton'),
  extractFunc(html, 'toggleDealsMySizes'),
  extractFunc(html, 'resetDealsFilters'),
].join('\n\n');

const btnState = {
  classes: new Set(),
  attrs: {},
  textContent: '',
  title: '',
  classList: {
    toggle(cls, val) {
      if (val) btnState.classes.add(cls);
      else btnState.classes.delete(cls);
    },
  },
  setAttribute(k, v) { btnState.attrs[k] = String(v); },
};

let loadCalls = 0;
let navCalls = [];
let toastCalls = [];
let savedSizes = { shoe: '42', clothing: 'L', kidsHeight: 'рост 104' };

const sandbox = {
  DealsFeedState: { type: 'all', minDiscount: 0, sort: 'random', category: 'Все', mySizesOnly: false },
  getUserSizes: () => ({ ...savedSizes }),
  loadDealsFeed: () => { loadCalls++; },
  showToast: (msg) => { toastCalls.push(msg); },
  navigateTo: (p) => { navCalls.push(p); },
  document: {
    getElementById: (id) => (id === 'dealsMySizesBtn' ? btnState : null),
    querySelectorAll: () => [],
  },
};

vm.runInNewContext(snippet, sandbox);

// 1. Button shows saved sizes and aria-pressed="false" initially
sandbox.updateDealsMySizesButton();
assert.strictEqual(btnState.attrs['aria-pressed'], 'false');
assert(!btnState.classes.has('active'));
assert(btnState.textContent.includes('42, L, рост 104'), 'Button should list saved sizes: ' + btnState.textContent);

// 2. Toggle ON activates filter and triggers loadDealsFeed
sandbox.toggleDealsMySizes();
assert.strictEqual(sandbox.DealsFeedState.mySizesOnly, true);
assert.strictEqual(btnState.attrs['aria-pressed'], 'true');
assert(btnState.classes.has('active'));
assert.strictEqual(loadCalls, 1);

// 3. renderDealSizesRow prioritizes saved size beyond 6 and marks is-my-size
const rowHtml = sandbox.renderDealSizesRow(['36', '37', '38', '39', '40', '41', '42', '43']);
assert(rowHtml.includes('deal-sizes-row'), 'Missing deal-sizes-row wrapper');
assert(rowHtml.includes('offer-size-chip is-my-size'), 'Saved size 42 must be highlighted with is-my-size');
assert(rowHtml.includes('>42<'), 'Saved size 42 must be visible even beyond index 5');
assert(rowHtml.includes('+2'), 'Hidden count must be +2');

// 3b. DS-01: shoe 42 is highlighted only on shoes, never on a jacket with numeric size 42
const shoeRow = sandbox.renderDealSizesRow(['40', '42', '44'], 'shoe');
const mineChip = (row, sz) => new RegExp('class="offer-size-chip is-my-size"[^>]*>' + sz + '<').test(row);
assert(mineChip(shoeRow, '42'), 'Shoe 42 must be highlighted on a shoe deal: ' + shoeRow);
const jacketRow = sandbox.renderDealSizesRow(['40', '42', '44'], 'apparel');
assert(!jacketRow.includes('is-my-size'), 'Shoe 42 must NOT be highlighted on a jacket 42: ' + jacketRow);
const apparelRow = sandbox.renderDealSizesRow(['M', 'L', 'рост 104'], 'apparel');
assert(mineChip(apparelRow, 'L') && mineChip(apparelRow, 'рост 104') && !mineChip(apparelRow, 'M'), 'Clothing L and height 104 highlighted on apparel: ' + apparelRow);
const shoeLetters = sandbox.renderDealSizesRow(['L'], 'shoe');
assert(!shoeLetters.includes('is-my-size'), 'Clothing L must NOT be highlighted on a shoe deal');
// 3c. DS-01: the feed sends profile sizes by kind, not one mixed list
assert(html.includes("params.set('shoe_size'") && html.includes("params.set('clothing_size'") && html.includes("params.set('kids_height'"),
  'Deals feed must send shoe_size/clothing_size/kids_height');
assert(!html.includes("params.set('sizes', savedSizes.join(','))"), 'Deals feed must not send profile sizes as one mixed list');
assert(html.includes('renderDealSizesRow(deal.available_sizes, deal.size_kind)'), 'Deal card must pass size_kind to the highlight');

// 4. resetDealsFilters clears mySizesOnly
sandbox.resetDealsFilters();
assert.strictEqual(sandbox.DealsFeedState.mySizesOnly, false);
assert.strictEqual(btnState.attrs['aria-pressed'], 'false');
assert(!btnState.classes.has('active'));
assert.strictEqual(loadCalls, 2);

// 5. Clicking with empty saved sizes guides user to /profile without enabling filter
savedSizes = { shoe: '', clothing: '', kidsHeight: '' };
sandbox.updateDealsMySizesButton();
assert.strictEqual(btnState.textContent, '👟 Мои размеры');
sandbox.toggleDealsMySizes();
assert.strictEqual(sandbox.DealsFeedState.mySizesOnly, false);
assert.strictEqual(navCalls[0], '/profile');
assert.strictEqual(toastCalls.length, 1);

console.log('PASS: test_frontend_deals_my_sizes.cjs (all 7 checks OK)');
