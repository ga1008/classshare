import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

// These save/closeout checks require the enriched disposable acceptance fixture,
// including a pending alert. Keep the ordinary P03 smoke suite independent.
test.skip(!process.env.HOME_CLASSROOM_BUSINESS_ACCEPTANCE, 'Requires an explicitly seeded disposable business fixture');

test('teacher saves ordinary grade kind from the complete task panel', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const open = () => page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
  await open();
  const control = page.locator(`[data-ordinary-grade-kind-select][data-assignment-id="${fixture.teacherReviewAssignmentId}"]`);
  const previous = await control.inputValue();
  const next = previous === 'exam' ? 'assignment' : 'exam';
  const saved = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${fixture.teacherReviewAssignmentId}/ordinary-grade-kind`) && response.request().method() === 'PATCH');
  await control.selectOption(next);
  expect((await saved).status()).toBe(200);
  expect(page.url()).toContain(`/classroom/${fixture.classOfferingId}`);
  await page.reload();
  await open();
  await expect(control).toHaveValue(next);
  const restored = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${fixture.teacherReviewAssignmentId}/ordinary-grade-kind`) && response.request().method() === 'PATCH');
  await control.selectOption(previous);
  expect((await restored).status()).toBe(200);
});

test('member details return to the same roster filter, scroll and focused row', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('.cw-learning-line [data-learning-modal-open]').click();
  const modal = page.locator('#learning-progress-modal');
  await expect(modal).toBeVisible();
  await modal.locator('[data-learning-roster-search]').fill(fixture.student.studentNumber);
  const row = modal.locator(`[data-student-insight-open][href="/manage/students/${fixture.student.id}"]`).last();
  await row.scrollIntoViewIfNeeded();
  const scroll = await modal.locator('.learning-modal-shell').evaluate(element => element.scrollTop);
  await row.click();
  const detail = page.locator('#student-insight-modal');
  await expect(detail).toBeVisible();
  await expect(page.frameLocator('[data-student-insight-frame]').getByText(new RegExp(`学号 ${fixture.student.studentNumber}`)).first()).toBeVisible();
  await page.locator('#student-insight-modal-close').click();
  await expect(detail).toBeHidden();
  await expect(row).toBeFocused();
  await expect(modal.locator('[data-learning-roster-search]')).toHaveValue(fixture.student.studentNumber);
  expect(await modal.locator('.learning-modal-shell').evaluate(element => element.scrollTop)).toBe(scroll);
});

test('teacher previews and saves real cultivation weights then sees persisted values', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('.cw-learning-line [data-learning-modal-open]').click();
  const panel = page.locator('[data-cultivation-weight-settings]');
  const weights: Record<string, number> = { material: 35, task: 35, interaction: 20, consistency: 10 };
  for (const [key, value] of Object.entries(weights)) {
    await panel.locator(`[data-weight-key="${key}"] [data-weight-number]`).fill(String(value));
  }
  const preview = page.waitForResponse(response => response.url().endsWith('/learning/weights/preview'));
  await panel.locator('[data-weight-preview-button]').click();
  expect((await preview).status()).toBe(200);
  await expect(panel.locator('[data-weight-preview]')).toBeVisible();
  const saved = page.waitForResponse(response => response.url().endsWith('/learning/weights') && response.request().method() === 'POST');
  await panel.locator('[data-weight-save]').click();
  expect((await saved).status()).toBe(200);
  await page.waitForEvent('domcontentloaded');
  await page.locator('.cw-learning-line [data-learning-modal-open]').click();
  for (const [key, value] of Object.entries(weights)) {
    await expect(panel.locator(`[data-weight-key="${key}"] [data-weight-number]`)).toHaveValue(String(value));
  }
});

