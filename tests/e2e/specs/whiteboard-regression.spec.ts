import { expect, test, type Page } from '@playwright/test';
import { collectBrowserErrors, expectNoBrowserErrors, loginTeacher, readFixture } from '../fixtures/p03';

async function host(page: Page) {
  await loginTeacher(page, readFixture());
  const response = await page.request.post('/api/materials/upload', { multipart: {
    files: { name: `whiteboard-regression-${Date.now()}.html`, mimeType: 'text/html', buffer: Buffer.from('<!doctype html><html><body><h1>Whiteboard regression</h1><button id="lesson-action" onclick="this.textContent=\'working\'">Lesson action</button></body></html>') },
  } });
  expect(response.ok()).toBeTruthy();
  const id = Number((await response.json()).created_items[0].id);
  await page.goto(`/materials/render-view/${id}?wbperf=1`);
  await expect(page.locator('#teacher-whiteboard-fab')).toBeVisible();
  await page.locator('#teacher-whiteboard-fab').click();
  await expect(page.locator('#teacher-whiteboard-root')).toHaveClass(/is-open/);
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.bootstrap(); });
  return id;
}

async function draw(page: Page, y = 330) {
  const width = page.viewportSize()!.width;
  await page.mouse.move(width * .3, y);
  await page.mouse.down();
  await page.mouse.move(width * .7, y + 70, { steps: 14 });
  await page.mouse.up();
}

test('responsive toolbar, performance preference, opaque iframe and document restore', async ({ page }, testInfo) => {
  const errors = collectBrowserErrors(page);
  await host(page);
  for (const width of [1440, 760, 390, 3840]) {
    await page.setViewportSize({ width, height: width === 3840 ? 2160 : 900 });
    await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.canvasWidth)).toBe(width);
    const toolbar = page.locator('#teacher-whiteboard-toolbar');
    await expect(toolbar).toBeVisible();
    const box = (await toolbar.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
    await draw(page);
    await page.screenshot({ path: testInfo.outputPath(`whiteboard-${width}.png`) });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.evaluate(() => {
    const wb = (window as any).teacherWhiteboard;
    wb.setPerfMode('lite');
    wb.updateSettings({ backgroundOpacity: 1 });
  });
  await expect(page.locator('#teacher-whiteboard-root')).toHaveAttribute('data-perf', 'lite');
  await expect(page.locator('#render-shell-frame')).toHaveCSS('visibility', 'hidden');
  await page.keyboard.press('Escape');
  await expect(page.locator('#render-shell-frame')).toHaveCSS('visibility', 'visible');
  await page.frameLocator('#render-shell-frame').locator('#lesson-action').click();
  await expect(page.frameLocator('#render-shell-frame').locator('#lesson-action')).toHaveText('working');
  expect(await page.frameLocator('#render-shell-frame').locator('html').getAttribute('class')).not.toContain('ld-quiet');
  await page.reload();
  await page.locator('#teacher-whiteboard-fab').click();
  await expect(page.locator('#teacher-whiteboard-root')).toHaveAttribute('data-perf', 'lite');
  await expectNoBrowserErrors(errors, testInfo);
});

test('save while drawing, offline retry, clear sync and concurrent copy preserve content', async ({ page }) => {
  const materialId = await host(page);
  await draw(page);
  let release: (() => void) | undefined;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let started: (() => void) | undefined;
  const start = new Promise<void>(resolve => { started = resolve; });
  await page.route('**/whiteboards/**', async route => {
    if (route.request().method() !== 'PUT') return route.continue();
    const response = await route.fetch();
    started!();
    await gate;
    await route.fulfill({ response });
  });
  await page.keyboard.press('Control+s');
  await start;
  await draw(page, 450);
  release!();
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.sync.inFlight.size)).toBe(0);
  expect(await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.dirty)).toBe(true);
  await page.unroute('**/whiteboards/**');
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.flushDirty(); });
  const boardKey = await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.id);
  const cloud = await page.request.get(`/api/materials/${materialId}/whiteboards/${boardKey}`);
  expect((await cloud.json()).board.elements).toHaveLength(2);

  await page.context().setOffline(true);
  await draw(page, 570);
  await page.keyboard.press('Control+s');
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.sync.inFlight.size)).toBe(0);
  expect(await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.dirty)).toBe(true);
  await page.context().setOffline(false);
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.flushDirty(); });
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.dirty)).toBe(false);

  const previous = (await (await page.request.get(`/api/materials/${materialId}/whiteboards/${boardKey}`)).json()).board;
  const competing = await page.request.put(`/api/materials/${materialId}/whiteboards/${boardKey}`, { data: {
    name: 'Remote version', viewport: previous.viewport, elements: previous.elements.slice(0, 1), schema_version: 2, base_version: previous.version,
  } });
  expect(competing.ok()).toBeTruthy();
  await draw(page, 670);
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.flushDirty(); });
  expect(await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.elements.length)).toBe(4);
  expect(await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.id)).not.toBe(boardKey);
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.flushDirty(); });
  await page.locator('[data-whiteboard-action="clear"]').click();
  await page.getByRole('alertdialog').getByRole('button', { name: /清空|清屏/ }).click();
  await page.evaluate(async () => { await (window as any).teacherWhiteboard.sync.flushDirty(); });
  const copyKey = await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.id);
  const cleared = (await (await page.request.get(`/api/materials/${materialId}/whiteboards/${copyKey}`)).json()).board;
  expect(cleared.elements).toEqual([]);
});

