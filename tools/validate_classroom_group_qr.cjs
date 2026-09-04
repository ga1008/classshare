/* node tools/validate_classroom_group_qr.cjs: real API + production hero browser acceptance. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const { chromium, expect } = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const scratch = path.join(root, '.codex-temp');
fs.mkdirSync(scratch, { recursive: true });
const fixture = fs.mkdtempSync(path.join(scratch, 'group-qr-browser-'));
const checks = [];

(async () => {
  let server, browser;
  let serverErrors = '';
  try {
    const port = await new Promise(resolve => {
      const probe = net.createServer();
      probe.listen(0, '127.0.0.1', () => { const port = probe.address().port; probe.close(() => resolve(port)); });
    });
    const base = `http://127.0.0.1:${port}`;
    server = spawn(path.join(root, 'venv/Scripts/python.exe'), [
      'tools/validate_classroom_group_qr.py', '--root', fixture, '--port', String(port),
    ], { cwd: root, windowsHide: true, env: { ...process.env, PYTHONUTF8: '1' }, stdio: ['ignore', 'pipe', 'pipe'] });
    server.stderr.on('data', data => { serverErrors += data; });
    await expect.poll(async () => {
      if (server.exitCode !== null) throw new Error(serverErrors);
      try { return (await fetch(base)).status; } catch { return 0; }
    }, { timeout: 20000 }).toBe(200);
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
    // Stabilize the existing animated hero background for layout measurements.
    const teacher = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const page = await teacher.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    const trigger = page.locator('[data-group-qr-open]');
    const dialog = page.locator('#classroom-group-qr-dialog');
    const save = page.locator('[data-group-qr-save]');
    const description = page.locator('[name="description"]');
    const file = page.locator('[data-group-qr-file]');
    const status = page.locator('#classroom-group-qr-status');
    await expect(trigger).toContainText('点击设置');
    const triggerBox = await trigger.boundingBox();
    const actionsBox = await page.locator('.workspace-hero-actions').boundingBox();
    assert(actionsBox.x + actionsBox.width <= triggerBox.x, 'existing buttons stay to the left of QR');
    await trigger.click();
    await expect(description).toBeEnabled();
    await expect(save).toBeDisabled();
    await expect(page.locator('[data-group-qr-empty]')).toBeVisible();
    const qr = Buffer.from(await (await fetch(base + '/fixture/qr')).arrayBuffer());
    await file.setInputFiles({ name: '班群二维码.png', mimeType: 'image/png', buffer: qr });
    await expect(status).toHaveText('新图片待保存');
    await description.fill('课程通知与答疑群\n入群后请备注姓名。');
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    await expect(page.locator('[data-group-qr-thumbnail]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(trigger).toBeFocused();
    await page.reload();
    await expect(trigger).toContainText('点击放大');
    await trigger.click();
    await expect(description).toHaveValue('课程通知与答疑群\n入群后请备注姓名。');
    checks.push('teacher uploads original QR and description; save, reload, Escape and focus restoration work');

    // Failed writes preserve the draft; the retry reaches the actual API.
    const firstUrl = await page.locator('[data-group-qr-preview]').getAttribute('src');
    await description.fill('保存失败也保留的简介');
    const endpoint = '**/api/classrooms/11/group-qr';
    await page.route(endpoint, route => route.request().method() === 'POST'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: '暂时无法保存，请重试' }) })
      : route.continue());
    await save.click();
    await expect(status).toContainText('暂时无法保存');
    await expect(description).toHaveValue('保存失败也保留的简介');
    await page.unroute(endpoint);
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    assert.equal(await page.locator('[data-group-qr-preview]').getAttribute('src'), firstUrl);
    checks.push('failed save preserves edits; description-only retry preserves image');

    // Two open editing sessions exercise revision conflict against the real DB.
    const other = await teacher.newPage();
    await other.goto(base);
    await other.locator('[data-group-qr-open]').click();
    await expect(other.locator('[name="description"]')).toBeEnabled();
    await other.locator('[name="description"]').fill('另一页面保存的新简介');
    await other.locator('[data-group-qr-save]').click();
    await expect(other.locator('#classroom-group-qr-status')).toHaveText('班群信息已保存');
    await description.fill('旧页面的草稿');
    await save.click();
    await expect(status).toContainText('其他页面更新');
    await expect(description).toHaveValue('旧页面的草稿');
    await expect(page.locator('[data-group-qr-conflict-panel]')).toBeVisible();
    await expect(save).toBeDisabled();
    await page.locator('[data-group-qr-keep-draft]').click();
    await expect(description).toHaveValue('旧页面的草稿');
    await expect(save).toBeEnabled();
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    await other.locator('[name="description"]').fill('第二次更新的简介');
    await other.locator('[data-group-qr-save]').click();
    await expect(other.locator('[data-group-qr-conflict-panel]')).toBeVisible();
    await other.locator('[data-group-qr-use-latest]').click();
    await expect(other.locator('[name="description"]')).toHaveValue('旧页面的草稿');
    await description.fill('另一页面保存的新简介');
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    await expect(description).toHaveValue('另一页面保存的新简介');
    const secondQR = Buffer.from(await (await fetch(base + '/fixture/qr?variant=two')).arrayBuffer());
    await file.setInputFiles({ name: '新二维码.png', mimeType: 'image/png', buffer: secondQR });
    await expect(status).toHaveText('新图片待保存');
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    assert.notEqual(await page.locator('[data-group-qr-preview]').getAttribute('src'), firstUrl);
    checks.push('concurrent edit preserves draft; both conflict recovery choices work; image replacement succeeds');

    const student = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    const studentPage = await student.newPage();
    studentPage.on('pageerror', error => errors.push(error.message));
    await studentPage.goto(base + '/?role=student');
    await studentPage.locator('[data-group-qr-open]').click();
    await expect(studentPage.locator('[data-group-qr-description]')).toHaveText('另一页面保存的新简介');
    assert.equal(await studentPage.locator('[data-group-qr-form], [data-group-qr-upload], [data-group-qr-save]').count(), 0);
    const rejected = await student.request.post(base + '/api/classrooms/11/group-qr', { form: { description: 'student overwrite' } });
    assert.equal(rejected.status(), 403);
    await expect(studentPage.locator('[data-group-qr-preview]')).toBeVisible();
    await expect(studentPage.locator('[data-group-qr-preview]')).toHaveJSProperty('complete', true);
    assert(await studentPage.locator('[data-group-qr-preview]').evaluate(el => el.naturalWidth > 0));
    const download = await student.request.get(base + '/api/classrooms/11/group-qr/image?download=true');
    assert.match(download.headers()['content-disposition'], /attachment;.*\.png/);
    assert.deepEqual(await download.body(), secondQR);
    checks.push('student can enlarge and read description; no editing controls and forged POST rejected');

    const screenshotDir = process.env.QR_SCREENSHOT_DIR;
    if (screenshotDir) fs.mkdirSync(screenshotDir, { recursive: true });
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'teacher-dialog.png') });
    await page.keyboard.press('Escape');
    if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'teacher-hero.png') });
    // Dirty close and backdrop do not silently lose a draft; removal is reversible.
    await trigger.click();
    await expect(description).toBeEnabled();
    await description.fill('未保存的简介');
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-group-qr-discard-panel]')).toBeVisible();
    await page.locator('[data-group-qr-keep]').click();
    await expect(description).toHaveValue('未保存的简介');
    await page.mouse.click(2, 2);
    await expect(page.locator('[data-group-qr-discard-panel]')).toBeVisible();
    await page.locator('[data-group-qr-discard]').click();
    await trigger.click();
    await expect(description).toHaveValue('另一页面保存的新简介');
    await page.locator('[data-group-qr-remove]').click();
    await expect(page.locator('[data-group-qr-preview]')).toBeHidden();
    await page.locator('[data-group-qr-undo-image]').click();
    await expect(page.locator('[data-group-qr-preview]')).toBeVisible();
    await expect(save).toBeDisabled();
    await page.locator('[data-group-qr-remove]').click();
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    await expect(trigger).toContainText('点击设置');
    await studentPage.keyboard.press('Escape');
    await studentPage.locator('[data-group-qr-open]').click();
    await expect(studentPage.locator('[data-group-qr-empty]')).toBeVisible();
    await expect(studentPage.locator('[data-group-qr-description]')).toHaveText('另一页面保存的新简介');
    await expect(studentPage.locator('[data-group-qr-download]')).toBeHidden();
    await file.setInputFiles({ name: '恢复二维码.png', mimeType: 'image/png', buffer: secondQR });
    await expect(status).toHaveText('新图片待保存');
    await save.click();
    await expect(status).toHaveText('班群信息已保存');
    checks.push('Escape and backdrop guard dirty edits; removal can be undone and saved; student sees empty state with description retained');

    await page.keyboard.press('Escape');
    await page.route(endpoint, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '加载失败测试' }) }));
    await trigger.click();
    await expect(page.locator('[data-group-qr-retry]')).toBeVisible();
    await expect(save).toBeDisabled();
    await expect(page.locator('[data-group-qr-preview]')).toBeHidden();
    await page.unroute(endpoint);
    await page.locator('[data-group-qr-retry]').click();
    await expect(description).toBeEnabled();
    await expect(page.locator('[data-group-qr-retry]')).toBeHidden();
    await page.keyboard.press('Escape');
    let held;
    await page.route(endpoint, async route => { held = route; });
    await trigger.click();
    await expect.poll(() => Boolean(held)).toBe(true);
    await expect(description).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await page.unroute(endpoint);
    await trigger.click();
    await expect(description).toBeEnabled();
    await held.fulfill({ contentType: 'application/json', body: JSON.stringify({ image_url: '', description: '过时响应', revision: '' }) }).catch(() => {});
    await expect(description).toHaveValue('另一页面保存的新简介');
    await page.keyboard.press('Escape');
    checks.push('failed loads hide stale QR and retry in place; closing during a slow load prevents stale responses overwriting a reopened dialog');
    for (const width of [1440, 1200, 1024, 768, 720, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) {
        console.error(await page.evaluate(() => ({vw: innerWidth, root: document.documentElement.scrollWidth, body: document.body.scrollWidth})));
        console.error(await page.evaluate(() => Array.from(document.querySelectorAll('body *')).map(el => ({
          tag: el.tagName, cls: el.className, width: el.getBoundingClientRect().width,
          right: el.getBoundingClientRect().right, minWidth: getComputedStyle(el).minWidth,
        })).filter(el => el.right > innerWidth).slice(0, 12)));
        if (screenshotDir) await page.screenshot({ path: path.join(screenshotDir, 'overflow.png') });
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `hero overflow at ${width}`);
      await trigger.click();
      await expect(description).toBeEnabled();
      assert.equal(await dialog.evaluate(el => el.scrollWidth > el.clientWidth), false, `dialog overflow at ${width}`);
      if (screenshotDir && width === 390) await page.screenshot({ path: path.join(screenshotDir, 'teacher-mobile-dialog.png') });
      await page.keyboard.press('Escape');
      if (screenshotDir && width === 390) await page.screenshot({ path: path.join(screenshotDir, 'teacher-mobile-hero.png') });
    }
    assert.deepEqual(errors, []);
    checks.push('desktop/tablet/mobile 320–1440 px have no horizontal overflow; no uncaught browser errors');
    console.log(JSON.stringify({ status: 'passed', checks }, null, 2));
  } finally {
    if (browser) await browser.close();
    if (server && server.exitCode === null) {
      const exited = new Promise(resolve => server.once('exit', resolve));
      server.kill();
      await exited;
    }
    const resolved = path.resolve(fixture);
    if (!resolved.startsWith(path.resolve(scratch) + path.sep)) throw new Error('Unsafe fixture cleanup target');
    fs.rmSync(resolved, { recursive: true, force: true });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