test('final-material deep link opens for its teacher and exposes no teacher generator to a student', async ({ page, browser }) => {
  const fixture = readFixture();
  const path = `/classroom/${fixture.classOfferingId}?open_final_material=1&final_material_type=exam_paper`;
  await loginTeacher(page, fixture);
  await page.goto(path);
  await expect(page.locator('#classroom-final-material-modal')).toBeVisible();
  await expect(page.locator('#classroom-final-material-type')).toHaveValue('exam_paper');
  const studentContext = await browser.newContext();
  const student = await studentContext.newPage();
  await loginStudent(student, fixture);
  await student.goto(path);
  await expect(student.locator('#classroom-final-material-modal')).toBeHidden();
  await expect(student.locator('#classroom-final-material-generate-btn')).toHaveCount(0);
  await studentContext.close();
});

test('material selection, select all and cancel keep one count and survive detail return', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('#cw-materials-preview').getByRole('button', { name: /全部课堂材料/ }).click();
  const rows = page.locator('#classroom-materials-list .materials-row');
  await expect(rows.first()).toBeVisible();
  const count = await rows.count();
  const checks = rows.locator('[data-role="select-item"]');
  for (const row of await rows.all()) {
    const materialName = (await row.locator('.materials-name-copy strong').innerText()).trim();
    await expect(row.getByRole('checkbox', { name: `选择材料：${materialName}`, exact: true })).toHaveCount(1);
  }
  const all = page.getByRole('checkbox', { name: '选择本页全部材料' });
  await expect(page.locator('#classroom-materials-selection')).toBeHidden();
  await checks.first().check();
  await expect(page.locator('#classroom-materials-selection-count')).toHaveText('1');
  await rows.first().locator('.materials-name-copy strong').click();
  await expect(page.getByRole('dialog', { name: '材料详情' }).locator('#classroom-material-detail-title')).toBeVisible();
  await page.getByRole('button', { name: '← 返回列表' }).click();
  await expect(checks.first()).toBeChecked();
  await expect(page.locator('#classroom-materials-selection-count')).toHaveText('1');
  await all.check();
  await expect(page.locator('#classroom-materials-selection-count')).toHaveText(String(count));
  expect(await checks.evaluateAll(elements => elements.every(el => (el as HTMLInputElement).checked))).toBe(true);
  await all.uncheck();
  await expect(page.locator('#classroom-materials-selection')).toBeHidden();
  const downloadable = rows.filter({ has: page.locator('[data-action="download"]') }).first();
  await downloadable.locator('[data-role="select-item"]').check();
  const response = page.waitForResponse(response => response.url().endsWith('/api/materials/download'));
  const downloaded = page.waitForEvent('download');
  await page.locator('#classroom-materials-download-btn').click();
  expect((await response).status()).toBe(200);
  expect((await downloaded).suggestedFilename()).toMatch(/\.zip$/);
});

