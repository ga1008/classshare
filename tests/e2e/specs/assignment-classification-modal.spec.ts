import { expect, test, type Locator, type Page } from '@playwright/test';
import { expectHealthUsesRuntimeDb, loginTeacher, readFixture } from '../fixtures/p03';

function classificationEndpoint(assignmentId: number) {
  return `/api/assignments/${assignmentId}/assessment-kind`;
}

async function setClassification(page: Page, assignmentId: number, kind: string) {
  const endpoint = classificationEndpoint(assignmentId);
  const current = await page.request.get(endpoint);
  expect(current.status()).toBe(200);
  const { assessment_kind_version: version } = await current.json();
  const saved = await page.request.patch(endpoint, { data: {
    assessment_kind: kind, expected_version: version,
  } });
  expect(saved.status()).toBe(200);
}

async function prepareAssignment(page: Page) {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  // Classification changes are allowed only in the isolated P03 database.
  await expectHealthUsesRuntimeDb(page, fixture);
  await setClassification(page, fixture.teacherReviewAssignmentId, 'homework');
  await page.goto(`/assignment/${fixture.teacherReviewAssignmentId}`);
  return fixture;
}

async function openClassification(page: Page, assignmentId: number) {
  await page.getByLabel('打开管理作业菜单', { exact: true }).click();
  const refreshed = page.waitForResponse(response =>
    new URL(response.url()).pathname === classificationEndpoint(assignmentId)
    && response.request().method() === 'GET');
  await page.getByRole('menuitem', { name: '作业分类', exact: true }).click();
  expect((await refreshed).status()).toBe(200);
  const dialog = page.getByRole('dialog', { name: '作业分类', exact: true });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('设置任务分类', { exact: true })).toBeEnabled();
  return dialog;
}

async function saveClassification(page: Page, surface: Locator, assignmentId: number) {
  const saved = page.waitForResponse(response =>
    new URL(response.url()).pathname === classificationEndpoint(assignmentId)
    && response.request().method() === 'PATCH');
  await surface.getByRole('button', { name: '保存分类', exact: true }).click();
  expect((await saved).status()).toBe(200);
}

async function expectDialogFitsViewport(dialog: Locator, width: number, height: number) {
  const layout = await dialog.evaluate(node => {
    const bounds = node.getBoundingClientRect();
    return {
      left: bounds.left, right: bounds.right, top: bounds.top, bottom: bounds.bottom,
      horizontalOverflow: node.scrollWidth - node.clientWidth,
      controlsOutside: Array.from(node.querySelectorAll('button, select')).some(control => {
        const box = control.getBoundingClientRect();
        return box.left < bounds.left || box.right > bounds.right
          || box.top < bounds.top || box.bottom > bounds.bottom;
      }),
    };
  });
  expect(layout.left).toBeGreaterThanOrEqual(0);
  expect(layout.right).toBeLessThanOrEqual(width + 1);
  expect(layout.top).toBeGreaterThanOrEqual(0);
  expect(layout.bottom).toBeLessThanOrEqual(height + 1);
  expect(layout.horizontalOverflow).toBeLessThanOrEqual(1);
  expect(layout.controlsOutside).toBe(false);
}

