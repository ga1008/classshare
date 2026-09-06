/* Read-only full-template responsive QA against the isolated HTTP service. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8768';
const output = path.resolve(process.argv[3] || '.codex-temp/career-http-qa');
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback only');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const context = await browser.newContext({ reducedMotion: 'reduce' });
  const page = await context.newPage(), checks = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    assert.equal((await (await context.request.get(base + '/__qa__/health')).json()).isolated, true);
    for (const [width, height] of [[360,800], [768,1024], [1440,1000], [720,450]]) {
      await page.setViewportSize({ width, height }); await page.goto(base + '/career-path');
      await page.locator('.career-direction').first().waitFor();
      const layout = await page.evaluate(() => {
        const header = document.getElementById('career-topbar').getBoundingClientRect(), banner = document.getElementById('career-banner').getBoundingClientRect();
        const heading = getComputedStyle(document.querySelector('.career-direction h2'));
        return { headerBottom: header.bottom, bannerTop: banner.top, headingColor: heading.color, horizontalScroll: document.documentElement.scrollWidth > innerWidth };
      });
      assert.ok(layout.headerBottom <= layout.bannerTop, JSON.stringify({ width, layout }));
      assert.equal(layout.horizontalScroll, false); assert.notEqual(layout.headingColor, 'rgb(15, 23, 42)');
      await page.screenshot({ path: path.join(output, 'http-career-' + width + 'x' + height + '.png'), fullPage: true });
      const last = page.locator('.career-direction').last(); await last.locator('button').last().scrollIntoViewIfNeeded();
      const lastBottom = await last.locator('button').last().evaluate(element => element.getBoundingClientRect().bottom);
      const statusTop = await page.locator('#career-task-status').evaluate(element => element.getBoundingClientRect().top);
      assert.ok(lastBottom <= statusTop + 1, `${width}: last=${lastBottom}, status=${statusTop}`);
      const opener = last.locator('button').first(); await opener.focus(); await page.keyboard.press('Enter');
      assert.equal(await page.locator('.career-detail__close').evaluate(element => element === document.activeElement), true);
      await page.keyboard.press('Escape'); assert.equal(await opener.evaluate(element => element === document.activeElement), true);
      await page.getByRole('button', { name: '网络图', exact: true }).click(); await page.locator('.cn-node').first().waitFor();
      await page.screenshot({ path: path.join(output, 'http-graph-' + width + 'x' + height + '.png'), fullPage: true });
      checks.push({ width, height, ...layout, lastButtonVisible: true, keyboardFocusRestored: true });
    }
    assert.deepEqual(errors, []); fs.writeFileSync(path.join(output, 'http-layout-qa.json'), JSON.stringify({ ok: true, checks, pageErrors: errors }, null, 2));
    console.log(JSON.stringify({ ok: true, checks, pageErrors: errors }, null, 2));
  } catch (error) { await page.screenshot({ path: path.join(output, 'http-layout-failure.png') }); throw error; }
  finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