test('legacy local boards migrate without deleting the original cache', async ({ page }) => {
  await host(page);
  await draw(page);
  const legacy = await page.evaluate(async () => {
    const wb = (window as any).teacherWhiteboard;
    const { storageKeys } = await import('/static/js/whiteboard/store_local.js');
    const keys = storageKeys(wb.context);
    const text = JSON.stringify(wb.state);
    wb.sync.enabled = false;
    wb.state = null; wb.activeBoard = null;
    localStorage.setItem(keys.current, text);
    localStorage.removeItem(keys.index);
    Object.keys(localStorage).filter(key => key.startsWith(keys.boardPrefix)).forEach(key => localStorage.removeItem(key));
    return { key: keys.current, index: keys.index, text };
  });
  await page.reload();
  await page.locator('#teacher-whiteboard-fab').click();
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.elements.length)).toBe(1);
  const cache = await page.evaluate(({ key, index }) => ({legacy:localStorage.getItem(key), migrated:!!localStorage.getItem(index)}), legacy);
  expect(cache.legacy).toBe(legacy.text);
  expect(cache.migrated).toBe(true);
});

test('shape and text tools, cancel text, history rename and cloud deletion', async ({ page }) => {
  const id = await host(page);
  await page.locator('[data-whiteboard-shape="rectangle"]').click();
  await draw(page);
  await page.keyboard.press('t');
  await page.mouse.click(450, 450);
  await page.locator('.teacher-whiteboard-text-editor').fill('中文板书回归');
  await page.keyboard.press('Enter');
  await page.mouse.click(450, 570);
  await page.locator('.teacher-whiteboard-text-editor').fill('取消的文字');
  await page.keyboard.press('Escape');
  await expect(page.locator('#teacher-whiteboard-root')).toHaveClass(/is-open/);
  const types = await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.elements.map((el: any) => el.type));
  expect(types).toEqual(['shape', 'text']);
  await page.keyboard.press('Control+s');
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.remoteVersion)).toBeGreaterThan(0);
  await page.locator('[data-whiteboard-action="history"]').click();
  await page.getByRole('button', {name:'重命名',exact:true}).click();
  await page.getByRole('textbox', {name:'白板名称'}).fill('课堂图示与文字');
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.dirty)).toBe(false);
  const key = await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.id);
  const board = (await (await page.request.get(`/api/materials/${id}/whiteboards/${key}`)).json()).board;
  expect(board.name).toBe('课堂图示与文字');
  expect(board.elements).toHaveLength(2);
  await page.getByRole('button', {name:'删除白板',exact:true}).click();
  await page.getByRole('alertdialog').getByRole('button', {name:/删除/}).click();
  await expect.poll(async () => (await page.request.get(`/api/materials/${id}/whiteboards/${key}`)).status()).toBe(404);
  await expect.poll(() => page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.elements.length)).toBe(0);
});

