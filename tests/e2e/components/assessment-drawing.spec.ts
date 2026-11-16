import { test, expect, type Page } from '@playwright/test';
import { mountAssessmentControls } from '../fixtures/assessment-controls';

async function openBoard(page: Page, source: 'transparent' | 'opaque' | 'blank' = 'blank') {
  await page.evaluate(source => {
    const w = window as any;
    let dataUrl;
    if (source !== 'blank') {
      const image = document.createElement('canvas'); image.width = 800; image.height = 500;
      const ctx = image.getContext('2d')!;
      if (source === 'opaque') { ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, 800, 500); }
      ctx.fillStyle = '#000'; ctx.fillRect(0, 240, 800, 20);
      ctx.fillStyle = 'rgba(255,0,0,.5)'; ctx.fillRect(20, 20, 80, 80);
      dataUrl = image.toDataURL('image/png');
    }
    w.drawingResult = w.board.open({ questionId: '1', paperTitle: '旧附图兼容', dataUrl, fileName: 'legacy.png' });
  }, source);
  await expect(page.locator('#exam-drawing-whiteboard-root')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).board.canvasWidth)).toBeGreaterThan(0);
  if (source !== 'blank') await expect.poll(() => page.evaluate(() => Boolean((window as any).board.baseline))).toBe(true);
}

async function center(page: Page) {
  return page.evaluate(() => {
    const b = (window as any).board, box = b.canvasEl.getBoundingClientRect();
    return { x: box.left + b.canvasWidth / 2, y: box.top + b.canvasHeight / 2 };
  });
}

async function pixel(page: Page, dx = 0, dy = 0) {
  return page.evaluate(({ dx, dy }) => {
    const b = (window as any).board;
    return Array.from(b.ctx.getImageData(Math.round((b.canvasWidth / 2 + dx) * b.dpr), Math.round((b.canvasHeight / 2 + dy) * b.dpr), 1, 1).data);
  }, { dx, dy });
}

async function eraseCenter(page: Page) {
  const point = await center(page);
  await page.mouse.move(point.x, point.y - 30); await page.mouse.down();
  await page.mouse.move(point.x, point.y + 30, { steps: 12 }); await page.mouse.up();
}

test('drawing tooltip remains above the overlay and closes and rebinds across sessions', async ({ page }) => {
  await mountAssessmentControls(page, { drawing: true }); await openBoard(page);
  await page.clock.install(); await page.clock.pauseAt(new Date());
  const eraser = page.locator('[data-exam-drawing-tool="eraser"]');
  for (let session = 0; session < 2; session++) {
    await page.mouse.move(5, 5); await eraser.hover(); await page.clock.runFor(999);
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await page.clock.runFor(33); await expect(page.getByRole('tooltip')).toHaveText('橡皮擦');
    const geometry = await page.getByRole('tooltip').evaluate(node => {
      const b = node.getBoundingClientRect(), hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2);
      return { hit: node === hit || node.contains(hit), left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: innerWidth, height: innerHeight };
    });
    expect(geometry.hit).toBe(true); expect(geometry.left).toBeGreaterThanOrEqual(0);
    expect(geometry.top).toBeGreaterThanOrEqual(0); expect(geometry.right).toBeLessThanOrEqual(geometry.width);
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.height);
    await page.locator('[data-exam-drawing-action="cancel"]').click(); await page.clock.runFor(200);
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await expect(page.locator('#exam-drawing-whiteboard-root')).toBeHidden();
    if (!session) await openBoard(page);
  }
});

