import { expect, test, type Locator } from '@playwright/test';
import { expectHealthUsesRuntimeDb, loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

test('homepage complete items retain paging and show a usable empty filter', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  const response = await page.request.get('/api/dashboard/workspace?limit=20');
  expect(response.status()).toBe(200);
  const { workspace } = await response.json();
  const opener = page.locator('.ls-focus').getByRole('button', { name: /^全部事项/ });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '日程与事项' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.ls-all-items')).toHaveAttribute('aria-busy', 'false');
  await expect(dialog.locator('.ls-agenda-row')).toHaveCount(Math.min(workspace.filtered_total, 20));
  if (workspace.filtered_total > 20) {
    await dialog.getByRole('button', { name: '下一页' }).click();
    await expect(dialog.locator('.ls-pagination')).toContainText('第 2 /');
    await expect(dialog.locator('.ls-all-items')).toHaveAttribute('aria-busy', 'false');
    const titles = await dialog.locator('.ls-item-copy strong').allTextContents();
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await opener.click();
    await expect(dialog.locator('.ls-pagination')).toContainText('第 2 /');
    await expect(dialog.locator('.ls-item-copy strong')).toHaveText(titles);
  }
  await dialog.getByLabel('搜索事项').fill('QA-no-such-item-unique-876543');
  await expect(dialog.locator('.ls-list-summary')).toContainText('共 0 项');
  await expect(dialog.locator('.ls-agenda-row')).toHaveCount(0);
  await dialog.getByRole('button', { name: '清除筛选' }).click();
  await expect(dialog.locator('.ls-list-summary')).toContainText(`共 ${workspace.filtered_total} 项`);
});

test('teacher task lists show applicant counts and update on return from a decision', async ({ page, browser, baseURL }, testInfo) => {
  const fixture = readFixture();
  const classroom = `/classroom/${fixture.classOfferingId}`;
  const pendingId = fixture.aiStopAssignmentId;
  const processedId = fixture.aiSuccessAssignmentId;
  const student = await browser.newPage({ baseURL });
  try {
    await loginStudent(student, fixture);
    await expectHealthUsesRuntimeDb(student, fixture);
    await loginTeacher(page, fixture);
    await expectHealthUsesRuntimeDb(page, fixture);

    async function apply(assignmentId: number) {
      const response = await student.request.post('/api/approvals', { data: {
        request_type: 'submission_correction', subject_id: `${assignmentId}:${fixture.student.id}`,
        reason: '合成验收，请核对提交内容。',
      } });
      await expect(response).toBeOK();
      return (await response.json()).request.id as number;
    }
    for (let attempt = 0; attempt < 2; attempt++) {
      const id = await apply(processedId);
      await expect(await page.request.post(`/api/approvals/${id}/reject`, { data: { note: '合成验收，保留原提交。' } })).toBeOK();
    }
    await expect(await page.request.post(`/api/submissions/${fixture.aiStopSubmissionId}/grade`, {
      data: { score: 90, feedback_md: '合成验收，批改已完成。' },
    })).toBeOK();
    const pendingRequest = await apply(pendingId);
    const preview = page.locator('#cw-tasks-preview');
    const dialog = page.getByRole('dialog', { name: '全部课堂任务' });
    const card = (list: Locator, id: number) => list.locator(`[data-lq-component="surface"][data-assignment-id="${id}"]`);
    const pending = '申请待处理 1 人', processed = '申请已处理 1 人';
    const countText = /申请(?:待处理|已处理)\s*\d+\s*人/;

    await student.goto(classroom);
    const studentPreview = student.locator('#cw-tasks-preview');
    await studentPreview.getByRole('button', { name: /^全部\s*\d/ }).click();
    await expect(card(studentPreview, pendingId)).toBeVisible();
    await expect(studentPreview.getByText(countText)).toHaveCount(0);
    await studentPreview.getByRole('button', { name: /全部任务/ }).click();
    const studentList = student.getByRole('dialog', { name: '全部课堂任务' });
    await expect(card(studentList, pendingId)).toBeVisible();
    await expect(studentList.getByText(countText)).toHaveCount(0);

    const detailRequests: string[] = [];
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/api/approvals') detailRequests.push(request.url());
    });
    await page.goto(classroom);
    await expect(card(preview, pendingId).getByText(pending, { exact: true })).toBeVisible();
    await preview.getByRole('button', { name: /^全部\s*\d/ }).click();
    async function checkCounts(list: Locator) {
      const waiting = card(list, pendingId).getByText(pending, { exact: true });
      const done = card(list, processedId).getByText(processed, { exact: true });
      await expect(waiting).toBeVisible();
      await expect(done).toBeVisible();
      await expect(card(list, fixture.studentSubmissionAssignmentId).getByText(countText)).toHaveCount(0);
      const colors: string[] = [];
      for (const badge of [waiting, done]) {
        colors.push(await badge.evaluate(el => {
          const style = getComputedStyle(el);
          return [style.color, style.backgroundColor, style.borderColor].join(' ');
        }));
        const bounds = await badge.boundingBox();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
      }
      expect(colors[0]).not.toBe(colors[1]);
    }
    for (const [width, appearance] of [[1440, 'light'], [390, 'dark']] as const) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 980 });
      await page.emulateMedia({ colorScheme: appearance });
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      await checkCounts(preview);
      await testInfo.attach(`preview-${width}`, { body: await card(preview, pendingId).screenshot(), contentType: 'image/png' });
      await preview.getByRole('button', { name: /全部任务/ }).click();
      await checkCounts(dialog);
      await testInfo.attach(`collection-${width}`, { body: await card(dialog, processedId).screenshot(), contentType: 'image/png' });
      await page.keyboard.press('Escape');
      await expect(dialog).toBeHidden();
    }
    expect(detailRequests).toEqual([]);
    await preview.getByRole('button', { name: /全部任务/ }).click();
    await card(dialog, pendingId).locator('.assignment-card-primary-link').click();
    await page.locator('.apr-drawer-toggle').click();
    await page.locator(`[data-apr-open="${pendingRequest}"]`).click();
    await page.locator('[data-apr-field="note"]').fill('合成验收，保留原提交。');
    await page.locator('[data-apr-decide="reject"]').click();
    await expect(page.locator('[data-apr-modal-body]')).toContainText('处理结果');
    await page.goBack();
    await expect(card(dialog, pendingId).getByText(processed, { exact: true })).toBeVisible();
    await expect(card(dialog, pendingId).getByText(pending, { exact: true })).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(card(preview, pendingId).getByText(processed, { exact: true })).toBeVisible();

    // Keep this document in place to exercise the persisted pageshow branch even
    // when the headless browser excludes a websocket page from its back/forward cache.
    await page.locator('#classroom-activity-tab-discussion').click();
    await page.locator('#chat-input').fill('还未发送的课堂草稿');
    await card(preview, processedId).locator('.cw-task-title').evaluate(link => {
      link.addEventListener('click', event => event.preventDefault(), { once: true });
      (link as HTMLAnchorElement).click();
    });
    await apply(processedId);
    const refresh = page.waitForResponse(response =>
      response.url().endsWith(`/api/approvals/assignments/${processedId}/applicant-counts`));
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true })));
    expect((await refresh).status()).toBe(200);
    await expect(card(preview, processedId).getByText(pending, { exact: true })).toBeVisible();
    await expect(card(preview, processedId).getByText(processed, { exact: true })).toHaveCount(0);
    await expect(page.locator('#chat-input')).toHaveValue('还未发送的课堂草稿');
  } finally {
    await student.close();
  }
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
  // This fixture may already have been submitted by the real submission test.
  // Exercise collection/filter restoration without assuming it is still pending.
  await dialog.getByLabel('任务状态').selectOption('all');
  await dialog.getByLabel('查找任务').fill('P03');
  const target = dialog.locator(`[data-assignment-task-card][data-assignment-id="${fixture.studentSubmissionAssignmentId}"]`);
  await expect(target).toBeVisible();
  await target.click();
  await page.waitForURL(new RegExp(`/assignment/${fixture.studentSubmissionAssignmentId}(?:\\?|$)`));
  await page.goBack();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel('任务状态')).toHaveValue('all');
  await expect(dialog.getByLabel('查找任务')).toHaveValue('P03');
  await expect(page.locator('[data-cw-source="tasks"]')).toHaveCount(1);
  expect(errors).toEqual([]);
  await cdp.detach();
});

