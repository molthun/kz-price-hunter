import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path.cwd()
html = (root / 'web/templates/search_first.html').read_text()
script = """
async () => {
  const manager = BarcodeScannerManager;
  const canvas = document.createElement('canvas');
  canvas.width = 632;
  canvas.height = 100;
  const ctx = canvas.getContext('2d');
  const L = ['0001101','0011001','0010011','0111101','0100011','0110001','0101111','0111011','0110111','0001011'];
  const code = '4870237159941';
  const parity = 'LGLLGG';
  const inverted = s => s.replace(/[01]/g, x => x === '0' ? '1' : '0');
  const bits = '101' + [...code.slice(1,7)].map((d,i) => parity[i] === 'L' ? L[d] : [...inverted(L[d])].reverse().join('')).join('') + '01010' + [...code.slice(7)].map(d => inverted(L[d])).join('') + '101';
  const paint = data => {
    ctx.fillStyle = 'white'; ctx.fillRect(0,0,632,100);
    ctx.fillStyle = 'black';
    [...data].forEach((b,i) => { if (b === '1') ctx.fillRect(31+i*6,0,6,100); });
  };
  paint(bits);
  const ean = decodeEan13FromCanvas(canvas);
  paint([...bits].reverse().join(''));
  const reversedEan = decodeEan13FromCanvas(canvas);
  const stream = canvas.captureStream(10);
  const track = stream.getVideoTracks()[0];
  let resolveCamera;
  Object.defineProperty(navigator, 'mediaDevices', {value: {
    getUserMedia: () => new Promise(resolve => { resolveCamera = resolve; })
  }, configurable:true});
  const openPromise = manager.open('barcode');
  manager.close();
  resolveCamera(stream);
  const openAfterClose = await openPromise;
  const result = {ean, reversedEan, openAfterClose, modalIsOpen:manager.isOpen, lateTrackState:track.readyState, hiddenCameraAttached:document.querySelector('#scannerVideo').srcObject === stream};
  track.stop();
  manager.close();
  return result;
}
"""
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel='chrome')
    page = browser.new_page(viewport={'width':390,'height':844})
    def route_request(route):
        if route.request.url == 'http://127.0.0.1:18945/':
            return route.fulfill(content_type='text/html',body=html)
        if route.request.url.startswith('http://127.0.0.1:18945/api/'):
            return route.fulfill(json={})
        return route.abort()
    page.route('**/*', route_request)
    page.goto('http://127.0.0.1:18945/',wait_until='domcontentloaded')
    print(json.dumps(page.evaluate(script)))
    browser.close()