for (const viewport of [{ width: 1440, height: 980 }, { width: 390, height: 844 }]) {
  test.describe(`assignment classification at ${viewport.width}px`, () => {
    test.use({ viewport, hasTouch: viewport.width < 600 });

    test('management dialog saves, cancels and stays consistent with edit and classroom controls', async ({ page }, testInfo) => {
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      const fixture = await prepareAssignment(page);
      const assignmentId = fixture.teacherReviewAssignmentId;
      const detailLabel = page.locator('[data-assessment-detail-label]');
      const dialog = await openClassification(page, assignmentId);
      const select = dialog.getByLabel('设置任务分类', { exact: true });
      const save = dialog.getByRole('button', { name: '保存分类', exact: true });
      await expect(select).toHaveValue('homework');
      await expect(select).toBeFocused();
      await expect(save).toBeDisabled();
      await expectDialogFitsViewport(dialog, viewport.width, viewport.height);
      await page.screenshot({ path: testInfo.outputPath(`classification-${viewport.width}.png`) });

      await select.selectOption('final');
      await dialog.getByRole('button', { name: '取消', exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(page.getByLabel('打开管理作业菜单', { exact: true })).toBeFocused();
      await expect(detailLabel).toHaveText('平时作业');
      await openClassification(page, assignmentId);
      await expect(select).toHaveValue('homework');
      await select.selectOption('final');
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
      await openClassification(page, assignmentId);
      await expect(select).toHaveValue('homework');
      await select.selectOption('midterm');
      await saveClassification(page, dialog, assignmentId);
      await expect(dialog).toBeHidden();
      await expect(detailLabel).toHaveText('期中测验');

      await page.getByLabel('打开管理作业菜单', { exact: true }).click();
      await page.getByRole('menuitem', { name: '编辑作业', exact: true }).click();
      const edit = page.locator('#edit-modal');
      await expect(edit).toBeVisible();
      await expect(edit.getByLabel('设置任务分类', { exact: true })).toHaveValue('midterm');
      await expect(edit.getByRole('button', { name: '保存分类', exact: true })).toBeDisabled();
      const controlIds = await page.locator('[data-assessment-kind-select]').evaluateAll(nodes => nodes.map(node => node.id));
      expect(controlIds).toHaveLength(2);
      expect(new Set(controlIds).size).toBe(2);
      await page.reload();
      await expect(detailLabel).toHaveText('期中测验');

      // A change in the existing classroom control must also be reflected here.
      await page.goto(`/classroom/${fixture.classOfferingId}`);
      const tasks = page.getByRole('dialog', { name: '全部课堂任务', exact: true });
      if (!(await tasks.isVisible())) {
        await page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
      }
      await expect(tasks).toBeVisible();
      await tasks.locator('.cw-filterbar').getByLabel('任务状态').selectOption('all');
      await tasks.locator('.cw-filterbar').getByLabel('任务分类').selectOption('midterm');
      const card = tasks.locator(`[data-assignment-task-card][data-assignment-id="${assignmentId}"]`);
      await expect(card).toBeVisible();
      await expect(card.locator('[data-assessment-kind-label]')).toHaveText('期中测验');
      await card.locator('details.assignment-card-classification > summary').click();
      await card.getByLabel('设置任务分类', { exact: true }).selectOption('final');
      await saveClassification(page, card, assignmentId);
      await tasks.locator('.cw-filterbar').getByLabel('任务分类').selectOption('final');
      await expect(card.locator('[data-assessment-kind-label]')).toHaveText('期末测验');
      await card.locator('.assignment-card-primary-link').click();
      await expect(page).toHaveURL(new RegExp(`/assignment/${assignmentId}(?:\\?|$)`));
      await expect(detailLabel).toHaveText('期末测验');
      await openClassification(page, assignmentId);
      await expect(select).toHaveValue('final');
      await expect(save).toBeDisabled();
      await dialog.getByRole('button', { name: '关闭作业分类', exact: true }).click();
      expect(errors).toEqual([]);
    });
  });
}

test('failed save keeps the selection and prevents duplicate submission or dismissal while retrying', async ({ page }) => {
  const fixture = await prepareAssignment(page);
  const assignmentId = fixture.teacherReviewAssignmentId;
  const endpoint = classificationEndpoint(assignmentId);
  const dialog = await openClassification(page, assignmentId);
  const select = dialog.getByLabel('设置任务分类', { exact: true });
  await select.selectOption('midterm');
  let attempts = 0;
  let releaseRetry!: () => void;
  const retryGate = new Promise<void>(resolve => { releaseRetry = resolve; });
  await page.route(`**${endpoint}`, async route => {
    if (route.request().method() !== 'PATCH') return route.continue();
    attempts += 1;
    if (attempts === 1) {
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '服务暂不可用，请重试' }) });
    }
    await retryGate;
    await route.continue();
  });
  const save = dialog.locator('[data-assessment-kind-save]');
  await save.click();
  await expect(dialog.locator('[data-assessment-kind-note]')).toContainText('服务暂不可用');
  await expect(dialog).toBeVisible();
  await expect(select).toHaveValue('midterm');
  await expect(save).toBeEnabled();
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('平时作业');

  await save.click();
  try {
    await expect.poll(() => attempts).toBe(2);
    await expect(save).toBeDisabled();
    await expect(select).toBeDisabled();
    await expect(dialog.getByRole('button', { name: '取消', exact: true })).toBeDisabled();
    await expect(dialog.getByRole('button', { name: '关闭作业分类', exact: true })).toBeDisabled();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeVisible();
    expect(attempts).toBe(2);
  } finally {
    releaseRetry();
  }
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('期中测验');
  const persisted = await page.request.get(endpoint);
  expect((await persisted.json()).assessment_kind).toBe('midterm');
});