test('real canvas cache preserves mixed ink, erasers and panning; exam drawing undo and reload', async ({ page }) => {
  await host(page);
  const result = await page.evaluate(async () => {
    const { RenderCache } = await import('/static/js/whiteboard/render_cache.js');
    const { renderElements } = await import('/static/js/whiteboard/renderer.js');
    const elements: any[] = Array.from({ length: 400 }, (_, i) => ({ type: 'stroke', color: i % 2 ? '#ff0000' : '#0044ff', size: 4,
      points: [{ x: (i % 20) * 35, y: Math.floor(i / 20) * 25 }, { x: (i % 20) * 35 + 70, y: Math.floor(i / 20) * 25 + 45 }] }));
    elements.push({ type: 'eraser', hardness: .3, size: 28, points: [{x: 200, y: 0}, {x: 220, y: 500}] });
    elements.push({ type: 'eraser', hardness: 1, size: 16, points: [{x: 0, y: 200}, {x: 700, y: 250}] });
    const cache = new RenderCache();
    cache.resize(800, 600, 1);
    cache.rebuild(elements, { x: 0, y: 0, scale: 1 });
    const stats = [];
    for (const viewport of [{x:0,y:0,scale:1}, {x:40,y:30,scale:1}]) {
      const cached = document.createElement('canvas'); cached.width = 800; cached.height = 600;
      const ctx = cached.getContext('2d')!;
      const geometry = cache.geometryFor(viewport);
      cache.blitTo(ctx, geometry);
      for (const rect of geometry.exposed) {
        ctx.save(); ctx.beginPath(); ctx.rect(rect.x, rect.y, rect.width, rect.height); ctx.clip();
        ctx.clearRect(rect.x, rect.y, rect.width, rect.height);
        renderElements(ctx, elements, viewport); ctx.restore();
      }
      const reference = document.createElement('canvas'); reference.width=800; reference.height=600;
      const ref = reference.getContext('2d')!; renderElements(ref,elements,viewport);
      const a=ctx.getImageData(0,0,800,600).data, b=ref.getImageData(0,0,800,600).data;
      let mismatch=0, ink=0;
      for(let i=0;i<a.length;i+=4){ if(b[i+3]>0) ink++; if(Math.abs(a[i+3]-b[i+3])>8) mismatch++; }
      stats.push({mismatch,ink});
    }
    return stats;
  });
  for(const stat of result){ expect(stat.ink).toBeGreaterThan(20000); expect(stat.mismatch).toBeLessThan(100); }
  await page.keyboard.press('Escape');
  await page.evaluate(async () => {
    const { initExamDrawingWhiteboard } = await import('/static/js/whiteboard/exam_board.js');
    const wb = initExamDrawingWhiteboard();
    (window as any).examResult = wb.open({ questionId: 1 });
  });
  await expect(page.locator('#exam-drawing-whiteboard-root')).toBeVisible();
  await draw(page);
  const strokes = await page.evaluate(() => {
    const wb = (window as any).examDrawingWhiteboard;
    (window as any).examPixels = wb.ctx.getImageData(0, 0, wb.canvasEl.width, wb.canvasEl.height).data;
    return JSON.stringify(wb.strokes);
  });
  await page.keyboard.press('Control+z');
  expect(await page.evaluate(() => (window as any).examDrawingWhiteboard.hasContent)).toBe(false);
  await page.keyboard.press('Control+y');
  const restored = await page.evaluate(() => {
    const wb = (window as any).examDrawingWhiteboard;
    const before = (window as any).examPixels;
    const after = wb.ctx.getImageData(0, 0, wb.canvasEl.width, wb.canvasEl.height).data;
    let difference=0, ink=0;
    for(let i=3;i<before.length;i+=4){difference+=Math.abs(before[i]-after[i]); ink+=before[i];}
    return { strokes: JSON.stringify(wb.strokes), alphaDifference: difference/ink };
  });
  expect(restored.strokes).toBe(strokes);
  // Incremental segments and one continuous replay differ slightly at antialiased joins.
  expect(restored.alphaDifference).toBeLessThan(.03);
  await page.locator('[data-exam-drawing-action="save"]').click();
  const saved = await page.evaluate(async () => (await (window as any).examResult).dataUrl);
  expect(saved).toMatch(/^data:image\/png;base64,/);
  await page.evaluate(dataUrl => { (window as any).examDrawingWhiteboard.open({questionId:1,dataUrl}); }, saved);
  await expect.poll(() => page.evaluate(() => Boolean((window as any).examDrawingWhiteboard.baseline))).toBe(true);
  await page.setViewportSize({width:760,height:900});
  expect(await page.evaluate(() => (window as any).examDrawingWhiteboard.hasContent)).toBe(true);
});
