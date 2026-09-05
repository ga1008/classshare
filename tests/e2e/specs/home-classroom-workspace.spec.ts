import { expect, test } from '@playwright/test';
import { loginStudent, readFixture } from '../fixtures/p03';

test('homepage complete items retain paging and show a usable empty filter', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  const response = await page.request.get('/api/dashboard/workspace?limit=20');
  expect(response.status()).toBe(200);
  const { workspace } = await response.json();
  const opener = page.locator('.dw-focus').getByRole('button', { name: /^全部事项/ });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '日程与事项' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.dw-all-items')).toHaveAttribute('aria-busy', 'false');
  await expect(dialog.locator('.dw-agenda-row')).toHaveCount(Math.min(workspace.filtered_total, 20));
  if (workspace.filtered_total > 20) {
    await dialog.getByRole('button', { name: '下一页' }).click();
    await expect(dialog.locator('.dw-pagination')).toContainText('第 2 /');
    await expect(dialog.locator('.dw-all-items')).toHaveAttribute('aria-busy', 'false');
    const titles = await dialog.locator('.dw-item-copy strong').allTextContents();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await expect(dialog.locator('.dw-pagination')).toContainText('第 2 /');
    await expect(dialog.locator('.dw-item-copy strong')).toHaveText(titles);
  }
  await dialog.getByLabel('搜索事项').fill('QA-no-such-item-unique-876543');
  await expect(dialog.locator('.dw-list-summary')).toContainText('共 0 项');
  await expect(dialog.locator('.dw-agenda-row')).toHaveCount(0);
  await dialog.getByRole('button', { name: '清除筛选' }).click();
  await expect(dialog.locator('.dw-list-summary')).toContainText(`共 ${workspace.filtered_total} 项`);
});

test('classroom reopens one task surface without multiplying listeners or sockets and restores task navigation', async ({ page, context }) => {
  const fixture = readFixture();
  let sockets = 0;
  const errors: string[] = [];
  page.on('websocket', () => sockets++);
  page.on('pageerror', error => errors.push(error.message));
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const opener = page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ });
  const dialog = page.getByRole('dialog', { name: '全部课堂任务' });
  await expect(opener).toBeVisible();
  await expect(page.locator('#ws-status')).toHaveClass(/status-online/);
  await page.locator('#classroom-activity-tab-discussion').click();
  const draft = 'QA unsent discussion draft preserved across task panels';
  await page.locator('#chat-input').fill(draft);
  await page.evaluate(() => { (window as any).__qaTaskSurface = document.querySelector('[data-cw-source="tasks"]'); });
  const cdp = await context.newCDPSession(page);
  const listenerCount = async () => {
    const result = await cdp.send('Runtime.evaluate', {
      expression: 'Object.values(getEventListeners(document)).reduce((total, listeners) => total + listeners.length, 0)',
      includeCommandLineAPI: true, returnByValue: true,
    });
    return Number(result.result.value);
  };
  await opener.click(); await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape'); await expect(dialog).toBeHidden();
  const before = { listeners: await listenerCount(), sockets };
  for (let index = 0; index < 20; index++) {
    await opener.click();
    await expect(dialog).toBeVisible();
    await expect(page.locator('[data-cw-source="tasks"]')).toHaveCount(1);
    expect(await page.evaluate(() => (window as any).__qaTaskSurface === document.querySelector('[data-cw-source="tasks"]'))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  }
  expect(await listenerCount()).toBeLessThanOrEqual(before.listeners);
  expect(sockets).toBe(before.sockets);
  await expect(page.locator('#chat-input')).toHaveValue(draft);
  await opener.click();
  await dialog.getByLabel('查找任务').fill('P03');
  const target = dialog.locator(`[data-assignment-task-card][data-assignment-id="${fixture.studentSubmissionAssignmentId}"]`);
  await expect(target).toBeVisible();
  await target.click();
  await page.waitForURL(new RegExp(`/assignment/${fixture.studentSubmissionAssignmentId}(?:\\?|$)`));
  await page.goBack();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('查找任务')).toHaveValue('P03');
  await expect(page.locator('[data-cw-source="tasks"]')).toHaveCount(1);
  expect(errors).toEqual([]);
  await cdp.detach();
});