test('selected lesson materials recover from failure and preserve viewer attribution without changing classroom tasks', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const timeline = page.locator('#teachingTimelineScroll');
  await expect(timeline).toBeVisible();
  const initialTasks = await page.locator('#cw-tasks-preview .cw-task-title').allTextContents();
  const routePattern = '**/learning-materials?**';
  const materialRequests: URL[] = [];
  page.on('request', request => { if (request.url().includes('/learning-materials?')) materialRequests.push(new URL(request.url())); });
  const actual = await page.request.get(`/api/classrooms/${fixture.classOfferingId}/learning-materials?session_id=0&generate_blurbs=false`);
  expect(actual.status()).toBe(200);
  const collection = (await actual.json()).materials as Array<{ open_url: string }>;
  await page.route(routePattern, route => route.fulfill({ status: 503, body: '{}' }));
  await timeline.locator('[data-session-select].is-home-entry').click();
  const entry = page.locator('#teachingSessionOpenMaterialBtn');
  await expect(page.locator('#cw-session-material-status')).toContainText('材料读取失败');
  await page.unroute(routePattern);
  await entry.click();
  await expect(entry).toBeEnabled();
  if (collection.length === 1) {
    const popup = page.waitForEvent('popup');
    await entry.click();
    const reader = await popup;
    await reader.waitForURL(/\/materials\/(?:view|render-view)\//);
    const url = new URL(reader.url());
    expect(url.searchParams.get('class_offering_id')).toBe(String(fixture.classOfferingId));
    expect(url.searchParams.get('session_id')).toBeNull();
    await reader.close();
    await expect(entry).toBeVisible();
  } else if (collection.length > 1) {
    await entry.click();
    const list = page.locator('.ls-mat-popup');
    await expect(list).toBeVisible();
    await expect(list.locator('[data-open-material]')).toHaveCount(collection.length);
    const popup = page.waitForEvent('popup');
    await list.locator('[data-open-material]').first().click();
    const reader = await popup;
    await reader.waitForURL(/\/materials\/(?:view|render-view)\//);
    const url = new URL(reader.url());
    expect(url.searchParams.get('class_offering_id')).toBe(String(fixture.classOfferingId));
    expect(url.searchParams.get('session_id')).toBeNull();
    await reader.close();
    await list.locator('[data-close-mat-popup]').click();
    await expect(entry).toBeVisible();
  } else {
    await expect(page.locator('#cw-session-material-status')).toContainText('暂无可访问');
    await expect(page.locator('.ls-mat-popup')).toBeHidden();
  }
  expect(materialRequests.length).toBeGreaterThanOrEqual(2);
  for (const request of materialRequests) {
    expect(request.searchParams.get('session_id')).toBe('0');
    expect(request.searchParams.get('generate_blurbs')).toBe('false');
  }
  await expect(page.locator('#cw-tasks-preview .cw-task-title')).toHaveText(initialTasks);
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
