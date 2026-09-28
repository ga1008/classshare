import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { serveScheduleModule, settleScheduleMotion } from './schedule-fixture-modules';

// No app server, credentials or database. Use the release CSS cascade, including
// shared motion tokens, while exercising the real portable schedule controller.
async function mountStack(page: Page, {
  appearance = 'light', motion = 'standard', weeks = 20, nestedHost = false,
}: { appearance?: string; motion?: string; weeks?: number; nestedHost?: boolean } = {}) {
  const errors: string[] = [];
  const unexpected: string[] = [];
  const nativeMount = nestedHost
    ? fs.readFileSync('templates/manage/course_schedule.html', 'utf8')
      .match(/<div\b[^>]*\bdata-cs-deck[^>]*><\/div>/)![0].replace('<div ', '<div id="deck" ')
    : '<div id="deck"></div>';
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://schedule-stack.test') {
      unexpected.push(url.href);
      await route.abort();
      return;
    }
    if (url.pathname === '/release.css') {
      await route.fulfill({ contentType: 'text/css', body: fs.readFileSync('static/css/tailwind-app.css') });
      return;
    }
    if (await serveScheduleModule(route)) return;
    if (url.pathname !== '/') {
      unexpected.push(url.href);
      await route.abort();
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-lq-motion="${motion}">
      <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/release.css">
      <style>body{margin:0;padding:16px;background:hsl(var(--ls-surface-0));color:hsl(var(--ls-ink))}#outside{margin-bottom:12px}</style>
      <button id="outside" type="button">课表外操作</button>${nativeMount}
      <script type="module">
      import {createScheduleDeck} from '/deck.js';
      window.navigations=[];
      window.makeOverview=(count=${weeks})=>({
        selected_term:{year:'2026-2027',term:'1',label:'第一学期',focus_week:5},
        section_range:{min:1,max:11},
        weeks:Array.from({length:count},(_,index)=>({week_index:index+1,label:'第'+(index+1)+'周',is_current:index===4,
          lessons:[{event_key:'lesson-'+(index+1),course_name:'第'+(index+1)+'周课程',weekday:1,sections:[2,3],hours:2,
            class_label:'计算机2601班',classroom:'知新楼B416-1',classroom_url:'/classroom/'+(index+1)},
          {event_key:'change-'+(index+1),course_name:'第'+(index+1)+'周换教室课程',weekday:4,sections:[6,7],hours:2,
            class_label:'计算机2601班',classroom:'知新楼B210',classroom_url:'/classroom/'+(index+1)+'?session_id=2',
            adjustment:{request_id:'room-'+(index+1),kind:'room',phase:'pending',endpoint:'original',
              original:{date:'2026-09-01',sections:[6,7],room:'B416-1'},proposed:{date:'2026-09-01',sections:[6,7],room:'B210'}}}]
        }))
      });
      window.overview=window.makeOverview();
      window.deck=createScheduleDeck(document.getElementById('deck'),{onNavigate:url=>window.navigations.push(url)});
      window.deck.setOverview(window.overview);window.ready=true;
      </script></html>` });
  });
  await page.goto('http://schedule-stack.test/');
  await page.waitForFunction(() => (window as any).ready);
  return { errors, unexpected };
}

async function settleStack(page: Page) {
  await page.locator('[data-csd-stage]').evaluate(async stage => {
    let quiet = 0;
    for (let frame = 0; frame < 90 && quiet < 2; frame++) {
      await new Promise(requestAnimationFrame);
      quiet = stage.getAnimations({ subtree: true }).some(animation => ['running', 'pending'].includes(animation.playState)) ? 0 : quiet + 1;
    }
    if (quiet < 2) throw new Error('Stack motion did not settle');
  });
}

async function assertOnlyActiveContent(page: Page, index: number, shellCount = 20, { ownBlur = true } = {}) {
  await expect(page.locator('#deck .cs-card')).toHaveCount(shellCount);
  await expect(page.locator('#deck .cs-card.is-active')).toHaveAttribute('data-week-index', String(index));
  await expect(page.locator('#deck .cs-card__content')).toHaveCount(1);
  await expect(page.locator('#deck .cs-grid')).toHaveCount(1);
  await expect(page.locator('#deck .cs-card.is-active .cs-card__bar strong')).toHaveText(`第${index + 1}周`);
  expect(await page.locator('#deck .cs-card:not(.is-active)').evaluateAll(cards => cards.map(card => ({
    children: card.childElementCount,
    text: card.textContent?.trim(),
    ariaHidden: card.getAttribute('aria-hidden'),
    material: card.getAttribute('data-lq-material'),
    blur: getComputedStyle(card).backdropFilter,
  })))).toEqual(Array.from({ length: shellCount - 1 }, () => ({ children: 0, text: '', ariaHidden: 'true', material: null, blur: 'none' })));
  expect(await page.locator('#deck .cs-card.is-active').getAttribute('aria-hidden')).not.toBe('true');
  await expect(page.locator('#deck .cs-card.is-active')).toHaveAttribute('data-lq-material', 'raised');
  if (ownBlur) await expect(page.locator('#deck .cs-card.is-active')).not.toHaveCSS('backdrop-filter', 'none');
  else await expect(page.locator('#deck .cs-card.is-active')).toHaveCSS('backdrop-filter', 'none');
}

for (const width of [390, 1440]) for (const appearance of ['light', 'dark']) {
  test(`rear week shells keep glass geometry without text or grids at ${width} ${appearance}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 980 });
    const diagnostics = await mountStack(page, { appearance });
    await settleStack(page);
    await assertOnlyActiveContent(page, 4);
    const surfaces = await page.locator('#deck .cs-card').evaluateAll(cards => cards.map(card => {
      const style = getComputedStyle(card);
      return { index: Number((card as HTMLElement).dataset.weekIndex), hidden: (card as HTMLElement).hidden,
        transform: style.transform, opacity: Number(style.opacity), fill: style.backgroundColor,
        border: style.borderTopWidth, willChange: style.willChange };
    }));
    const rear = surfaces.filter(card => !card.hidden && card.index > 4);
    expect(rear).toHaveLength(5);
    for (const card of rear) {
      expect(card.transform).not.toBe('none');
      expect(card.opacity).toBeGreaterThan(0);
      expect(card.fill).not.toBe('rgba(0, 0, 0, 0)');
      expect(parseFloat(card.border)).toBeGreaterThan(0);
    }
    expect(surfaces.every(card => card.willChange === 'auto')).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`stack-${width}-${appearance}.png`) });
    expect(diagnostics).toEqual({ errors: [], unexpected: [] });
  });
}