test('selected lesson materials recover from failure and preserve viewer attribution without changing classroom tasks', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const timeline = page.locator('[data-cw-source="timeline"]');
  test.skip(await timeline.count() === 0, 'Fixture has no teaching timeline; the populated visual fixture covers this case.');
  const initialTasks = await page.locator('#cw-tasks-preview .cw-row-copy strong').allTextContents();
  const routePattern = '**/learning-materials?**';
  const materialRequests: URL[] = [];
  page.on('request', request => { if (request.url().includes('/learning-materials?')) materialRequests.push(new URL(request.url())); });
  await page.route(routePattern, route => route.fulfill({ status: 503, body: '{}' }));
  await page.getByRole('button', { name: '全部课次', exact: true }).click();
  await timeline.locator('[data-session-select].is-home-entry').click();
  const materials = page.locator('#cw-materials-preview');
  await expect(materials.getByRole('alert')).toContainText('材料读取失败');
  await page.unroute(routePattern);
  await materials.getByRole('button', { name: '重试' }).click();
  await expect(materials.getByRole('alert')).toHaveCount(0);
  await expect(materials.getByRole('status')).toHaveCount(0);
  expect(materialRequests.length).toBeGreaterThanOrEqual(2);
  for (const request of materialRequests) {
    expect(request.searchParams.get('session_id')).toBe('0');
    expect(request.searchParams.get('generate_blurbs')).toBe('false');
  }
  const link = materials.getByRole('link', { name: /^阅读/ }).first();
  if (await link.count()) {
    const url = new URL((await link.getAttribute('href'))!, page.url());
    expect(url.searchParams.get('class_offering_id')).toBe(String(fixture.classOfferingId));
    // Homepage binding uses 0, while the established reading attribution uses
    // no lesson (null). Real lessons retain their positive session ID.
    expect(url.searchParams.get('session_id')).toBeNull();
    const opened = await page.context().newPage();
    const response = await opened.goto(url.toString());
    expect(response?.status()).toBe(200);
    await opened.close();
  }
  await expect(page.locator('#cw-tasks-preview .cw-row-copy strong')).toHaveText(initialTasks);
});

test.describe('touch classroom workspace', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test('primary controls have usable touch targets and explicit help, and urgent filtering excludes undated work', async ({ page }) => {
    const fixture = readFixture();
    await loginStudent(page, fixture);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    const tasks = page.locator('#cw-tasks-preview');
    const help = tasks.getByRole('button', { name: '课堂任务说明', exact: true });
    await expect(help).toBeVisible();
    expect(await page.evaluate(() => matchMedia('(pointer: coarse)').matches)).toBe(true);
    for (const control of [help, tasks.getByRole('button', { name: /全部任务/ }), page.locator('#teachingTimelineNextBtn')]) {
      const rect = await control.boundingBox();
      expect(rect!.width).toBeGreaterThanOrEqual(44);
      expect(rect!.height).toBeGreaterThanOrEqual(44);
    }
    await help.click();
    const explanation = page.locator('.ui-explain-popover');
    await expect(explanation).toBeVisible();
    await expect(explanation).toContainText('不随所选课次筛选');
    await page.keyboard.press('Escape');
    await expect(explanation).toBeHidden();
    await tasks.getByRole('button', { name: /全部任务/ }).click();
    const dialog = page.getByRole('dialog', { name: '全部课堂任务' });
    await dialog.getByLabel('任务状态').selectOption('urgent');
    await expect(dialog.getByLabel('任务状态')).toHaveValue('urgent');
    const visibleIds = await dialog.locator('[data-assignment-task-card]:visible').evaluateAll(nodes => nodes.map(node => Number((node as HTMLElement).dataset.assignmentId)));
    const invalidIds = await page.evaluate(() => {
      const tasks = (window as any).APP_CONFIG.assignmentWorkspaceItems as Array<{ id: number; countdownAt: string; resubmissionDueAt: string; canResubmit: boolean }>;
      return tasks.filter(task => !(task.canResubmit ? task.resubmissionDueAt || task.countdownAt : task.countdownAt)).map(task => task.id);
    });
    expect(visibleIds.filter(id => invalidIds.includes(id))).toEqual([]);
    const dimensions = await dialog.boundingBox();
    expect(dimensions!.x).toBeGreaterThanOrEqual(0);
    expect(dimensions!.x + dimensions!.width).toBeLessThanOrEqual(390);
  });
});