test('discussion quote, unsent attachment, draft and history position survive panel changes', async ({ page }, testInfo) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('#classroom-activity-tab-discussion').click();
  const messages = page.locator('#chat-messages');
  await messages.locator('.chat-message:not(.system) .chat-message-main').first().click({ button: 'right' });
  await page.locator('#chat-message-menu [data-message-action="quote"]').click();
  await expect(page.locator('#chat-quote-preview')).toBeVisible();
  const quotedText = await page.locator('#chat-quote-preview').innerText();
  const draft = 'QA unsent draft with quote and image';
  await page.locator('#chat-input').fill(draft);
  await page.locator('#chat-attachment-file-input').setInputFiles({
    name: 'qa-pending.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGRkAAAAASUVORK5CYII=', 'base64'),
  });
  await expect(page.locator('#chat-attachment-preview-row .chat-attachment-preview-card')).toHaveCount(1);
  await messages.evaluate(el => { el.scrollTop = 0; });
  const scroll = await messages.evaluate(el => el.scrollTop);
  await page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
  await page.keyboard.press('Escape');
  await page.locator('#classroom-activity-tab-resources').click();
  await page.locator('#classroom-activity-tab-discussion').click();
  await expect(page.locator('#chat-input')).toHaveValue(draft);
  await expect(page.locator('#chat-quote-preview')).toHaveText(quotedText, { useInnerText: true });
  await expect(page.locator('#chat-attachment-preview-row .chat-attachment-preview-card')).toHaveCount(1);
  expect(await messages.evaluate(el => el.scrollTop)).toBe(scroll);
  await page.locator('.chat-attachment-preview-remove').click();
  await expect(page.locator('#chat-attachment-preview-row')).toBeHidden();
  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    const send = page.locator('#chat-form button[type="submit"]');
    await send.scrollIntoViewIfNeeded();
    await expect(send).toBeInViewport();
    const geometry = await send.evaluate(button => {
      const box = button.getBoundingClientRect();
      const target = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
      const panel = button.closest('.classroom-activity-panel')!;
      return {
        hit: target === button || button.contains(target),
        overflow: getComputedStyle(panel).overflowY,
        nestedScroll: panel.scrollHeight - panel.clientHeight,
      };
    });
    expect(geometry.hit).toBe(true);
    expect(geometry.overflow).toBe('visible');
    expect(geometry.nestedScroll).toBeLessThanOrEqual(2);
    await page.screenshot({ path: testInfo.outputPath(`discussion-tools-${viewport.width}.png`), fullPage: true });
  }
});

test('teacher handles a local cultivation alert and reads back the saved result', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('.cw-learning-line [data-learning-modal-open]').click();
  const item = page.locator('[data-cultivation-alert-item]').filter({ hasText: 'QA isolated acceptance alert' });
  await expect(item).toBeVisible();
  const id = await item.getAttribute('data-alert-id');
  const saved = page.waitForResponse(response => response.url().endsWith(`/learning/alerts/${id}/actions`) && response.request().method() === 'POST');
  await item.locator('[data-cultivation-alert-action="handled"]').click();
  expect((await saved).status()).toBe(200);
  await expect(item).toHaveCount(0);
  await page.reload();
  await page.locator('.cw-learning-line [data-learning-modal-open]').click();
  await expect(item).toHaveCount(0);
  await expect(page.locator('[data-exam-roster-panel]')).toBeVisible();
  await expect(page.locator('[data-exam-roster-status]')).not.toContainText('正在读取');
});

test('teacher closes one chosen synthetic assignment and keeps skipped tasks open', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('summary[aria-label="打开更多课堂入口"]').click();
  const summary = page.waitForResponse(response => response.url().endsWith('/closeout/summary'));
  await page.locator('[data-classroom-closeout-open]').click();
  expect((await summary).status()).toBe(200);
  const modal = page.locator('#classroom-closeout-modal');
  await expect(modal).toBeVisible();
  const cards = modal.locator('[data-closeout-card]');
  await expect(cards.first()).toBeVisible();
  const scoreInput = cards.locator('[data-closeout-score-input]').first();
  const key = await scoreInput.getAttribute('data-closeout-score-input');
  expect(key).toBeTruthy();
  const [kind, id] = key!.split(':');
  const chosen = cards.filter({ has: page.locator(`[data-closeout-score-input="${key}"]`) });
  await expect(chosen).toBeVisible();
  for (const skip of await cards.locator('[data-closeout-skip]').all()) await skip.check();
  await chosen.locator('[data-closeout-skip]').uncheck();
  await chosen.locator('[data-closeout-score-input]').fill('37');
  page.once('dialog', dialog => dialog.accept());
  const executed = page.waitForResponse(response => response.url().endsWith('/closeout/execute') && response.request().method() === 'POST');
  await page.locator('#closeout-confirm-btn').click();
  const response = await executed;
  expect(response.status()).toBe(200);
  const request = response.request().postDataJSON();
  expect(request[kind][id].default_score).toBe(37);
  const payload = await response.json();
  expect(payload.processed[kind]).toBe(1);
  const after = await page.request.get(`/api/classroom/${fixture.classOfferingId}/closeout/summary`);
  expect(after.status()).toBe(200);
  const remaining = (await after.json()).cards;
  expect(remaining.some((card: any) => String(card.id) === id && card.kind === kind)).toBe(false);
  expect(remaining.length).toBeGreaterThan(0);
});

