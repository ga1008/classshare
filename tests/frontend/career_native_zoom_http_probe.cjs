/* 390px reflow and native Chrome 200% zoom; real HTTP/full templates, no Office.
 * Native zoom uses Chrome's own Settings control in a disposable profile.
 * No CSS zoom, deviceScaleFactor, or setPageScaleFactor is used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8774';
const output = path.resolve(process.argv[3] || '.codex-temp/career-native-zoom-qa');
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback only');
fs.mkdirSync(output, { recursive: true });
const profile = path.join(output, 'chrome-profile');
if (fs.existsSync(profile)) throw new Error('Native zoom requires a fresh, private QA profile');
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
(async () => {
  let browser, context, nativeContext, settings, nativePage, initialZoom, restored = false;
  const checks = [], errors = [], captures = [], zoom = {};
  const watch = page => page.on('pageerror', error => errors.push({ url: page.url(), message: error.message }));
  const capture = async (page, name) => {
    const filename = path.join(output, name + '.png');
    if (page.context() === nativeContext) {
      // Native browser zoom changes CSS-to-DIP conversion. Request the actual
      // compositor viewport without Playwright's CSS-sized full-page clip.
      const cdp = await nativeContext.newCDPSession(page);
      try {
        await page.bringToFront();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const result = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
        fs.writeFileSync(filename, Buffer.from(result.data, 'base64'));
      } finally { await cdp.detach(); }
    } else await page.screenshot({ path: filename, fullPage: true });
    captures.push(filename);
  };
  async function json(ctx, route, options = {}) {
    const response = await ctx.request.fetch(base + route, options);
    assert.equal(response.ok(), true, `${route}: ${response.status()} ${await response.text()}`);
    return response.json();
  }
  async function geometry(page) {
    const cdp = await page.context().newCDPSession(page);
    try {
      const metrics = await cdp.send('Page.getLayoutMetrics');
      return { ...await page.evaluate(() => ({ outerWidth, outerHeight, innerWidth, innerHeight, devicePixelRatio,
        visualViewportScale: visualViewport.scale, visualViewportWidth: visualViewport.width,
        documentWidth: document.documentElement.scrollWidth, rootCssZoom: getComputedStyle(document.documentElement).zoom })),
        cdpCssVisualViewport: metrics.cssVisualViewport, cdpCssLayoutViewport: metrics.cssLayoutViewport };
    } finally { await cdp.detach(); }
  }
  async function assertCareer(page, label) {
    await page.goto(base + '/career-path');
    await page.locator('.career-direction').first().waitFor();
    const box = await geometry(page);
    assert.ok(box.documentWidth <= box.innerWidth + 1, JSON.stringify(box));
    const layout = await page.evaluate(() => ({ headerBottom: document.getElementById('career-topbar').getBoundingClientRect().bottom,
      bannerTop: document.getElementById('career-banner').getBoundingClientRect().top }));
    assert.ok(layout.headerBottom <= layout.bannerTop + 1, JSON.stringify(layout));
    await capture(page, label + '-career-list');
    const last = page.locator('.career-direction').last(), lastAction = last.getByRole('button').last();
    await lastAction.focus(); await lastAction.scrollIntoViewIfNeeded();
    const lastBox = await lastAction.boundingBox(), statusBox = await page.locator('#career-task-status').boundingBox();
    assert.ok(lastBox.y + lastBox.height <= statusBox.y + 1);
    await capture(page, label + '-career-last-card');
    const returnScrollTop = await page.locator('#career-stage').evaluate(el => el.scrollTop);
    const opener = last.getByRole('button').first();
    await opener.focus(); await page.keyboard.press('Enter');
    assert.equal(await page.locator('.career-detail__close').evaluate(el => el === document.activeElement), true);
    await capture(page, label + '-career-detail');
    const detailPosition = await page.locator('.career-detail__close').evaluate(el => {
      const r = el.getBoundingClientRect(), hit = document.elementFromPoint(r.left+r.width/2, r.top+r.height/2);
      return { top: r.top, bottom: r.bottom, viewportHeight: innerHeight, stageScrollTop: document.getElementById('career-stage').scrollTop,
        visibleHit: el === hit || el.contains(hit), hit: hit?.outerHTML.slice(0,200), left:r.left, right:r.right };
    });
    assert.ok(detailPosition.top >= 0 && detailPosition.bottom <= detailPosition.viewportHeight && detailPosition.visibleHit,
      'Focused detail close must actually be visible: ' + JSON.stringify(detailPosition));
    await page.keyboard.press('Escape');
    assert.equal(await opener.evaluate(el => el === document.activeElement), true);
    assert.ok(Math.abs(await page.locator('#career-stage').evaluate(el => el.scrollTop) - returnScrollTop) <= 1,
      'Closing details must restore the original list position');
    await page.getByRole('button', { name: '网络图', exact: true }).click();
    await page.locator('.cn-node').first().waitFor();
    await capture(page, label + '-career-graph');
    checks.push({ label, surface: 'career', geometry: box, headerClear: true, lastActionAboveStatus: true, keyboardFocusRestored: true });
    return box;
  }
  async function assertResume(page, rid, label) {
    await page.goto(base + '/resume/builder?' + (rid ? 'edit=' + rid : 'auto=1&target=' + encodeURIComponent('英语教师')));
    await page.locator('#rzZones .rz-chip').first().waitFor();
    const box = await geometry(page);
    assert.ok(box.documentWidth <= box.innerWidth + 1, JSON.stringify(box));
    const opener = page.getByRole('button', { name: '编辑本份文字', exact: true });
    await opener.focus(); await page.keyboard.press('Enter');
    const modal = page.getByRole('dialog'); await modal.waitFor();
    await page.waitForFunction(() => document.querySelector('.rz-modal.show')?.contains(document.activeElement));
    assert.equal(await modal.evaluate(el => el.contains(document.activeElement)), true);
    assert.equal(await modal.evaluate(el => el.scrollWidth > el.clientWidth + 1), false);
    await capture(page, label + '-resume-edit');
    const action = modal.getByRole('button', { name: '应用到当前草稿', exact: true });
    const actionHits = await action.evaluate(el => {
      const r = el.getBoundingClientRect();
      return [[.25,.25],[.75,.25],[.25,.75],[.75,.75]].map(([x,y]) => {
        const hit = document.elementFromPoint(r.left+r.width*x, r.top+r.height*y);
        return { owned: el === hit || el.contains(hit), actual: hit?.textContent.trim().slice(0,60), className: hit?.className };
      });
    });
    assert.equal(actionHits.every(hit => hit.owned), true, 'Modal apply button is covered: ' + JSON.stringify(actionHits));
    await page.keyboard.press('Escape');
    await modal.waitFor({ state: 'detached' });
    assert.equal(await opener.evaluate(el => el === document.activeElement), true);
    await page.locator('#rzResumeTitle').fill(label + '手工草稿');
    const [saved] = await Promise.all([page.waitForResponse(r => /\/api\/resume\/resumes(?:\/\d+)?$/.test(r.url()) && ['POST', 'PUT'].includes(r.request().method())),
      page.getByRole('button', { name: '保存草稿', exact: true }).click()]);
    assert.equal(saved.status(), 200);
    if (!rid) { await page.waitForURL(/edit=\d+/); rid = Number(new URL(page.url()).searchParams.get('edit')); }
    assert.equal((await json(page.context(), '/api/resume/resumes/' + rid)).resume.title, label + '手工草稿');
    await capture(page, label + '-resume-saved');
    checks.push({ label, surface: 'resume', geometry: box, modalNoHorizontalOverflow: true, keyboardFocusRestored: true, revisionSaveSucceeded: true });
    return rid;
  }
  try {
    browser = await chromium.launch({ headless: true, executablePath: chrome });
    context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
    const page = await context.newPage(); watch(page);
    const startup = await json(context, '/__qa__/health'); assert.equal(startup.fixed_code, true);
    await page.goto(base + '/career-path');
    if ((await json(context, '/api/career-path/state')).session_status !== 'ready') {
      await page.getByRole('button', { name: /快速测评/ }).click();
      const questions = (await json(context, '/api/career-path/questions?mode=quick')).questions;
      for (const question of questions) {
        await page.getByText(question.title, { exact: true }).waitFor();
        await page.locator('#career-opts button').first().click();
        if (question.kind === 'multi') await page.locator('#career-confirm').click();
      }
      await page.getByRole('button', { name: '提交并查看方向' }).click();
    }
    await page.locator('.career-direction').first().waitFor();
    await json(context, '/__qa__/drain', { method: 'POST' });
    await assertCareer(page, '390px');
    const rid = await assertResume(page, null, '390px');
    await browser.close(); browser = null;

    nativeContext = await chromium.launchPersistentContext(profile, { headless: true, executablePath: chrome,
      viewport: null, reducedMotion: 'reduce', args: ['--window-size=1440,1000'] });
    nativePage = nativeContext.pages()[0] || await nativeContext.newPage(); watch(nativePage);
    settings = await nativeContext.newPage();
    await settings.goto('chrome://settings/appearance');
    const control = settings.locator('#zoomLevel'); await control.waitFor();
    initialZoom = await control.inputValue(); assert.equal(Number(initialZoom), 1);
    zoom.initialControlValue = initialZoom;
    await nativePage.goto(base + '/career-path'); await nativePage.locator('.career-direction').first().waitFor();
    zoom.at100 = await geometry(nativePage);
    await control.selectOption('2');
    await settings.reload(); await control.waitFor();
    assert.equal(Number(await control.inputValue()), 2);
    zoom.selectedControlValue = await control.inputValue();
    zoom.selectedControlLabel = await control.locator('option:checked').innerText();
    await control.scrollIntoViewIfNeeded();
    await capture(settings, 'chrome-settings-native-200');
    zoom.at200 = await assertCareer(nativePage, 'native-200pct');
    assert.equal(zoom.at200.cdpCssVisualViewport.zoom, 2, 'Chrome CDP must report native page zoom factor 2');
    assert.equal(zoom.at200.visualViewportScale, 1, 'No pinch/page scale emulation');
    assert.equal(zoom.at200.outerWidth, zoom.at100.outerWidth, 'Same native browser window width');
    assert.equal(zoom.at200.outerHeight, zoom.at100.outerHeight, 'Same native browser window height');
    assert.ok(Math.abs(zoom.at100.innerWidth / zoom.at200.innerWidth - 2) < .02);
    assert.ok(Math.abs(zoom.at200.devicePixelRatio / zoom.at100.devicePixelRatio - 2) < .02);
    assert.ok(['1', 'normal'].includes(zoom.at200.rootCssZoom));
    await assertResume(nativePage, rid, 'native-200pct');
    await settings.goto('chrome://settings/appearance'); await control.waitFor();
    await control.selectOption(initialZoom); await settings.reload();
    assert.equal(Number(await control.inputValue()), 1);
    zoom.restoredControlValue = await control.inputValue();
    await nativePage.goto(base + '/career-path'); await nativePage.locator('.career-direction').first().waitFor();
    zoom.restored = await geometry(nativePage);
    assert.equal(zoom.restored.cdpCssVisualViewport.zoom, 1); restored = true;
    await control.scrollIntoViewIfNeeded();
    await capture(settings, 'chrome-settings-restored-100');
    const ending = await json(nativeContext, '/__qa__/health');
    assert.equal(ending.fixed_code, true, JSON.stringify(ending.changed_files));
    assert.equal(ending.source_fingerprint, startup.source_fingerprint);
    assert.deepEqual(errors, []);
    const report = { ok: true, checks, zoom, pageErrors: errors, screenshots: captures, office_used: false,
      native_zoom_method: 'Isolated branded Chrome profile, actual chrome://settings/appearance #zoomLevel control; reloaded Settings readback and Page.getLayoutMetrics cssVisualViewport.zoom confirm 2. viewport:null; no CSS/device/page-scale emulation.',
      browser_version: nativeContext.browser().version(), profile, native_zoom_restored: restored,
      source_fingerprint: ending.source_fingerprint, fixed_code: ending.fixed_code, source_manifest: ending.startup_manifest,
      test_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex') };
    await nativeContext.close(); nativeContext = null; report.browser_closed = true;
    fs.writeFileSync(path.join(output, 'native-zoom-http-qa.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ...report, checks: checks.map(({ label, surface }) => ({ label, surface })), screenshots: captures.length }, null, 2));
  } catch (error) {
    if (nativePage && !nativePage.isClosed()) await capture(nativePage, 'native-zoom-failure');
    if (settings && !settings.isClosed()) await capture(settings, 'settings-failure');
    console.error(JSON.stringify({ error: error.message, zoom, pageErrors: errors }, null, 2)); throw error;
  } finally {
    if (settings && !settings.isClosed() && initialZoom !== undefined && !restored) {
      try { await settings.goto('chrome://settings/appearance'); await settings.locator('#zoomLevel').selectOption(initialZoom); } catch (_) {}
    }
    if (nativeContext) await nativeContext.close();
    if (browser) await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
