'use strict';

// S4 actual student-login SSR, offline routes only. Never starts the application.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { chromium } = require('@playwright/test');
const { inspectTextPixels } = require('./contrast_probe.cjs');
const ROOT = path.resolve(__dirname, '../..');
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');

async function run({ output, limit = Infinity, assetRoot = 'static' }) {
  if (!output) throw new Error('Provide --output PATH.');
  const assetPath = path.resolve(ROOT, assetRoot);
  if (!assetPath.startsWith(path.join(ROOT, 'static'))) throw new Error('Assets must be inside static.');
  const manifestBytes = fs.readFileSync(path.join(ROOT, 'static/img/life_tips/manifest.json'));
  const manifest = JSON.parse(manifestBytes);
  const files = manifest.images.map(item => item.file);
  if (!files.length || new Set(files).size !== files.length || files.some(file => typeof file !== 'string' || path.basename(file) !== file || /[\\/]/.test(file))) throw new Error('Invalid manifest.');
  const python = process.env.LQ_TEST_PYTHON || path.join(ROOT, 'venv/Scripts/python.exe');
  const fixture = JSON.parse(execFileSync(python, ['tests/e2e/scripts/render_lq_centered.py'], { cwd: ROOT, encoding: 'utf8' }));
  if (!fixture.isolated) throw new Error('Renderer imported the application.');
  const html = fixture.pages.student_login_v4;
  const bytes = new Map(), sourceHashes = {};
  const selected = files.slice(0, limit), records = [], evidence = [];
  let currentFile = selected[0];
  const origin = 'https://login-page-pixels.test';
  const browser = await chromium.launch({ headless: true, ...(fs.existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? { channel: 'chrome' } : {}) });
  const context = await browser.newContext({ deviceScaleFactor: 1, reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => {
    Object.defineProperty(navigator, 'hardwareConcurrency', { value: 8 });
    Object.defineProperty(navigator, 'deviceMemory', { value: 8 });
  });
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === '/static/img/life_tips/manifest.json') return route.fulfill({ json: { images: [{ file: currentFile, categories: [] }] } });
    if (url.pathname.startsWith('/static/')) {
      const isCode = /^\/static\/(css|js)\//.test(url.pathname);
      const file = path.resolve(isCode ? assetPath : path.join(ROOT, 'static'), '.' + url.pathname.slice('/static'.length));
      const expectedRoot = isCode ? assetPath : path.join(ROOT, 'static');
      if (!file.startsWith(expectedRoot + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: 'Missing local asset' });
      if (!bytes.has(file)) { bytes.set(file, fs.readFileSync(file)); sourceHashes[path.relative(ROOT, file).replaceAll('\\', '/')] = sha(bytes.get(file)); }
      const mime = { '.css': 'text/css', '.js': 'text/javascript', '.webp': 'image/webp', '.jpg': 'image/jpeg', '.png': 'image/png', '.woff2': 'font/woff2' }[path.extname(file)] || 'application/octet-stream';
      return route.fulfill({ contentType: mime, body: bytes.get(file) });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
    return route.fulfill({ status: 404, body: 'Offline probe: application requests are not allowed' });
  });
  const page = await context.newPage();
  async function select(file, appearance, viewport) {
    currentFile = file;
    await page.setViewportSize(viewport);
    await page.evaluate(async appearance => {
      const module = await import('/static/js/login_scene.js');
      (await module.initLoginScene())?.dispose();
      const w = window;
      w.LanShareTheme.applyTheme({ preferences: { palette_key: 'indigo', appearance, glass: 'tinted' }, capabilities: w.LanShareTheme.detectCapabilities(window) });
      document.documentElement.dispatchEvent(new CustomEvent('lq:theme-change', { bubbles: true }));
      const scene = await module.initLoginScene();
      if (!scene) throw new Error('Image did not load.');
      await document.fonts.ready;
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      await Promise.allSettled(document.getAnimations().filter(a => Number.isFinite(a.effect?.getComputedTiming().endTime)).map(a => a.finished));
      window.scrollTo(0, 0);
      document.querySelectorAll('[data-lq-pixel-text]').forEach(el => el.removeAttribute('data-lq-pixel-text'));
      const card = document.querySelector('[data-lq-login-card="student"]');
      if (!card.classList.contains('lq-glass--clear')) throw new Error('Clear was not selected by the actual scene owner.');
      for (const el of card.querySelectorAll('h1,.subtitle,.lq-field__label,.lq-btn__label,.link-button,.footer-links a')) {
        if (!el.closest('[hidden]') && el.getClientRects().length && !el.children.length) el.setAttribute('data-lq-pixel-text', '');
      }
    }, appearance);
  }
  try {
    await page.goto(origin);
    await page.waitForSelector('[data-lq-scene-state="ready"]');
    for (const appearance of ['light', 'dark']) for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 660 }]) {
      for (const file of selected) {
        await select(file, appearance, viewport);
        const result = await inspectTextPixels(page);
        const controls = await page.locator('#student-password-login-form input:not([type=hidden])').evaluateAll(inputs => {
          const values = color => (color.match(/[\d.]+/g) || []).map(Number);
          const lum = color => color.slice(0,3).map(c => c/255).map(c => c <= .04045 ? c/12.92 : ((c+.055)/1.055)**2.4).reduce((s,c,i) => s+c*[.2126,.7152,.0722][i],0);
          return inputs.flatMap(input => {
            const style = getComputedStyle(input), background = values(style.backgroundColor);
            if (background.length > 3 && background[3] !== 1) throw new Error('Input background must be opaque for this check.');
            return [style.color, getComputedStyle(input, '::placeholder').color].map((color,index) => {
              const a=lum(values(color)),b=lum(background),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
              return { id: input.id, kind: index ? 'placeholder' : 'value', color, background: style.backgroundColor, ratio, status: ratio >= 4.5 ? 'passed' : 'failed' };
            });
          });
        });
        const blurs = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(el => {
          if (!el.getClientRects().length || el.closest('[hidden]')) return false;
          const rect=el.getBoundingClientRect(),s=getComputedStyle(el);
          return rect.bottom>0 && rect.top<innerHeight && (s.backdropFilter.includes('blur(')||s.filter.includes('blur('));
        }).map(el=>({tag:el.tagName,className:el.className})));
        const record = { file, sha256: sha(fs.readFileSync(path.join(ROOT, 'static/img/life_tips', file))), appearance, viewport, ...result, controls, blurs };
        if (controls.some(c=>c.status!=='passed') || blurs.length>2) record.status='failed';
        records.push(record);
        if(records.length%40===0) process.stderr.write(`Measured ${records.length}/${selected.length*4}\n`);
      }
    }
    const screens = path.resolve(output.replace(/\.json$/i,'')+'-screens'); fs.mkdirSync(screens,{recursive:true});
    for (const appearance of ['light','dark']) for (const width of [1440,390]) {
      const worst = records.filter(r=>r.appearance===appearance&&r.viewport.width===width).sort((a,b)=>a.minimumRatio-b.minimumRatio)[0];
      await select(worst.file, appearance, worst.viewport);
      const screenshot=path.join(screens,`${appearance}-${width}.png`); await page.screenshot({path:screenshot,fullPage:true});
      evidence.push({file:worst.file,appearance,width,minimumRatio:worst.minimumRatio,screenshot:path.relative(ROOT,screenshot),sha256:sha(fs.readFileSync(screenshot))});
    }
    const report = { measuredAt:new Date().toISOString(),scope:'Actual student_login_v4 Jinja and real scene owner; offline route-only development assets unless an immutable --asset-root is explicitly selected. Not authenticated app, device or success-reveal evidence.',
      assetRoot,sourceHashes,templateSHA256:sha(Buffer.from(html)),manifestSHA256:sha(manifestBytes),manifestCount:files.length,measuredImageCount:selected.length,
      completePool:selected.length===files.length,recordCount:records.length,failedCount:records.filter(r=>r.status!=='passed').length,minimumRatio:Math.min(...records.map(r=>r.minimumRatio)),records,evidence,
      limitations:['Pixel line boxes include actual heading, subtitle, labels, login button and secondary links; input values/placeholders are computed against asserted opaque input surfaces.','Only default password panel; identity/setup/recovery states and real authentication covered separately.','No physical device, user-selected image crop, screenshot antialias threshold, or full-screen life-tip reveal qualification.'] };
    report.status=report.failedCount?'failed':report.completePool?'passed':'partial';
    fs.mkdirSync(path.dirname(path.resolve(output)),{recursive:true}); fs.writeFileSync(output,JSON.stringify(report,null,2));
    console.log(JSON.stringify({...report,records:undefined,sourceHashes:undefined}));
    return report;
  } finally { await browser.close(); }
}

if(require.main===module){
  const options={};
  for(let i=2;i<process.argv.length;i+=2){const [key,value]=process.argv.slice(i,i+2);if(key==='--output')options.output=value;else if(key==='--limit'&&/^\d+$/.test(value))options.limit=Number(value);else if(key==='--asset-root')options.assetRoot=value;else throw new Error('Invalid option '+key);}
  run(options).then(report=>{process.exitCode=report.status==='passed'?0:report.status==='partial'?2:1;}).catch(error=>{console.error(error);process.exitCode=1;});
}
module.exports={run};