test('restricted materials block both the mobile batch control and a direct download request', async ({ page }) => {
  test.skip(!process.env.HOME_CLASSROOM_DOWNLOAD_LIMIT_TEST, 'Requires the isolated server download-size limit');
  const fixture = readFixture();
  await page.setViewportSize({ width: 390, height: 844 });
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('#cw-materials-preview').getByRole('button', { name: /全部课堂材料/ }).click();
  const rows = page.locator('#classroom-materials-list .materials-row');
  const restricted = page.locator('#classroom-materials-list .materials-row[data-material-download-allowed="false"]').first();
  await expect(restricted).toBeVisible();
  const id = Number(await restricted.getAttribute('data-id'));
  const all = page.getByRole('checkbox', { name: '选择本页全部材料' });
  await expect(all).toBeVisible();
  await all.check();
  await expect(page.locator('#classroom-materials-selection-count')).toHaveText(String(await rows.count()));
  await all.uncheck();
  await restricted.locator('[data-role="select-item"]').check();
  let batchRequests = 0;
  page.on('request', request => { if (request.url().endsWith('/api/materials/download')) batchRequests++; });
  await page.locator('#classroom-materials-download-btn').click();
  await expect(page.locator('.toast').filter({ hasText: '已限制下载' }).last()).toBeVisible();
  expect(batchRequests).toBe(0);
  const direct = await page.request.post('/api/materials/download', { data: { material_ids: [id] } });
  expect(direct.status()).toBe(403);
  expect((await direct.json()).detail).toContain('已限制下载');
});

test('student reads long requirements and submits a real attachment during the supplement window', async ({ page }) => {
  const fixture = readFixture();
  const assignment = JSON.parse(fs.readFileSync(path.join(fixture.runtimeRoot, 'long-assignment.json'), 'utf8'));
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
  await page.getByLabel('查找任务').fill(assignment.title);
  await page.locator(`[data-assignment-task-card][data-assignment-id="${assignment.id}"]`).click();
  await page.waitForURL(new RegExp(`/assignment/${assignment.id}(?:\\?|$)`));
  const requirements = page.locator('#requirements-content');
  await expect(requirements).toContainText('完整要求验收起点');
  const end = requirements.getByText('完整要求验收终点：提交前检查答案和附件。');
  await end.scrollIntoViewIfNeeded();
  await expect(end).toBeVisible();
  expect((await requirements.innerText()).length).toBeGreaterThan(4000);
  await expect(page.locator('#rubric-content')).toContainText('总分100分');
  await expect(page.locator('#drop-zone')).toContainText('单文件不超过');
  await expect(page.locator('[data-assignment-clock-detail]')).toContainText('80');
  for (const answer of await page.locator('.answer-textarea').all()) await answer.fill('QA complete long-task answer with a real text attachment.');
  await page.locator('#file-input').setInputFiles({ name: 'qa-long.txt', mimeType: 'text/plain', buffer: Buffer.from('Synthetic local attachment for the classroom acceptance workflow.') });
  await expect(page.locator('#file-chips')).toContainText('qa-long.txt');
  const submitted = page.waitForResponse(response => response.url().includes(`/api/assignments/${assignment.id}/submit`) && response.request().method() === 'POST');
  await page.getByTestId('p03-submit-assignment').click();
  expect((await submitted).status()).toBe(200);
  await page.reload();
  await expect(page.locator('#submitted-answers-container')).toContainText('QA complete long-task answer');
  await expect(page.getByText('qa-long.txt', { exact: true }).first()).toBeVisible();
});