for (const source of ['opaque', 'transparent'] as const) test(`student eraser edits ${source} legacy PNG with undo, redo, white export and reopen`, async ({ page }, testInfo) => {
  await mountAssessmentControls(page, { drawing: true });
  await openBoard(page, source);
  expect(await pixel(page)).toEqual([0, 0, 0, 255]);
  const alphaAwayFromInk = (await pixel(page, 120, -120))[3];
  expect(alphaAwayFromInk).toBe(source === 'opaque' ? 255 : 0);
  const brush = page.locator('[data-exam-drawing-tool="brush"]');
  const eraser = page.locator('[data-exam-drawing-tool="eraser"]');
  expect(await eraser.locator('svg').innerHTML()).not.toBe(await page.locator('[data-exam-drawing-action="clear"] svg').innerHTML());
  const range = page.locator('#exam-drawing-whiteboard-root-brush-size');
  await brush.click(); await range.focus(); await range.press('Home'); await range.press('ArrowRight');
  const brushSize = await range.inputValue();
  await eraser.click(); await expect(eraser).toHaveAttribute('aria-pressed', 'true');
  await expect(brush).toHaveAttribute('aria-pressed', 'false');
  await expect(range).toHaveAttribute('aria-label', '橡皮大小');
  await range.focus(); await range.press('End'); expect(await range.inputValue()).toBe('120');
  await brush.click(); expect(await range.inputValue()).toBe(brushSize);
  await eraser.click(); expect(await range.inputValue()).toBe('120');
  await eraseCenter(page); expect((await pixel(page))[3]).toBe(0);
  await page.locator('[data-exam-drawing-action="undo"]').click(); expect(await pixel(page)).toEqual([0, 0, 0, 255]);
  await page.locator('[data-exam-drawing-action="redo"]').click(); expect((await pixel(page))[3]).toBe(0);
  await testInfo.attach('editing-alpha.json', { contentType: 'application/json', body: JSON.stringify({ source, alphaAwayFromInk, erasedCenter: await pixel(page) }) });
  await page.locator('[data-exam-drawing-action="save"]').click();
  const saved = await page.evaluate(async () => (await (window as any).drawingResult).dataUrl);
  expect(saved).toMatch(/^data:image\/png;base64,/);
  const exported = await page.evaluate(async dataUrl => {
    const image = new Image(); image.src = dataUrl; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
    return { center: Array.from(ctx.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data), corner: Array.from(ctx.getImageData(1, 1, 1, 1).data) };
  }, saved);
  expect(exported).toEqual({ center: [255, 255, 255, 255], corner: [255, 255, 255, 255] });
  await page.evaluate(dataUrl => { const w = window as any; w.drawingResult = w.board.open({ questionId: '1', dataUrl }); }, saved);
  await expect.poll(() => page.evaluate(() => Boolean((window as any).board.baseline))).toBe(true);
  expect(await pixel(page)).toEqual([255, 255, 255, 255]);
  await page.locator('[data-exam-drawing-tool="eraser"]').click(); await eraseCenter(page);
  expect((await pixel(page))[3]).toBe(0);
  await page.locator('[data-exam-drawing-action="cancel"]').click();
  expect(await page.evaluate(async () => await (window as any).drawingResult)).toBeNull();
});

test('student eraser removes new ink and keyboard undo restores it without losing the brush setting', async ({ page }) => {
  await mountAssessmentControls(page, { drawing: true }); await openBoard(page);
  const point = await center(page);
  await page.mouse.move(point.x - 100, point.y); await page.mouse.down();
  await page.mouse.move(point.x + 100, point.y, { steps: 20 }); await page.mouse.up();
  expect((await pixel(page))[3]).toBeGreaterThan(200);
  await page.locator('[data-exam-drawing-tool="eraser"]').click(); await eraseCenter(page);
  expect((await pixel(page))[3]).toBe(0);
  await page.keyboard.press('Control+z'); expect((await pixel(page))[3]).toBeGreaterThan(200);
  await page.keyboard.press('Control+y'); expect((await pixel(page))[3]).toBe(0);
  await page.keyboard.press('Escape'); await expect(page.locator('#exam-drawing-whiteboard-root')).toBeHidden();
});

for (const appearance of ['light', 'dark']) test(`student drawing controls fit narrow ${appearance} with an identifiable eraser`, async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mountAssessmentControls(page, { drawing: true, appearance }); await openBoard(page);
  const eraser = page.locator('[data-exam-drawing-tool="eraser"]');
  await expect(eraser).toBeVisible(); await eraser.click();
  await expect(eraser).toHaveAttribute('aria-pressed', 'true');
  const metrics = await page.locator('.exam-drawing-toolbar').evaluate(node => {
    const box = node.getBoundingClientRect();
    return { innerWidth, scrollWidth: document.documentElement.scrollWidth, left: box.left, right: box.right,
      controls: [...node.querySelectorAll('button,input')].map(control => { const b = control.getBoundingClientRect(); return { label: control.getAttribute('aria-label') || control.textContent, left: b.left, right: b.right }; }) };
  });
  expect(metrics.left).toBeGreaterThanOrEqual(-1); expect(metrics.right).toBeLessThanOrEqual(391);
  for (const control of metrics.controls) { expect(control.left).toBeGreaterThanOrEqual(-1); expect(control.right).toBeLessThanOrEqual(391); }
  await testInfo.attach('drawing-layout.json', { contentType: 'application/json', body: JSON.stringify(metrics) });
  await page.screenshot({ path: testInfo.outputPath(`drawing-${appearance}-390.png`), fullPage: true });
});

test.describe('student touch drawing', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test('touch creates and erases ink while retaining undo without a focus tooltip', async ({ page }) => {
    await mountAssessmentControls(page, { drawing: true }); await openBoard(page);
    const point = await center(page);
    await page.touchscreen.tap(point.x, point.y); expect((await pixel(page))[3]).toBeGreaterThan(200);
    await page.locator('[data-exam-drawing-tool="eraser"]').tap();
    await expect(page.getByRole('tooltip')).toHaveCount(0);
    await page.touchscreen.tap(point.x, point.y); expect((await pixel(page))[3]).toBe(0);
    await page.locator('[data-exam-drawing-action="undo"]').tap(); expect((await pixel(page))[3]).toBeGreaterThan(200);
    await page.locator('[data-exam-drawing-action="redo"]').tap(); expect((await pixel(page))[3]).toBe(0);
    await page.locator('[data-exam-drawing-action="cancel"]').tap();
    await expect(page.locator('#exam-drawing-whiteboard-root')).toBeHidden();
  });
});