test('concurrent classification changes refresh on open and require an explicit choice after a conflict', async ({ page }) => {
  const fixture = await prepareAssignment(page);
  const assignmentId = fixture.teacherReviewAssignmentId;
  const endpoint = classificationEndpoint(assignmentId);
  // The statistics page remains open while another authorized page changes the value.
  await setClassification(page, assignmentId, 'final');
  const dialog = await openClassification(page, assignmentId);
  const select = dialog.getByLabel('设置任务分类', { exact: true });
  const save = dialog.getByRole('button', { name: '保存分类', exact: true });
  await expect(select).toHaveValue('final');
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('期末测验');
  await select.selectOption('midterm');
  await setClassification(page, assignmentId, 'homework');
  const conflict = page.waitForResponse(response =>
    new URL(response.url()).pathname === endpoint && response.request().method() === 'PATCH');
  await save.click();
  expect((await conflict).status()).toBe(409);
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-assessment-kind-note]')).toContainText('请重新选择并保存');
  await expect(select).toHaveValue('homework');
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('平时作业');
  await expect(save).toBeDisabled();
  await select.selectOption('midterm');
  await saveClassification(page, dialog, assignmentId);
  await expect(dialog).toBeHidden();
  await expect(page.locator('[data-assessment-detail-label]')).toHaveText('期中测验');
  const persisted = await page.request.get(endpoint);
  expect((await persisted.json()).assessment_kind).toBe('midterm');
});

test('a slow classification read can be cancelled and its late response cannot disturb a reopened dialog', async ({ page }) => {
  const fixture = await prepareAssignment(page);
  const assignmentId = fixture.teacherReviewAssignmentId;
  const endpoint = classificationEndpoint(assignmentId);
  let reads = 0;
  let releaseFirstRead!: () => void;
  let finishFirstRead!: () => void;
  const firstReadGate = new Promise<void>(resolve => { releaseFirstRead = resolve; });
  const firstReadFinished = new Promise<void>(resolve => { finishFirstRead = resolve; });
  await page.route(`**${endpoint}`, async route => {
    if (route.request().method() !== 'GET' || ++reads !== 1) return route.continue();
    const response = await route.fetch();
    await firstReadGate;
    try {
      await route.fulfill({ response });
    } finally {
      finishFirstRead();
    }
  });
  const dialog = page.getByRole('dialog', { name: '作业分类', exact: true });
  const select = dialog.getByLabel('设置任务分类', { exact: true });
  const cancel = dialog.getByRole('button', { name: '取消', exact: true });
  try {
    await page.getByLabel('打开管理作业菜单', { exact: true }).click();
    await page.getByRole('menuitem', { name: '作业分类', exact: true }).click();
    await expect.poll(() => reads).toBe(1);
    await expect(dialog).toBeVisible();
    await expect(select).toBeDisabled();
    await expect(cancel).toBeEnabled();
    await expect(dialog.getByRole('button', { name: '关闭作业分类', exact: true })).toBeEnabled();
    await cancel.click();
    await expect(dialog).toBeHidden();
    await expect(page.getByLabel('打开管理作业菜单', { exact: true })).toBeFocused();

    await openClassification(page, assignmentId);
    expect(reads).toBe(2);
    await expect(select).toHaveValue('homework');
    await select.selectOption('midterm');
    await cancel.focus();
    releaseFirstRead();
    await firstReadFinished;
    // Let queued network and dialog callbacks finish before checking the user draft.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(dialog).toBeVisible();
    await expect(select).toBeEnabled();
    await expect(select).toHaveValue('midterm');
    await expect(cancel).toBeFocused();
    await expect(dialog.getByRole('button', { name: '保存分类', exact: true })).toBeEnabled();
    await expect(dialog.locator('[data-assessment-kind-note]')).not.toContainText(/重试|读取|失败/);
    await expect(page.locator('[data-assessment-detail-label]')).toHaveText('平时作业');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  } finally {
    releaseFirstRead();
  }
});
