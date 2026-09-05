import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const moduleSource = fs.readFileSync(path.resolve('static/js/course_schedule_deck.js'), 'utf8');
const classes = Array.from({ length: 8 }, (_, index) => `人工智能260${index + 1}班（专升本）`).join(' · ');

async function mountDeck(page: Page) {
  await page.route('http://schedule.test/**', async route => {
    if (route.request().url().endsWith('/deck.js')) {
      await route.fulfill({ contentType: 'text/javascript', body: moduleSource });
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN">
      <meta charset="utf-8"><style>body{margin:24px;font-family:Arial,sans-serif}*{box-sizing:border-box}</style>
      <button id="before">页面入口</button><div id="deck"></div><script type="module">
      import { createScheduleDeck } from '/deck.js';
      window.navigations = [];
      window.deck = createScheduleDeck(document.getElementById('deck'), {
        onNavigate: url => window.navigations.push(url)
      });
      const lesson = (name, weekday, sections, classLabel, url) => ({course_name:name,
        weekday,sections,class_label:classLabel,classroom:'知新楼B416-1',
        classroom_short:'知新楼B416-1',session_no:2,session_total:32,classroom_url:url});
      window.deck.setOverview({ selected_term:{label:'2026-2027第一学期'}, section_range:{min:1,max:11},
        filters:{course_options:['短课程','长课程','超长课程','待匹配课程']},weeks:[
        {week_index:1,label:'第1周',is_current:true,lesson_count:4,total_hours:8,lessons:[
          lesson('短课程',1,[2,3],'计算机2601班','/classroom/1'),
          lesson('长课程',7,[8,9],${JSON.stringify(classes)},'/classroom/2'),
          lesson('超长课程',7,[10,11],${JSON.stringify(classes.repeat(10))},'/classroom/3'),
          lesson('待匹配课程',4,[4,5],'计算机2601班','')
        ]}, ...Array.from({length:7}, (_,index) => ({week_index:index+2,label:'第'+(index+2)+'周',lesson_count:0,total_hours:0,lessons:[]}))]});
      </script></html>` });
  });
  await page.goto('http://schedule.test/');
  await page.locator('.cs-card.is-active').click();
  await expect(page.getByRole('dialog')).toBeVisible();
}

async function previewMetrics(page: Page) {
  return page.locator('.cs-lesson--cell.is-preview').evaluate(cell => {
    const rect = cell.getBoundingClientRect();
    const body = cell.closest('.cs-expand__body')!.getBoundingClientRect();
    const lines = [...cell.children].map(line => ({
      height: line.getBoundingClientRect().height,
      scrollHeight: line.scrollHeight,
    }));
    return { width: rect.width, height: rect.height, left: rect.left, right: rect.right,
      top: rect.top, bottom: rect.bottom, bodyLeft: body.left, bodyRight: body.right,
      bodyTop: body.top, bodyBottom: body.bottom, clientHeight: cell.clientHeight,
      scrollHeight: cell.scrollHeight, scrollTop: cell.scrollTop, lines };
  });
}

function expectContained(metrics: Awaited<ReturnType<typeof previewMetrics>>) {
  expect(metrics.left).toBeGreaterThanOrEqual(metrics.bodyLeft + 10);
  expect(metrics.right).toBeLessThanOrEqual(metrics.bodyRight - 10);
  expect(metrics.top).toBeGreaterThanOrEqual(metrics.bodyTop + 10);
  expect(metrics.bottom).toBeLessThanOrEqual(metrics.bodyBottom - 10);
  for (const line of metrics.lines) expect(line.height).toBeGreaterThanOrEqual(line.scrollHeight - 1);
}

test('populated 3D stacks stay inside their canvas on narrow screens without clipping navigation', async ({ page }) => {
  await mountDeck(page);
  await page.keyboard.press('Escape');
  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: 980 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    for (const control of ['[data-csd-next]', '[data-csd-slider]']) {
      await expect(page.locator(control)).toBeVisible();
      const box = await page.locator(control).boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    }
  }
  await page.locator('.cs-card.is-active').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('link', { name: /^长课程/ }).focus();
  expectContained(await previewMetrics(page));
});

test('short courses stay compact while long Sunday cards wrap completely inside the timetable', async ({ page }, testInfo) => {
  await mountDeck(page);
  await page.getByRole('link', { name: /^短课程/ }).hover();
  const short = await previewMetrics(page);
  expectContained(short);
  expect(short.width).toBeLessThan(300);
  expect(short.height).toBeLessThan(230);
  expect(short.scrollHeight).toBeLessThanOrEqual(short.clientHeight + 1);
  await page.screenshot({ path: testInfo.outputPath('short-course.png') });
  await page.getByRole('link', { name: /^长课程/ }).hover();
  const long = await previewMetrics(page);
  expectContained(long);
  expect(long.width).toBeGreaterThan(short.width);
  expect(long.height).toBeGreaterThan(short.height);
  expect(long.scrollHeight).toBeLessThanOrEqual(long.clientHeight + 1);
  await page.screenshot({ path: testInfo.outputPath('long-course.png') });
  await testInfo.attach('content-dimensions', { body: JSON.stringify({ short, long }, null, 2), contentType: 'application/json' });
  const card = page.locator('.cs-lesson--cell.is-preview');
  await page.mouse.move(long.left + 12, long.top + 12);
  await expect(card).toHaveCount(1);
  await card.click();
  await expect.poll(() => page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/2']);
});

test('exceptionally long cards scroll internally, retain the week, and reflow on viewport changes', async ({ page }) => {
  await mountDeck(page);
  await page.getByRole('link', { name: /^超长课程/ }).hover();
  const metrics = await previewMetrics(page);
  expectContained(metrics);
  expect(metrics.scrollHeight).toBeGreaterThan(metrics.clientHeight);
  await page.mouse.move(metrics.left + metrics.width / 2, metrics.top + metrics.height / 2);
  await page.mouse.wheel(0, 350);
  await expect.poll(async () => (await previewMetrics(page)).scrollTop).toBeGreaterThan(0);
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第1周（本周）');
  // Keep keyboard focus when resizing; a pointer left outside the new viewport
  // correctly dismisses an unfocused hover preview.
  await page.locator('.cs-lesson--cell.is-preview').focus();
  await page.setViewportSize({ width: 768, height: 700 });
  await expect.poll(async () => {
    const next = await previewMetrics(page);
    return next.right <= next.bodyRight - 10 && next.bottom <= next.bodyBottom - 10;
  }).toBe(true);
  expectContained(await previewMetrics(page));
});

test('keyboard access opens details, Escape dismisses in order, and closed dialogs leave the focus order', async ({ page }) => {
  await mountDeck(page);
  await expect(page.locator('[data-csd-expand-close]')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('.cs-lesson--cell.is-preview')).toContainText('短课程');
  await page.keyboard.press('Escape');
  await expect(page.locator('.cs-lesson--cell.is-preview')).toHaveCount(0);
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.locator('.cs-lesson--cell.is-preview')).toContainText('长课程');
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/2']);
  await page.keyboard.press('Escape');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('[data-csd-stage]')).toBeFocused();
  await expect(page.locator('.cs-expand')).toHaveAttribute('hidden', '');
});

test('unmatched courses support focus preview and scrolling without switching weeks', async ({ page }) => {
  await mountDeck(page);
  const card = page.locator('.cs-lesson--cell').filter({ has: page.getByText('待匹配课程', { exact: true }) });
  await card.focus();
  expectContained(await previewMetrics(page));
  await card.hover();
  await page.mouse.wheel(0, 150);
  await expect(page.locator('[data-csd-expand-title]')).toHaveText('第1周（本周）');
});

test.describe('touch', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test('first tap reads the full course, second tap navigates', async ({ page }, testInfo) => {
    await mountDeck(page);
    const card = page.getByRole('link', { name: /^长课程/ });
    await card.tap();
    await expect(card).toHaveClass(/is-preview/);
    expectContained(await previewMetrics(page));
    await page.screenshot({ path: testInfo.outputPath('touch-course.png') });
    expect(await page.evaluate(() => (window as any).navigations)).toEqual([]);
    await card.tap();
    expect(await page.evaluate(() => (window as any).navigations)).toEqual(['/classroom/2']);
  });
});
