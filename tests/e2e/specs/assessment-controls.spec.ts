import type { Page } from '@playwright/test';
import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

async function pointerLine(page: Page, x1: number, y1: number, x2: number, y2: number) {
  await page.mouse.move(x1, y1); await page.mouse.down();
  await page.mouse.move(x2, y2, { steps: 20 }); await page.mouse.up();
}

async function canvasGeometry(page: Page, owner: 'examDrawingWhiteboard' | 'teacherWhiteboard') {
  return page.evaluate(owner => {
    const b = (window as any)[owner], r = b.canvasEl.getBoundingClientRect();
    return { x: r.left + b.canvasWidth / 2, y: r.top + b.canvasHeight / 2 };
  }, owner);
}

async function alphaAtCenter(page: Page, owner: 'examDrawingWhiteboard' | 'teacherWhiteboard') {
  return page.evaluate(owner => { const b = (window as any)[owner];
    return b.ctx.getImageData(b.canvasEl.width / 2, b.canvasEl.height / 2, 1, 1).data[3];
  }, owner);
}

test('real teacher and student icon editors preserve returned drafts and drawing save/reopen/submission', async ({ page }, testInfo) => {
  test.setTimeout(150000);
  const fixture = readS3Fixture();
  const failures: string[] = []; page.on('pageerror', error => failures.push(error.message));
  await loginTeacher(page, fixture);
  expect((await page.goto('/exam/lq-s3-examFailure/edit'))?.status()).toBe(200);
  const teacherToolbar = page.locator('.assessment-editor__toolbar').first();
  await expect(teacherToolbar.getByRole('button', { name: '加粗', exact: true }).locator('svg')).toHaveCount(1);
  expect((await teacherToolbar.textContent())?.trim()).toBe('');
  await loginStudent(page, fixture);
  const homework = fixture.s3.legacyReturnedHomeworkAssignmentId;
  const oldAnswer = page.locator('.answer-textarea').first();
  expect((await page.goto(`/assignment/${homework}`))?.status()).toBe(200);
  await expect(oldAnswer).toHaveValue('存量旧答案：分层分工、接口协作。');
  await expect(page.locator('#file-chips')).toContainText('legacy-returned-homework.txt');
  await oldAnswer.selectText(); await page.locator('.assessment-editor__toolbar').first().getByRole('button', { name: '加粗', exact: true }).click();
  await expect(oldAnswer).toHaveValue('**存量旧答案：分层分工、接口协作。**');
  const saved = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${homework}/draft`) && response.request().method() === 'POST');
  expect((await saved).status()).toBe(200);
  for (const appearance of ['light', 'dark']) {
    const preferences = (await (await page.request.get('/api/profile/ui-preferences')).json()).preferences;
    const changed = await page.request.patch('/api/profile/ui-preferences', { headers: { 'X-UI-Preferences-Context': preferences.context_token },
      data: { appearance, version: preferences.version } });
    expect(changed.status()).toBe(200);
    await page.setViewportSize({ width: 390, height: 844 }); await page.reload();
    await expect(oldAnswer).toHaveValue('**存量旧答案：分层分工、接口协作。**');
    await expect(page.locator('#file-chips .file-chip')).toHaveCount(1);
    await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); });
    await oldAnswer.scrollIntoViewIfNeeded();
    const layout = await page.evaluate(() => ({ innerWidth, scrollWidth: document.documentElement.scrollWidth, scrollX,
      toolbar: Array.from(document.querySelectorAll('.assessment-editor__toolbar button')).map(button => {
        const b = button.getBoundingClientRect(); return { name: button.getAttribute('aria-label'), x: b.x, width: b.width, height: b.height, right: b.right };
      }), overflow: Array.from(document.body.querySelectorAll('*')).map(node => {
        const b = node.getBoundingClientRect(); return { tag: node.tagName, cls: typeof node.className === 'string' ? node.className : '', left: b.left, right: b.right, height: b.height };
      }).filter(row => row.height > 0 && row.right > innerWidth + 2).slice(0, 12) }));
    await testInfo.attach(`ordinary-${appearance}-layout.json`, { contentType: 'application/json', body: JSON.stringify(layout) });
    for (const button of layout.toolbar) { expect(button.x).toBeGreaterThanOrEqual(-2); expect(button.right).toBeLessThanOrEqual(392); }
    await page.screenshot({ path: testInfo.outputPath(`ordinary-${appearance}-390.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  const exam = fixture.s3.examFailureAssignmentId, endpoint = `/api/assignments/${exam}`;
  expect((await page.goto(`/exam/take/${exam}`))?.status()).toBe(200);
  await page.locator('textarea[data-text-input="q1"]').fill('附图说明：中心位置用橡皮擦修正。');
  await page.screenshot({ path: testInfo.outputPath('exam-answer-desktop-1440.png'), fullPage: true });
  await page.locator('[data-open-drawing="q1"]').click();
  await expect(page.locator('#exam-drawing-whiteboard-root')).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).examDrawingWhiteboard.canvasWidth)).toBeGreaterThan(0);
  let point = await canvasGeometry(page, 'examDrawingWhiteboard');
  await pointerLine(page, point.x - 140, point.y, point.x + 140, point.y);
  expect(await alphaAtCenter(page, 'examDrawingWhiteboard')).toBeGreaterThan(200);
  await page.locator('[data-exam-drawing-tool="eraser"]').click();
  await pointerLine(page, point.x, point.y - 30, point.x, point.y + 30);
  expect(await alphaAtCenter(page, 'examDrawingWhiteboard')).toBe(0);
  const draftSaved = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft` && response.request().method() === 'POST');
  await page.locator('[data-exam-drawing-action="save"]').click();
  const firstDraft = await draftSaved; expect(firstDraft.status()).toBe(200);
  expect((await firstDraft.json()).files_by_question.q1.some((file: any) => file.kind === 'exam_drawing')).toBe(true);
  await page.goto('/dashboard'); await page.evaluate(() => localStorage.clear());
  await page.goto(`/exam/take/${exam}`);
  await expect(page.locator('[data-drawing-preview="q1"] img')).toBeVisible();
  await page.locator('[data-open-drawing="q1"]').click();
  await expect.poll(() => page.evaluate(() => Boolean((window as any).examDrawingWhiteboard.baseline))).toBe(true);
  point = await canvasGeometry(page, 'examDrawingWhiteboard');
  const centerColor = await page.evaluate(() => { const b = (window as any).examDrawingWhiteboard; return Array.from(b.ctx.getImageData(b.canvasEl.width / 2, b.canvasEl.height / 2, 1, 1).data); });
  expect(centerColor).toEqual([255, 255, 255, 255]);
  await page.locator('[data-exam-drawing-tool="eraser"]').click();
  await pointerLine(page, point.x + 80, point.y - 30, point.x + 80, point.y + 30);
  const changedDrawing = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft` && response.request().method() === 'POST');
  await page.locator('[data-exam-drawing-action="save"]').click();
  expect((await changedDrawing).status()).toBe(200);
  const submitted = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/submit` && response.request().method() === 'POST');
  page.on('dialog', dialog => dialog.accept()); await page.locator('#topbarSubmitBtn').click();
  const finalResponse = await submitted; expect(finalResponse.status()).toBe(200);
  const sid = (await finalResponse.json()).submission_id;
  const files = readS3Rows<{ id: number; mime_type: string }>('SELECT id,mime_type FROM submission_files WHERE submission_id=?', [sid]);
  expect(files).toHaveLength(1); expect(files[0].mime_type).toBe('image/png');
  const png = await page.request.get(`/submissions/download/${files[0].id}`); expect(png.status()).toBe(200);
  const pixels = await page.evaluate(async src => {
    const image = new Image(); image.src = src; await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
    return { center: Array.from(ctx.getImageData(canvas.width / 2, canvas.height / 2, 1, 1).data), ink: Array.from(ctx.getImageData(canvas.width / 2 - 100, canvas.height / 2, 1, 1).data) };
  }, `data:image/png;base64,${(await png.body()).toString('base64')}`);
  expect(pixels.center).toEqual([255, 255, 255, 255]); expect(pixels.ink).not.toEqual([255, 255, 255, 255]);
  expect(failures).toEqual([]);
});

test('teacher material whiteboard retains its independent vector eraser, undo, redo and cloud save', async ({ page }) => {
  const fixture = readS3Fixture(); await loginTeacher(page, fixture);
  const uploaded = await page.request.post('/api/materials/upload', { multipart: {
    files: { name: 'assessment-tools-teacher-board.html', mimeType: 'text/html', buffer: Buffer.from('<!doctype html><html><body><h1>Teacher eraser regression</h1></body></html>') },
  } });
  expect(uploaded.status()).toBe(200);
  const id = Number((await uploaded.json()).created_items[0].id);
  await page.goto(`/materials/render-view/${id}`);
  await page.locator('#teacher-whiteboard-fab').click();
  await expect(page.locator('#teacher-whiteboard-root')).toHaveClass(/is-open/);
  await page.evaluate(async () => await (window as any).teacherWhiteboard.sync.bootstrap());
  const point = await canvasGeometry(page, 'teacherWhiteboard');
  await page.locator('[data-whiteboard-tool="brush"]').click();
  await pointerLine(page, point.x - 130, point.y, point.x + 130, point.y);
  await expect.poll(() => alphaAtCenter(page, 'teacherWhiteboard')).toBeGreaterThan(0);
  await page.locator('[data-whiteboard-tool="eraser"]').click();
  await pointerLine(page, point.x, point.y - 40, point.x, point.y + 40);
  await expect.poll(() => alphaAtCenter(page, 'teacherWhiteboard')).toBe(0);
  expect(await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.elements.at(-1).type)).toBe('eraser');
  await page.keyboard.press('Control+z'); await expect.poll(() => alphaAtCenter(page, 'teacherWhiteboard')).toBeGreaterThan(0);
  await page.keyboard.press('Control+y'); await expect.poll(() => alphaAtCenter(page, 'teacherWhiteboard')).toBe(0);
  const cloudSaved = page.waitForResponse(response => response.url().includes(`/api/materials/${id}/whiteboards/`) && response.request().method() === 'PUT');
  await page.keyboard.press('Control+s'); expect((await cloudSaved).status()).toBe(200);
  const boardId = await page.evaluate(() => (window as any).teacherWhiteboard.activeBoard.id);
  const cloud = await page.request.get(`/api/materials/${id}/whiteboards/${boardId}`);
  expect((await cloud.json()).board.elements.some((element: any) => element.type === 'eraser')).toBe(true);
  await expect(page.locator('#exam-drawing-whiteboard-root')).toHaveCount(0);
});

test.describe('real touch layouts', () => {
  test.use({ isMobile: true, hasTouch: true, viewport: { width: 390, height: 844 } });
test('ordinary restored attachment and exam editor fit stable narrow light and dark pages', async ({ page }, testInfo) => {
  const fixture = readS3Fixture(); await loginStudent(page, fixture);
  for (const appearance of ['light', 'dark']) {
    const preferences = (await (await page.request.get('/api/profile/ui-preferences')).json()).preferences;
    expect((await page.request.patch('/api/profile/ui-preferences', { headers: { 'X-UI-Preferences-Context': preferences.context_token },
      data: { appearance, version: preferences.version } })).status()).toBe(200);
    for (const route of ['ordinary', 'exam']) {
      await page.goto(route === 'ordinary' ? `/assignment/${fixture.s3.legacyReturnedHomeworkAssignmentId}` : `/exam/take/${fixture.s3.examDraftAssignmentId}`);
      const input = route === 'ordinary' ? page.locator('.answer-textarea').first() : page.locator('textarea[data-text-input="q1"]');
      await expect(input).toBeVisible();
      if (route === 'ordinary') {
        const chip = page.locator('#file-chips .file-chip');
        await expect(chip).toHaveCount(1); await expect(chip.locator('[title="legacy-returned-homework.txt"]')).toBeVisible();
        await expect(chip.locator('[data-remove-upload]')).toBeVisible();
      } else await input.fill('第一步：梳理需求。\n第二步：验证结果。');
      await page.evaluate(async () => { await document.fonts.ready; await new Promise(requestAnimationFrame); });
      await input.scrollIntoViewIfNeeded();
      const geometry = await page.evaluate(() => ({ innerWidth, scrollWidth: document.documentElement.scrollWidth, scrollX,
        coarse: matchMedia('(pointer: coarse)').matches,
        controls: [...document.querySelectorAll('.assessment-editor__toolbar button,.assessment-editor textarea,#file-chips .file-chip,#file-chips [data-remove-upload]')].map(node => {
          const b = node.getBoundingClientRect(), s = getComputedStyle(node); return { tag: node.tagName, name: node.getAttribute('aria-label'), left: b.left, right: b.right, width: b.width, height: b.height, minWidth: s.minWidth, minHeight: s.minHeight, space10: s.getPropertyValue('--ls-s-10'), space1: s.getPropertyValue('--ls-s-1'), buttonHeight: s.getPropertyValue('--lq-btn-height') };
        }) }));
      await testInfo.attach(`${route}-${appearance}-stable-layout.json`, { contentType: 'application/json', body: JSON.stringify(geometry) });
      expect(geometry.coarse).toBe(true);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.innerWidth); expect(geometry.scrollX).toBe(0);
      for (const control of geometry.controls) {
        expect(control.left).toBeGreaterThanOrEqual(-1); expect(control.right).toBeLessThanOrEqual(391);
        if (control.tag === 'BUTTON' && control.name) { expect(control.width).toBeGreaterThanOrEqual(44); expect(control.height).toBeGreaterThanOrEqual(44); }
      }
      await page.screenshot({ path: testInfo.outputPath(`${route}-${appearance}-stable-390.png`) });
      expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
    }
  }
});
});
