// Сканер штрихкодов (A04, A08, аудит 6.31.0). Офлайн, камера — синтетический поток из Canvas.
// Проверяет: встроенный декодер EAN-13 (прямой и перевёрнутый кадр); поток, полученный после
// закрытия сканера, останавливается и не подключается к скрытому видео; при быстром повторном
// открытии активна одна камера; обычное закрытие освобождает камеру.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'web/templates/search_first.html'), 'utf8');
const BASE = 'http://127.0.0.1:18946';

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, locale: 'en-US' });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.origin !== BASE) return route.abort();
      if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      return route.fulfill({ contentType: 'application/json', body: '{}' });
    });
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof BarcodeScannerManager === 'object' && typeof decodeEan13FromCanvas === 'function');

    const r = await page.evaluate(async () => {
      const manager = BarcodeScannerManager;
      const makeStream = () => {
        const canvas = document.createElement('canvas');
        canvas.width = 632; canvas.height = 100;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 632, 100);
        return canvas.captureStream(10);
      };
      // 1. Декодер EAN-13
      const canvas = document.createElement('canvas');
      canvas.width = 632; canvas.height = 100;
      const ctx = canvas.getContext('2d');
      const L = ['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
      const code = '4870237159941';
      const parity = 'LGLLGG';
      const inv = s => s.replace(/[01]/g, x => x === '0' ? '1' : '0');
      const bits = '101' + [...code.slice(1, 7)].map((d, i) => parity[i] === 'L' ? L[d] : [...inv(L[d])].reverse().join('')).join('')
        + '01010' + [...code.slice(7)].map(d => inv(L[d])).join('') + '101';
      const paint = data => {
        ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 632, 100);
        ctx.fillStyle = 'black';
        [...data].forEach((b, i) => { if (b === '1') ctx.fillRect(31 + i * 6, 0, 6, 100); });
      };
      paint(bits);
      const ean = decodeEan13FromCanvas(canvas);
      paint([...bits].reverse().join(''));
      const reversedEan = decodeEan13FromCanvas(canvas);

      // 2. Закрытие до выдачи потока
      let pending = [];
      Object.defineProperty(navigator, 'mediaDevices', { value: {
        getUserMedia: () => new Promise(resolve => pending.push(resolve))
      }, configurable: true });
      const late = makeStream();
      const lateTrack = late.getVideoTracks()[0];
      const openPromise = manager.open('barcode');
      manager.close();
      pending.shift()(late);
      const openAfterClose = await openPromise;
      const video = document.querySelector('#scannerVideo');
      const lateResult = { openAfterClose, modalIsOpen: manager.isOpen, lateTrackState: lateTrack.readyState,
                           hiddenCameraAttached: video.srcObject === late, managerStream: !!manager.stream };

      // 3. Быстрое повторное открытие: первый поток приходит позже второго
      const s1 = makeStream(), s2 = makeStream();
      const p1 = manager.open('barcode');
      const p2 = manager.open('barcode');
      pending[1](s2);
      const second = await p2;
      pending[0](s1);
      const first = await p1;
      pending = [];
      const reopen = { first, second, s1: s1.getVideoTracks()[0].readyState, s2: s2.getVideoTracks()[0].readyState,
                       attached: video.srcObject === s2, isOpen: manager.isOpen };

      // 4. Обычное закрытие освобождает камеру
      manager.close({ target: document.getElementById('scannerModalBackdrop') });
      const afterClose = { s2: s2.getVideoTracks()[0].readyState, srcObject: video.srcObject, isOpen: manager.isOpen };
      return { ean, reversedEan, lateResult, reopen, afterClose };
    });

    assert.equal(r.ean, '4870237159941');
    assert.equal(r.reversedEan, '4870237159941');
    assert.deepEqual(r.lateResult, { openAfterClose: false, modalIsOpen: false, lateTrackState: 'ended',
                                     hiddenCameraAttached: false, managerStream: false },
                     'поздний поток после закрытия должен быть остановлен');
    assert.equal(r.reopen.second, true);
    assert.equal(r.reopen.first, false);
    assert.equal(r.reopen.s1, 'ended', 'устаревшая попытка освобождает свою камеру');
    assert.equal(r.reopen.s2, 'live');
    assert.equal(r.reopen.attached, true);
    assert.equal(r.afterClose.s2, 'ended');
    assert.equal(r.afterClose.srcObject, null);
    assert.equal(r.afterClose.isOpen, false);
    assert.equal(errors.length, 0, 'Ошибки страницы: ' + errors.join('; '));
    console.log('✓ Barcode scanner camera lifecycle (A04) passed');
  } finally {
    await browser.close();
  }
})().catch(err => {
  console.error('FAIL:', err);
  process.exit(1);
});
