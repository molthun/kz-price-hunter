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
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(m.index, i + 1);
    }
  }
  throw new Error(`Unclosed function ${name}`);
}

assert(html.includes('value="size_in_stock"'), 'Missing size_in_stock radio option');
assert(html.includes('id="watchSizeInputWrapper"'), 'Missing #watchSizeInputWrapper');
assert(html.includes('id="watchTargetSizeInput"'), 'Missing #watchTargetSizeInput');

const snippet = [
  extractFunc(html, 'escapeHtml'),
  extractFunc(html, 'populateWatchSizeQuickChips'),
  extractFunc(html, 'setWatchTargetSize'),
  extractFunc(html, 'onWatchConditionChange'),
  extractFunc(html, 'submitWatchForm'),
].join('\n\n');

const priceWrapper = { style: { display: 'none' } };
const sizeWrapper = { style: { display: 'none' } };
const sizeInput = { value: '' };
const quickBox = { innerHTML: '', hidden: true };
const submitBtn = { disabled: false };
let selectedCondition = 'size_in_stock';
let createdPayloads = [];
let errors = [];
let closed = 0;

const sandbox = {
  AppState: { city: 'Алматы' },
  WatchManager: {
    currentWatchProduct: { target: 'cp_kari_1', title: 'Кроссовки Kari', price: 20000 },
    async create(payload) {
      createdPayloads.push(payload);
      return { watch: { city: 'Алматы', already_existed: false } };
    },
    async load() {},
  },
  getSavedSizesList: () => ['42', 'L', 'рост 104'],
  showWatchFormError: (msg) => { if (msg) errors.push(msg); },
  closeWatchModal: () => { closed++; },
  showToast: () => {},
  document: {
    getElementById(id) {
      if (id === 'targetPriceInputWrapper') return priceWrapper;
      if (id === 'watchSizeInputWrapper') return sizeWrapper;
      if (id === 'watchTargetSizeInput') return sizeInput;
      if (id === 'watchSizeQuickChips') return quickBox;
      if (id === 'watchSubmitBtn') return submitBtn;
      if (id === 'viewProfile') return { classList: { contains: () => false } };
      return null;
    },
    querySelector(sel) {
      if (sel === 'input[name="watchCondition"]:checked') return { value: selectedCondition };
      return null;
    },
  },
};

vm.runInNewContext(snippet, sandbox);

// 1. Quick chips populate from saved sizes and prefill first size
sandbox.populateWatchSizeQuickChips();
assert.strictEqual(quickBox.hidden, false);
assert(quickBox.innerHTML.includes('data-size="42"'));
assert(quickBox.innerHTML.includes('data-size="рост 104"'));
assert.strictEqual(sizeInput.value, '42');

// 2. Switching condition toggles input wrappers properly
sandbox.onWatchConditionChange('size_in_stock');
assert.strictEqual(sizeWrapper.style.display, 'block');
assert.strictEqual(priceWrapper.style.display, 'none');

// 3. Empty size shows validation error and does not submit
sizeInput.value = '   ';
sandbox.submitWatchForm({ preventDefault() {} }).then(async () => {
  assert.strictEqual(errors.length, 1);
  assert.strictEqual(createdPayloads.length, 0);

  // 4. Selecting saved size chip & submitting sends condition=size_in_stock + target_size
  sandbox.setWatchTargetSize('рост 104');
  assert.strictEqual(sizeInput.value, 'рост 104');
  await sandbox.submitWatchForm({ preventDefault() {} });
  assert.strictEqual(createdPayloads.length, 1);
  assert.strictEqual(createdPayloads[0].condition, 'size_in_stock');
  assert.strictEqual(createdPayloads[0].target_size, 'рост 104');
  assert.strictEqual(closed, 1);
  console.log('PASS: test_frontend_size_watch_modal.cjs (all 4 checks OK)');
}).catch(err => {
  console.error(err);
  process.exit(1);
});
