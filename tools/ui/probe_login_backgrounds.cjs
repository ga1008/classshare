'use strict';

// Offline S1 material qualification. This fixture is not the S4 login page.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const { chromium } = require('@playwright/test');
const { inspectTextPixels } = require('./contrast_probe.cjs');

const ROOT = path.resolve(__dirname, '../..');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function run({ output, limit = Infinity, appearances = ['light', 'dark'], scrimToken = null, css = 'static/css/tailwind-app.css' }) {
  if (!output) throw new Error('Provide --output PATH.');
  if (scrimToken && !/^--ls-[a-z0-9-]+$/.test(scrimToken)) throw new Error('The scrim override must be a named --ls- token.');
  const manifestFile = path.join(ROOT, 'static/img/life_tips/manifest.json');
  const manifestBytes = fs.readFileSync(manifestFile);
  const manifest = JSON.parse(manifestBytes);
  const files = manifest.images.map(item => item.file);
  if (!files.length || new Set(files).size !== files.length || files.some(file => typeof file !== 'string' || path.basename(file) !== file || /[\\/]/.test(file))) throw new Error('Invalid or duplicate manifest filenames.');
  const fixture = fs.readFileSync(path.join(ROOT, 'tests/ui/fixtures/lq-login-material.html'));
  const cssFile = path.resolve(ROOT, css);
  if (!cssFile.startsWith(ROOT + path.sep) || path.extname(cssFile) !== '.css') throw new Error('Use a CSS file inside this repository.');
  const compiledCss = fs.readFileSync(cssFile);
  const sources = [path.relative(ROOT, cssFile), 'static/css/lq/tokens.css', 'static/css/lq/materials.css'];
  const sourceHashes = Object.fromEntries(sources.map(file => [file, sha(fs.readFileSync(path.join(ROOT, file)))]));
  const staticRoot = path.join(ROOT, 'static');
  const server = http.createServer((req, res) => {
    try {
      const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      if (pathname === '/fixture') { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); res.end(fixture); return; }
      if (pathname === '/static/css/tailwind-app.css') { res.writeHead(200, { 'content-type': 'text/css' }); res.end(compiledCss); return; }
      const file = path.resolve(staticRoot, '.' + pathname.slice('/static'.length));
      if (!pathname.startsWith('/static/') || !file.startsWith(staticRoot + path.sep) || !fs.statSync(file).isFile()) throw new Error('Not a static fixture asset');
      const actual = fs.realpathSync(file);
      if (!actual.startsWith(fs.realpathSync(staticRoot) + path.sep)) throw new Error('External link');
      const mime = { '.css': 'text/css', '.webp': 'image/webp', '.woff2': 'font/woff2', '.woff': 'font/woff' }[path.extname(file)] || 'application/octet-stream';
      res.writeHead(200, { 'content-type': mime, 'cache-control': 'no-store' }); fs.createReadStream(file).pipe(res);
    } catch { res.writeHead(404); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const chrome = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
  let browser;
  const records = [];
  try {
    browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
    const context = await browser.newContext({ deviceScaleFactor: 1, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    await page.goto(`${origin}/fixture`);
    await page.evaluate(() => document.fonts.ready);
    const material = await page.locator('.specimen-card').evaluate(card => {
      const css = getComputedStyle(card);
      return { backdropFilter: css.backdropFilter, color: css.color, background: css.backgroundColor, clearToken: css.getPropertyValue('--ls-glass-fill-clear').trim() };
    });
    if (!material.clearToken || !material.backdropFilter.includes('blur(')) throw new Error('Built CSS does not contain the active S1 clear material; build before measuring.');
    if (scrimToken) {
      const found = await page.evaluate(token => getComputedStyle(document.documentElement).getPropertyValue(token).trim(), scrimToken);
      if (!found) throw new Error(`Missing scrim token: ${scrimToken}`);
      await page.locator('.specimen-shell').evaluate((shell, token) => shell.style.setProperty('--ls-scrim', `var(${token})`), scrimToken);
    }
    const selected = files.slice(0, limit);
    for (const appearance of appearances) {
      await page.evaluate(value => document.documentElement.dataset.appearance = value, appearance);
      for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(viewport);
        for (const file of selected) {
          await page.locator('.scene-image').evaluate(async (img, url) => { img.src = url; await img.decode(); if (!img.naturalWidth) throw new Error('Image did not decode.'); }, `${origin}/static/img/life_tips/${encodeURIComponent(file)}`);
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const result = await inspectTextPixels(page);
          records.push({ file, sha256: sha(fs.readFileSync(path.join(staticRoot, 'img/life_tips', file))), appearance, viewport, ...result });
          if (records.length % 40 === 0) process.stderr.write(`Measured ${records.length}/${selected.length * appearances.length * 2}\n`);
        }
      }
    }
    const evidence = [];
    const evidenceDir = path.resolve(output.replace(/\.json$/i, '') + '-screens');
    fs.mkdirSync(evidenceDir, { recursive: true });
    for (const appearance of appearances) {
      for (const width of [1440, 390]) {
        const worst = records.filter(record => record.appearance === appearance && record.viewport.width === width).sort((a, b) => a.minimumRatio - b.minimumRatio)[0];
        await page.setViewportSize(worst.viewport);
        await page.evaluate(value => document.documentElement.dataset.appearance = value, appearance);
        await page.locator('.scene-image').evaluate(async (img, url) => { img.src = url; await img.decode(); }, `${origin}/static/img/life_tips/${encodeURIComponent(worst.file)}`);
        const screenshot = path.join(evidenceDir, `${appearance}-${width}.png`);
        await page.screenshot({ path: screenshot, animations: 'disabled' });
        evidence.push({ appearance, width, image: worst.file, minimumRatio: worst.minimumRatio, screenshot: path.relative(ROOT, screenshot) });
      }
    }
    const report = {
      measuredAt: new Date().toISOString(), browser: browser.version(), manifestSha256: sha(manifestBytes), sourceHashes,
      fixtureSha256: sha(fixture), scrimToken, manifestCount: files.length, measuredImageCount: selected.length,
      completePool: selected.length === files.length, appearances, viewports: ['1440x900', '390x844'],
      recordCount: records.length, failedCount: records.filter(r => r.status !== 'passed').length,
      minimumRatio: Math.min(...records.map(r => r.minimumRatio)), records, evidence,
      scope: 'S1 clear-glass/scrim materials on fixed cover crops and specimen text; actual login, inputs, errors, links, image loading and tier fallbacks require S4 page verification.',
    };
    report.status = report.completePool && !report.failedCount ? 'passed' : report.failedCount ? 'failed' : 'partial';
    fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + '\n', 'utf8');
    process.stdout.write(JSON.stringify({ ...report, records: undefined }) + '\n');
    return report;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

if (require.main === module) {
  const options = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i], value = process.argv[i + 1];
    if (key === '--output') options.output = value;
    else if (key === '--limit' && /^\d+$/.test(value) && Number(value) > 0) options.limit = Number(value);
    else if (key === '--appearance' && ['light', 'dark'].includes(value)) options.appearances = [value];
    else if (key === '--scrim-token') options.scrimToken = value;
    else if (key === '--css') options.css = value;
    else throw new Error(`Invalid option ${key}`);
  }
  run(options).then(report => { process.exitCode = report.status === 'passed' ? 0 : report.status === 'partial' ? 2 : 1; }).catch(error => { console.error(error.message); process.exitCode = 2; });
}

module.exports = { run };
