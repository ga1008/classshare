import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher } from '../fixtures/p03';
import fs from 'node:fs';
import path from 'node:path';

let css: string;
function source(file: string): string {
  return fs.readFileSync(file, 'utf8').replace(/@import\s+["']([^"']+)["'];/g,
    (_, relative) => source(path.resolve(path.dirname(file), relative)));
}
test.beforeAll(async () => {
  // Exercise current source without replacing the release's asset graph.
  css = process.env.LQ_CONTROL_BUILT_CSS === '1' ? fs.readFileSync('static/css/tailwind-app.css', 'utf8')
    : (await require('postcss')([require('tailwindcss')(require(path.resolve('tailwind.config.js')))])
      .process(source(path.resolve('static/css/ui-system.src.css')), { from: path.resolve('static/css/ui-system.src.css') })).css;
});

for (const mobile of [false, true]) test.describe(mobile ? 'touch discussion' : 'desktop discussion', () => {
  test.use({ viewport: { width: mobile ? 390 : 1440, height: 1000 }, isMobile: mobile, hasTouch: mobile, reducedMotion: 'reduce' });
  test('history and live messages keep chronology, geometry and reading position', async ({ page }, testInfo) => {
    const fixture = readS3Fixture();
    const seeded = JSON.parse(fs.readFileSync(path.join(fixture.runtimeRoot, 'seed.json'), 'utf8'));
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    if (process.env.LQ_CONTROL_BUILT_CSS !== '1') await page.route('**/static/**', route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/css/tailwind-app.css')) return route.fulfill({ contentType: 'text/css', body: css });
      if (url.pathname.endsWith('/css/classroom_workspace.css')) return route.fulfill({ contentType: 'text/css', body: fs.readFileSync('static/css/classroom_workspace.css') });
      if (url.pathname.endsWith('/js/chat.js')) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync('static/js/chat.js') });
      return route.continue();
    });
    await loginTeacher(page, fixture);
    const preferences = (await (await page.request.get('/api/profile/ui-preferences')).json()).preferences;
    expect((await page.request.patch('/api/profile/ui-preferences', {
      headers: { 'X-UI-Preferences-Context': preferences.context_token },
      data: { appearance: mobile ? 'dark' : 'light', version: preferences.version },
    })).status()).toBe(200);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await page.locator('[data-classroom-activity-tab="discussion"]').click();
    const box = page.locator('#chat-messages');
    await expect(box.locator(`.chat-message[data-message-id="${seeded.currentIds[0]}"]`)).toBeAttached();
    await box.scrollIntoViewIfNeeded();
    const checks: unknown[] = [];
    for (let batch = 0; batch < 2; batch++) {
      await box.evaluate(el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); });
      const loader = page.locator('#chat-history-load-btn');
      await expect(loader).toBeVisible();
      const shape = await loader.evaluate(el => { const s = getComputedStyle(el); return { radius: parseFloat(s.borderRadius), height: el.getBoundingClientRect().height }; });
      expect(shape.radius).toBeGreaterThanOrEqual(shape.height / 2);
      const anchor = await box.evaluate(el => { const first = el.querySelector<HTMLElement>('.chat-message[data-message-id]')!; return { id: first.dataset.messageId, top: first.getBoundingClientRect().top - el.getBoundingClientRect().top }; });
      await loader.click();
      const expectedOld = batch === 0 ? seeded.olderIds[5] : seeded.olderIds[0];
      await expect(box.locator(`.chat-message[data-message-id="${expectedOld}"]`)).toBeAttached();
      await expect(loader).toBeEnabled();
      const result = await box.evaluate((el, previous) => {
        const rows = [...el.querySelectorAll<HTMLElement>('.chat-message[data-message-id]')];
        const anchor = el.querySelector<HTMLElement>(`[data-message-id="${previous.id}"]`)!;
        return { ids: rows.map(row => Number(row.dataset.messageId)), anchorDelta: anchor.getBoundingClientRect().top - el.getBoundingClientRect().top - previous.top,
          scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
          gaps: rows.slice(1).map((row, i) => row.getBoundingClientRect().top - rows[i].getBoundingClientRect().bottom) };
      }, anchor);
      expect(result.ids).toEqual([...result.ids].sort((a, b) => a - b));
      expect(Math.abs(result.anchorDelta)).toBeLessThanOrEqual(2);
      expect(result.scrollHeight).toBeGreaterThan(result.clientHeight);
      expect(Math.min(...result.gaps)).toBeGreaterThan(4);
      checks.push(result);
    }
    const target = box.locator(`.chat-message[data-message-id="${seeded.currentIds[1]}"]`);
    await target.locator('[data-message-action="quote"]').click();
    await expect(page.locator('#chat-quote-preview')).toBeVisible();
    const text = `布局验收 ${mobile ? '手机' : '桌面'} ${Date.now()} 😀`;
    await page.locator('#chat-input').fill(text);
    await page.locator('#chat-form .chat-send-btn').click();
    const live = box.locator('.chat-message').filter({ has: page.locator('.message-content', { hasText: text }) });
    await expect(live).toHaveCount(1);
    await expect(live.locator('.chat-quote-block')).toBeVisible();
    const geometry = await box.evaluate(el => {
      const rows = [...el.querySelectorAll<HTMLElement>('.chat-message[data-message-id]')];
      return rows.map(row => {
        const main = row.querySelector<HTMLElement>('.chat-message-main')!, header = row.querySelector<HTMLElement>('.chat-message-header')!, actions = row.querySelector<HTMLElement>('.chat-message-actions')!;
        const pieces = [...main.children].map(child => child.getBoundingClientRect());
        const style = getComputedStyle(row);
        return { id: row.dataset.messageId, background: style.backgroundColor, backgroundImage: style.backgroundImage,
          actionsInMain: actions.parentElement === main, headerContainsActions: header.contains(actions),
          gaps: pieces.slice(1).map((piece, i) => piece.top - pieces[i].bottom),
          overflow: row.scrollWidth - row.clientWidth };
      });
    });
    for (const row of geometry) {
      expect(row.background).toBe('rgba(0, 0, 0, 0)'); expect(row.backgroundImage).toBe('none');
      expect(row.actionsInMain).toBe(true); expect(row.headerContainsActions).toBe(false);
      expect(Math.min(...row.gaps)).toBeGreaterThanOrEqual(0); expect(row.overflow).toBeLessThanOrEqual(2);
    }
    const composerTools = await page.locator('#chat-form .chat-composer-tool-btn.lq-btn--glass').evaluateAll(buttons => buttons.map(button => {
      const probe = document.createElement('span');
      probe.style.cssText = 'position:absolute;visibility:hidden;background:hsl(var(--lq-control-fill,var(--ls-glass-fill-control)));color:hsl(var(--lq-control-ink,var(--ls-ink)))';
      button.append(probe);
      const actual = getComputedStyle(button), expected = getComputedStyle(probe);
      const rgba = (value: string) => value.match(/[\d.]+/g)!.map(Number);
      const chain: Element[] = [];
      for (let el: Element | null = button; el; el = el.parentElement) chain.unshift(el);
      const compositeFill = chain.reduce((under, el) => {
        const fill = rgba(getComputedStyle(el).backgroundColor), alpha = fill[3] ?? 1;
        return under.map((channel, i) => fill[i] * alpha + channel * (1 - alpha));
      }, [255, 255, 255]);
      const luminance = (rgb: number[]) => rgb.slice(0, 3).map(channel => {
        const value = channel / 255;
        return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
      }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
      const foreground = luminance(rgba(actual.color)), background = luminance(compositeFill);
      const result = { id: button.id, fill: actual.backgroundColor, ink: actual.color, backgroundImage: actual.backgroundImage,
        expectedFill: expected.backgroundColor, expectedInk: expected.color, compositeFill,
        contrastOverCssFill: (Math.max(foreground, background) + .05) / (Math.min(foreground, background) + .05) };
      probe.remove();
      return result;
    }));
    expect(composerTools).toHaveLength(4);
    for (const tool of composerTools) {
      expect(tool.fill).toBe(tool.expectedFill); expect(tool.ink).toBe(tool.expectedInk);
      expect(tool.backgroundImage).toBe('none');
      if (mobile) {
        const channels = tool.fill.match(/[\d.]+/g)!.map(Number);
        expect(channels).toHaveLength(4);
        expect(channels[3]).toBeLessThan(1);
        expect(Math.max(...tool.compositeFill)).toBeLessThan(128);
        expect(tool.contrastOverCssFill).toBeGreaterThanOrEqual(4.5);
      }
    }
    if (mobile) await page.locator('#chat-form').screenshot({ path: testInfo.outputPath('composer-dark.png') });
    await page.locator('#discussion-room').screenshot({ path: testInfo.outputPath('broadcast.png') });

    await page.locator('[data-classroom-message-tab="private"]').click();
    await page.locator('#classroom-private-contact-input').click();
    const contact = page.locator('#classroom-private-contact-list [data-contact-key^="student:"]').first();
    await expect(contact).toBeVisible(); await contact.click();
    const privateText = `私信布局验收 ${Date.now()}：正常发送与长内容换行。`;
    await page.locator('#classroom-private-input').fill(privateText);
    await page.locator('#classroom-private-send-btn').click();
    const privateBubble = page.locator('.classroom-private-message', { hasText: privateText });
    await expect(privateBubble).toBeVisible();
    const privateGeometry = await privateBubble.evaluate(el => ({ overflow: el.scrollWidth - el.clientWidth, background: getComputedStyle(el).backgroundColor }));
    expect(privateGeometry.overflow).toBeLessThanOrEqual(2);
    await page.locator('#discussion-room').screenshot({ path: testInfo.outputPath('private.png') });
    await testInfo.attach('geometry', { body: JSON.stringify({ checks, geometry, composerTools, privateGeometry, errors }, null, 2), contentType: 'application/json' });
    expect(errors).toEqual([]);
  });
});
