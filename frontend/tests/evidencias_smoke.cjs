const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const baseUrl = process.env.QA_BASE_URL || 'http://127.0.0.1:8000';
const browserPath = [chromium.executablePath(), 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find(fs.existsSync);
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/yqkAAAAASUVORK5CYII=', 'base64');
const session = {
  id: 'test-session', project_name: 'Proyecto QA', requirement: 'REQ-1', environment: 'UAT',
  source_filename: 'casos.xlsx', case_count: 1, image_count: 1,
  cases: [{
    case_key: 'case-1', case_id: 'CP-01', name: 'Validar alta', status: 'Error',
    source_fields: { 'ID caso': 'CP-01', Estado: 'Error' },
    images: [{ id: 'img-1', filename: 'error.png', title: 'Error inicial', caption: '', category: 'Error' }],
  }],
};
const events = [];

async function run() {
  assert.ok(browserPath, 'Se necesita Chromium, Chrome o Edge para la prueba visual.');
  const browser = await chromium.launch({ headless: true, executablePath: browserPath });
  try {
    for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport, acceptDownloads: true });
      const page = await context.newPage();
      const pageErrors = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.route('**/api/evidence/**', async route => {
        const request = route.request();
        const url = new URL(request.url());
        const endpoint = url.pathname;
        const method = request.method();
        if (endpoint === '/api/evidence/sessions' && method === 'GET') {
          return route.fulfill({ json: { sessions: [session] } });
        }
        if (endpoint === '/api/evidence/sessions/test-session' && method === 'GET') {
          return route.fulfill({ json: { session } });
        }
        if (endpoint.endsWith('/images/img-1') && method === 'GET') {
          return route.fulfill({ body: png, contentType: 'image/png' });
        }
        if (endpoint.endsWith('/images/img-1') && method === 'PATCH') {
          events.push('save-start');
          await new Promise(resolve => setTimeout(resolve, 250));
          const updates = JSON.parse(request.postData());
          Object.assign(session.cases[0].images[0], updates);
          events.push('save-done');
          return route.fulfill({ json: { image: { id: 'img-1', ...updates } } });
        }
        if (endpoint.endsWith('/cases/case-1/images') && method === 'POST') {
          const image = { id: 'img-2', filename: 'extra.png', title: 'Extra', caption: '', category: 'General' };
          session.cases[0].images.push(image);
          return route.fulfill({ json: { image } });
        }
        if (endpoint.endsWith('/images/img-2') && method === 'GET') {
          return route.fulfill({ body: png, contentType: 'image/png' });
        }
        if (endpoint.endsWith('/report') && method === 'POST') {
          events.push('report');
          return route.fulfill({ body: Buffer.from('test-docx'), contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', headers: { 'Content-Disposition': 'attachment; filename="Evidencias_QA.docx"' } });
        }
        return route.fulfill({ status: 404, json: { detail: 'Ruta simulada no implementada' } });
      });
      await page.goto(`${baseUrl}/app/evidencias.html`);
      await page.locator('#savedSessions').selectOption('test-session');
      await page.locator('.ev-case summary').click();
      assert.equal(await page.locator('.ev-case').getAttribute('open'), '');
      if (viewport.width === 1280) {
        await page.locator('[data-image-title]').first().fill('Título corregido');
        const download = page.waitForEvent('download');
        await page.locator('#generateReport').click();
        await download;
        assert.ok(events.indexOf('save-done') >= 0 && events.indexOf('save-done') < events.indexOf('report'), 'El Word esperó el guardado');
        await page.locator('[data-add-images]').setInputFiles({ name: 'extra.png', mimeType: 'image/png', buffer: png });
        await page.locator('.ev-image-row').nth(1).waitFor();
        assert.equal(await page.locator('.ev-case').getAttribute('open'), '', 'El caso permanece abierto luego de agregar una captura');
      }
      const dimensions = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
      assert.ok(dimensions.scroll <= dimensions.width, `Sin scroll horizontal: ${JSON.stringify({ viewport, dimensions })}`);
      assert.deepEqual(pageErrors, [], 'La pantalla no debe lanzar errores de JavaScript');
      const screenshot = path.join(os.tmpdir(), `evidencias-${viewport.width}.png`);
      await page.screenshot({ path: screenshot, fullPage: true });
      console.log(`${viewport.width}px OK, screenshot: ${screenshot}`);
      await context.close();
    }
  } finally {
    await browser.close();
  }
}

run().catch(error => { console.error(error); process.exitCode = 1; });