test('navigation synchronously creates only the entering week and retains the same shells across rapid jumps', async ({ page }) => {
  const diagnostics = await mountStack(page, { weeks: 180 });
  const changes = await page.evaluate(() => {
    const w = window as any;
    const shells = [...document.querySelectorAll('#deck .cs-card')];
    return [5, 6, 2, 179, 0, 90, 4].map(index => {
      const outgoing = document.querySelector('#deck .cs-card__content');
      w.deck.goToWeek(index);
      const active = document.querySelector('#deck .cs-card.is-active')!;
      return { index: w.deck.getActiveWeekIndex(), label: active.querySelector('.cs-card__bar strong')?.textContent,
        grids: document.querySelectorAll('#deck .cs-grid').length,
        content: document.querySelectorAll('#deck .cs-card__content').length,
        outgoingRemoved: !outgoing?.isConnected,
        sameShells: [...document.querySelectorAll('#deck .cs-card')].every((node, i) => node === shells[i]),
        rearEmpty: shells.every(node => node === active || node.childNodes.length === 0) };
    });
  });
  expect(changes).toEqual([5, 6, 2, 179, 0, 90, 4].map(index => ({ index, label: `第${index + 1}周`,
    grids: 1, content: 1, outgoingRemoved: true, sameShells: true, rearEmpty: true })));
  await settleStack(page);
  await assertOnlyActiveContent(page, 4, 180);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('the real management Surface mount keeps the nested blur boundary with light dark off reduced and opaque preferences', async ({ page }, testInfo) => {
  const diagnostics = await mountStack(page, { nestedHost: true });
  await expect(page.locator('#deck')).toHaveAttribute('data-lq-component', 'surface');
  await expect(page.locator('#deck')).toHaveClass(/\blq-surface\b/);
  await expect(page.locator('#deck')).toHaveAttribute('data-cs-deck', '');
  const results: unknown[] = [];
  let index = 5;
  for (const appearance of ['light', 'dark']) for (const mode of ['standard', 'off', 'reduced', 'opaque']) {
    await page.emulateMedia({ reducedMotion: mode === 'reduced' ? 'reduce' : 'no-preference' });
    await page.evaluate(({ appearance, mode, index }) => {
      document.documentElement.dataset.appearance = appearance;
      document.documentElement.dataset.lqMotion = mode === 'off' ? 'off' : mode === 'reduced' ? 'expressive' : 'standard';
      document.documentElement.dataset.lqGlass = mode === 'opaque' ? 'off' : 'tinted';
      (window as any).deck.goToWeek(index);
    }, { appearance, mode, index });
    await assertOnlyActiveContent(page, index++, 20, { ownBlur: false });
    await settleStack(page);
    const metrics = await page.locator('#deck .cs-card.is-active').evaluate(card => {
      const style = getComputedStyle(card);
      const context = document.createElement('canvas').getContext('2d')!;
      context.fillStyle = style.backgroundColor;
      context.fillRect(0, 0, 1, 1);
      return { blur: style.backdropFilter, alpha: context.getImageData(0, 0, 1, 1).data[3],
        image: style.backgroundImage, contentOpacity: getComputedStyle(card.querySelector('.cs-card__content')!).opacity,
        liveAnimations: card.closest('[data-csd-stage]')!.getAnimations({ subtree: true }).length };
    });
    expect(metrics.blur).toBe('none');
    expect(metrics.alpha).toBeGreaterThan(0);
    expect(metrics.contentOpacity).toBe('1');
    expect(metrics.liveAnimations).toBe(0);
    if (mode === 'opaque') { expect(metrics.alpha).toBe(255); expect(metrics.image).toBe('none'); }
    results.push({ appearance, mode, ...metrics });
  }
  await testInfo.attach('management-host-materials', { body: JSON.stringify(results, null, 2), contentType: 'application/json' });
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('wheel, keyboard, buttons and native slider render the requested week without extra navigation requests', async ({ page }) => {
  const diagnostics = await mountStack(page);
  await page.locator('[data-csd-next]').click();
  await assertOnlyActiveContent(page, 5);
  await expect(page.locator('[data-csd-next]')).toBeFocused();
  await page.locator('[data-csd-stage]').focus();
  await page.keyboard.press('ArrowRight');
  await assertOnlyActiveContent(page, 6);
  await page.keyboard.press('ArrowLeft');
  await assertOnlyActiveContent(page, 5);
  await page.locator('[data-csd-slider]').fill('12');
  await assertOnlyActiveContent(page, 11);
  await page.locator('[data-csd-stage]').dispatchEvent('wheel', { deltaY: 120, deltaMode: 0 });
  await assertOnlyActiveContent(page, 12);
  // An immediate inertia tail still belongs to the original gesture.
  await page.locator('[data-csd-stage]').dispatchEvent('wheel', { deltaY: 120, deltaMode: 0 });
  await assertOnlyActiveContent(page, 12);
  await page.locator('[data-csd-stage]').dispatchEvent('wheel', { deltaY: -120, ctrlKey: true });
  await assertOnlyActiveContent(page, 12);
  await page.locator('[data-csd-prev]').click();
  await assertOnlyActiveContent(page, 11);
  expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('rapid reversal preserves the current transform and shared finite motion without content overlap', async ({ page }, testInfo) => {
  await mountStack(page);
  await settleStack(page);
  const result = await page.evaluate(async () => {
    const target = document.querySelector<HTMLElement>('#deck .cs-card[data-week-index="5"]')!;
    const matrix = () => Array.from(new DOMMatrixReadOnly(getComputedStyle(target).transform).toFloat64Array());
    const initial = matrix();
    (window as any).deck.goToWeek(5);
    const content = target.querySelector<HTMLElement>('.cs-card__content')!;
    const contentDuration = getComputedStyle(content).transitionDuration;
    const contentFrames = [Number(getComputedStyle(content).opacity)];
    for (let i = 0; i < 3; i++) { await new Promise(requestAnimationFrame); contentFrames.push(Number(getComputedStyle(content).opacity)); }
    const beforeReverse = matrix();
    (window as any).deck.goToWeek(4);
    const afterReverse = matrix();
    const duration = getComputedStyle(target).transitionDuration.split(',').map(value => parseFloat(value) * (value.endsWith('ms') ? 1 : 1000));
    const sharedDuration = parseFloat(getComputedStyle(target).getPropertyValue('--lq-motion-presence-duration'));
    return { initial, beforeReverse, afterReverse, duration, sharedDuration, contentDuration, contentFrames,
      removedContent: !content.isConnected, rearChildren: target.childNodes.length };
  });
  expect(result.beforeReverse.some((value, i) => Math.abs(value - result.initial[i]) > .01)).toBe(true);
  expect(Math.max(...result.afterReverse.map((value, i) => Math.abs(value - result.beforeReverse[i])))).toBeLessThan(.01);
  expect(Math.max(...result.duration)).toBeLessThanOrEqual(result.sharedDuration + 1);
  expect(result.sharedDuration).toBeGreaterThan(0);
  expect(result.removedContent).toBe(true);
  expect(result.rearChildren).toBe(0);
  expect(result.contentFrames.some(value => value < 1)).toBe(true);
  await settleStack(page);
  await assertOnlyActiveContent(page, 4);
  expect(await page.locator('[data-csd-stage]').evaluate(stage => stage.getAnimations({ subtree: true }).length)).toBe(0);
  await testInfo.attach('reversal', { body: JSON.stringify(result, null, 2), contentType: 'application/json' });
});

test('focus relocation, synchronous course lookup and keepWeek refresh preserve their distinct owners', async ({ page }) => {
  const diagnostics = await mountStack(page);
  await page.locator('#deck .cs-card.is-active [data-csd-change]').focus();
  await page.evaluate(() => (window as any).deck.goToWeek(6));
  await expect(page.locator('[data-csd-stage]')).toBeFocused();
  await page.locator('#outside').focus();
  await page.evaluate(() => (window as any).deck.goToWeek(7));
  await expect(page.locator('#outside')).toBeFocused();
  const lookup = await page.evaluate(() => {
    const w = window as any;
    const found = w.deck.focusLesson('change-15', 15);
    return { found, active: w.deck.getActiveWeekIndex(), focused: document.activeElement?.getAttribute('data-csd-change') };
  });
  expect(lookup).toEqual({ found: true, active: 14, focused: 'change-15' });
  await page.evaluate(() => {
    const w = window as any;
    w.overview.weeks[14].lessons[0].course_name = '服务器刷新后的课程';
    w.deck.setOverview(w.overview, { keepWeek: true });
  });
  await assertOnlyActiveContent(page, 14);
  await expect(page.locator('#deck .cs-card.is-active')).toContainText('服务器刷新后的课程');
  await expect(page.locator('[data-csd-stage]')).toBeFocused();
  await page.evaluate(() => (window as any).deck.setOverview((window as any).overview));
  await assertOnlyActiveContent(page, 4);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('expanded reopen retains original lesson links and empty or destroyed decks cannot resurrect miniature content', async ({ page }) => {
  const diagnostics = await mountStack(page);
  await page.locator('#deck .cs-card.is-active .cs-card__bar').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await settleScheduleMotion(page);
  const sameLink = await page.evaluate(async () => {
    const link = document.querySelector('.cs-expand [data-event-key="lesson-5"]');
    (document.querySelector('[data-csd-expand-close]') as HTMLButtonElement).click();
    await new Promise(requestAnimationFrame);
    (window as any).deck.openExpanded();
    return link === document.querySelector('.cs-expand [data-event-key="lesson-5"]');
  });
  expect(sameLink).toBe(true);
  await settleScheduleMotion(page);
  await expect(page.locator('.cs-expand [data-event-key="lesson-5"]')).toHaveAttribute('href', '/classroom/5');
  await page.locator('[data-csd-expand-next]').click();
  await assertOnlyActiveContent(page, 5);
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第6周');
  await page.evaluate(() => (window as any).deck.setOverview({ weeks: [] }));
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('#deck .cs-card')).toHaveCount(0);
  await expect(page.locator('#deck .cs-empty')).toBeVisible();
  await page.evaluate(() => {
    const w = window as any;
    w.deck.setOverview(w.overview);
    w.deck.destroy(); w.deck.destroy();
    w.deck.goToWeek(6); w.deck.setOverview(w.overview); w.deck.openExpanded();
  });
  await expect(page.locator('#deck')).toBeEmpty();
  await expect(page.locator('.cs-expand')).toHaveCount(0);
  await page.evaluate(async () => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); });
  await expect(page.locator('#deck')).toBeEmpty();
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('native miniature comparison buttons and course links retain keyboard activation instead of stage shortcuts', async ({ page }) => {
  const diagnostics = await mountStack(page);
  await page.evaluate(() => (window as any).deck.focusLesson('change-15', 15));
  const trigger = page.locator('#deck .cs-card.is-active [data-csd-change]');
  await expect(trigger).toBeFocused();
  await trigger.press('ArrowRight');
  await assertOnlyActiveContent(page, 14);
  await trigger.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  const expanded = page.locator('.cs-expand [data-event-key="change-15"]');
  await expect(expanded.locator('.cs-adjustment-details')).toBeVisible();
  await expect(expanded.locator('[data-csd-change]')).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await page.locator('#deck .cs-card.is-active a.cs-lesson__main').press('Enter');
  expect(await page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/15?session_id=2']);
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

for (const mode of ['off', 'reduced']) {
  test(`${mode} keeps immediate readable content and navigation without pending stack animations`, async ({ page }) => {
    if (mode === 'reduced') await page.emulateMedia({ reducedMotion: 'reduce' });
    const diagnostics = await mountStack(page, { motion: mode === 'off' ? 'off' : 'expressive' });
    await page.evaluate(() => (window as any).deck.goToWeek(5));
    await assertOnlyActiveContent(page, 5);
    await expect(page.locator('#deck .cs-card__content')).toHaveCSS('opacity', '1');
    expect(await page.locator('[data-csd-stage]').evaluate(stage => stage.getAnimations({ subtree: true }).length)).toBe(0);
    await page.locator('[data-csd-stage]').focus();
    await page.keyboard.press('Enter');
    expect(diagnostics.errors).toEqual([]);
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-csd-stage]')).toBeFocused();
    await assertOnlyActiveContent(page, 5);
  });
}

test.describe('touch stack', () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });
  test('horizontal drag switches content while first tap still opens the active week', async ({ page }) => {
    const diagnostics = await mountStack(page);
    await settleStack(page);
    const point = await page.locator('#deck .cs-card.is-active .cs-card__bar').boundingBox();
    const client = await page.context().newCDPSession(page);
    const start = { x: point!.x + point!.width * .65, y: point!.y + point!.height / 2 };
    await client.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [start] });
    for (let step = 1; step <= 8; step++) {
      await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x - 105 * step / 8, y: start.y }] });
      await page.evaluate(async () => { await new Promise(requestAnimationFrame); });
    }
    // End a deliberate swipe at rest, rather than a synthetic one-frame fling
    // whose browser gesture recognizer consumes the next tap before JS click.
    for (let frame = 0; frame < 8; frame++) {
      await page.evaluate(async () => { await new Promise(requestAnimationFrame); });
      await client.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: start.x - 105, y: start.y }] });
    }
    await client.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    await client.detach();
    await assertOnlyActiveContent(page, 5);
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await settleStack(page);
    await page.locator('#deck .cs-card.is-active .cs-card__bar').tap();
    expect(diagnostics.errors).toEqual([]);
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page.locator('[data-csd-expand-title]')).toHaveText('第6周');
    expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
    expect(diagnostics).toEqual({ errors: [], unexpected: [] });
  });
});
