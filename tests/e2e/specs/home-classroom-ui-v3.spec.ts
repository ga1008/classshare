import { expect, test, type Page } from '@playwright/test';
import { loginStudent, loginTeacher, readFixture, expectHealthUsesRuntimeDb, type P03Fixture } from '../fixtures/p03';

type V3Fixture = P03Fixture & { uiV3Synthetic: boolean; visualSessionIds: number[]; visualMaterialIds: number[]; additionalOfferingIds: number[] };
const fixture = () => readFixture() as V3Fixture;
const palette = (page: Page) => page.getByRole('combobox', { name: '界面配色', exact: true });

test.beforeAll(() => { expect(fixture().uiV3Synthetic).toBe(true); });

async function changePalette(page: Page, value: string) {
  const saved = page.waitForResponse(response => response.url().endsWith('/api/profile/ui-preferences') && response.request().method() === 'PATCH');
  await palette(page).selectOption(value);
  expect((await saved).status()).toBe(200);
  await expect(page.locator('body')).toHaveAttribute('data-ui-palette', value);
}

test('one palette control persists through SSR, classroom and a new browser context', async ({ page, browser }) => {
  const f = fixture();
  await loginStudent(page, f);
  await expectHealthUsesRuntimeDb(page, f);
  await expect(palette(page)).toHaveCount(1);
  await expect(palette(page).locator('option')).toHaveCount(5);
  let businessRequests = 0;
  page.on('request', request => { if (/course-schedule\/overview|dashboard\/workspace|learning-materials\?/.test(request.url())) businessRequests++; });
  const value = await palette(page).inputValue() === 'sky' ? 'mint' : 'sky';
  const before = businessRequests;
  await changePalette(page, value);
  expect(businessRequests).toBe(before);
  const response = await page.goto(`/classroom/${f.classOfferingId}`);
  expect(await response!.text()).toContain(`data-ui-palette="${value}"`);
  await expect(palette(page)).toHaveValue(value);
  const context = await browser.newContext();
  try {
    const fresh = await context.newPage();
    await loginStudent(fresh, f);
    await expect(palette(fresh)).toHaveValue(value);
    await changePalette(fresh, 'indigo');
  } finally { await context.close(); }
});

test('a stale account tab cannot change the newly logged-in student palette', async ({ page, context }) => {
  const f = fixture();
  await loginStudent(page, f);
  const oldContext = await page.locator('body').getAttribute('data-ui-palette-context');
  const second = await context.newPage();
  await loginStudent(second, f, f.otherStudent);
  expect(await second.locator('body').getAttribute('data-ui-palette-context')).not.toBe(oldContext);
  const before = (await (await second.request.get('/api/profile/ui-preferences')).json()).preferences;
  const denied = page.waitForResponse(response => response.url().endsWith('/api/profile/ui-preferences') && response.request().method() === 'PATCH');
  await palette(page).selectOption('rose');
  expect((await denied).status()).toBe(409);
  await expect(palette(page)).toBeDisabled();
  await expect(page.locator('[data-ui-palette-status]')).toContainText('登录账号已变化');
  const after = (await (await second.request.get('/api/profile/ui-preferences')).json()).preferences;
  expect(after).toEqual(before);
});

test('palette save failure keeps the preview and supports retry without disturbing the page', async ({ page }) => {
  await loginStudent(page, fixture());
  let fail = true;
  await page.route('**/api/profile/ui-preferences', async route => {
    if (fail && route.request().method() === 'PATCH') await route.fulfill({ status: 503, json: { detail: '暂时无法同步' } });
    else await route.continue();
  });
  await palette(page).selectOption('violet');
  await expect(page.locator('body')).toHaveAttribute('data-ui-palette', 'violet');
  await expect(page.locator('[data-ui-palette-status]')).toContainText('未同步');
  fail = false;
  await changePalette(page, 'mint');
  await page.reload();
  await expect(palette(page)).toHaveValue('mint');
});

test('single material opens the real reader directly while compact lesson details and unsent draft remain', async ({ page }) => {
  const f = fixture();
  await loginStudent(page, f);
  await page.goto(`/classroom/${f.classOfferingId}`);
  await page.locator('#classroom-activity-tab-discussion').click();
  await page.locator('#chat-input').fill('阅读期间保留的草稿');
  const card = page.locator(`#teachingTimelineScroll [data-session-select][data-session-id="${f.visualSessionIds[1]}"]`);
  await card.click();
  const button = page.locator('#teachingSessionOpenMaterialBtn');
  await expect(button).toBeVisible();
  const geometry = await button.boundingBox();
  const dialog = await page.locator('.cw-dialog').boundingBox();
  expect(geometry!.height).toBeLessThanOrEqual(40);
  expect(geometry!.width).toBeLessThan(dialog!.width * .65);
  await expect(page.locator('#cw-materials-preview')).toHaveCount(0);
  const opened = page.waitForEvent('popup');
  await button.click();
  const reader = await opened;
  await reader.waitForURL(/\/materials\/(?:view|render-view)\//);
  const url = new URL(reader.url());
  expect(url.searchParams.get('class_offering_id')).toBe(String(f.classOfferingId));
  expect(url.searchParams.get('session_id')).toBe(String(f.visualSessionIds[1]));
  await expect(reader.getByText('这是第三版真实阅读验证使用的合成材料。', { exact: false }).first()).toBeVisible();
  const closed = reader.waitForEvent('close');
  await reader.locator('[data-classroom-reader-return]').click();
  await closed;
  await expect(button).toBeVisible();
  await expect(page.locator('.ls-mat-popup')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(card).toBeFocused();
  await expect(page.locator('#chat-input')).toHaveValue('阅读期间保留的草稿');
});

test('multiple materials use a list, zero materials stay in lesson details, and task scope stays unchanged', async ({ page }) => {
  const f = fixture();
  await loginStudent(page, f);
  await page.goto(`/classroom/${f.classOfferingId}`);
  const tasks = await page.locator('#cw-tasks-preview .cw-task-title').allTextContents();
  await page.locator(`#teachingTimelineScroll [data-session-id="${f.visualSessionIds[2]}"]`).click();
  await page.locator('#teachingSessionOpenMaterialBtn').click();
  const list = page.locator('.ls-mat-popup');
  await expect(list).toBeVisible();
  await expect(list.locator('[data-open-material]')).toHaveCount(2);
  const popup = page.waitForEvent('popup');
  await list.locator('[data-open-material]').first().click();
  const reader = await popup;
  await reader.waitForURL(/\/materials\/(?:view|render-view)\//);
  expect(reader.url()).toMatch(/\/materials\/(?:view|render-view)\//);
  const closed = reader.waitForEvent('close');
  await reader.locator('[data-classroom-reader-return]').click();
  await closed;
  await expect(list).toBeVisible();
  await list.locator('[data-close-mat-popup]').click();
  await expect(page.locator('#teachingSessionOpenMaterialBtn')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.locator(`#teachingTimelineScroll [data-session-id="${f.visualSessionIds[3]}"]`).click();
  await page.locator('#teachingSessionOpenMaterialBtn').click();
  await expect(page.locator('#cw-session-material-status')).toContainText(/暂无|尚未|没有/);
  await expect(list).toBeHidden();
  await expect(page.locator('#cw-tasks-preview .cw-task-title')).toHaveText(tasks);
});

test('course cultivation is a topbar value and teachers keep their original role boundary', async ({ page }) => {
  const f = fixture();
  await loginStudent(page, f);
  await page.goto(`/classroom/${f.classOfferingId}`);
  const entry = page.locator('.cw-cultivation-entry[data-learning-modal-open]');
  await expect(entry).toContainText(/本课修为\s*[\d.]+/);
  await entry.click();
  await expect(page.locator('#learning-progress-modal')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(entry).toBeFocused();
  await loginTeacher(page, f);
  await page.goto(`/classroom/${f.classOfferingId}`);
  await expect(palette(page)).toHaveCount(0);
  expect((await page.request.get('/api/profile/ui-preferences')).status()).toBe(403);
  await expect(page.locator('#classroom-final-material-generate-btn')).toHaveCount(1);
});

test('home keeps one 3D deck and complete course access across views and filters', async ({ page }) => {
  const f = fixture();
  await loginStudent(page, f);
  const schedule = page.locator('[data-student-schedule]');
  const stage = schedule.locator('[data-csd-stage]');
  await expect(stage).toBeVisible();
  await expect(stage.locator('.cs-card.is-active')).toHaveCount(1);
  await expect(page.getByRole('heading', { name: '我的课堂', exact: true })).toHaveCount(0);
  const selectedWeek = await schedule.locator('[data-student-week-label]').textContent();
  await schedule.locator('[data-student-schedule-mode="agenda"]').click();
  await expect(schedule.locator('[data-student-schedule-agenda]')).toBeVisible();
  await expect(schedule.locator('[data-student-week-label]')).toHaveText(selectedWeek!);
  await schedule.locator('[data-student-schedule-mode="courses"]').click();
  const cards = schedule.locator('.dw-schedule-course');
  await expect(cards).toHaveCount(3);
  await expect(cards.filter({ hasText: '学术写作' })).toBeVisible();
  await expect(cards.filter({ hasText: '高等数学（往期）' })).toBeVisible();
  const attention = await page.locator('.dw-focus').textContent();
  await schedule.locator('[data-student-course-state]').selectOption('history');
  await expect(cards).toHaveCount(1);
  expect(await page.locator('.dw-focus').textContent()).toBe(attention);
  await schedule.locator('[data-student-course-search]').fill('不存在的课程');
  await expect(schedule.getByText('没有匹配的课程', { exact: true })).toBeVisible();
  await schedule.getByRole('button', { name: '清除集合筛选', exact: true }).click();
  await expect(cards).toHaveCount(3);
  await schedule.locator('[data-student-schedule-mode="3d"]').click();
  await expect(stage).toBeVisible();
  await expect(page.locator('[data-csd-stage]')).toHaveCount(1);
});

test('wheel and keyboard change weeks with 3D motion while zoom and deck boundaries escape', async ({ page }) => {
  await loginStudent(page, fixture());
  const stage = page.locator('[data-student-schedule] [data-csd-stage]');
  const label = page.locator('[data-student-week-label]');
  await expect(stage.locator('.cs-card.is-active')).toHaveCount(1);
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  const before = await label.textContent();
  await stage.hover();
  await page.mouse.wheel(0, 160);
  await expect(label).not.toHaveText(before!);
  expect(await stage.evaluate(node => getComputedStyle(node).perspective)).not.toBe('none');
  const changed = await label.textContent();
  await stage.dispatchEvent('wheel', { deltaY: 160, ctrlKey: true });
  await expect(label).toHaveText(changed!);
  await stage.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(label).toHaveText(before!);
  const active = stage.locator('.cs-card.is-active');
  expect(await active.evaluate(node => getComputedStyle(node).transitionDuration)).not.toBe('0s');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.keyboard.press('ArrowRight');
  await expect(label).toHaveText(changed!);
  // Boundary behavior is observable through cancellation, without depending on page scroll height.
  for (let index = 0; index < 25; index++) await page.keyboard.press('ArrowLeft');
  await expect(page.locator('[data-student-week-prev]')).toBeDisabled();
  expect(await stage.evaluate(node => node.dispatchEvent(new WheelEvent('wheel', { deltaY: -120, bubbles: true, cancelable: true })))).toBe(true);
});

test('custom todo create edit complete and restore refresh locally and locate the exact record', async ({ page }) => {
  const f = fixture();
  await loginStudent(page, f);
  const initialURL = page.url();
  let navigations = 0;
  page.on('framenavigated', frame => { if (frame === page.mainFrame()) navigations++; });
  const title = `第三版自定义待办 ${Date.now()}`;
  await page.locator('.dw-focus [data-agenda-add-todo]').click();
  const editor = page.locator('.agenda-todo-modal').filter({ has: page.locator('#agendaTodoForm') });
  await expect(editor).toBeVisible();
  await editor.locator('[name="title"]').fill(title);
  await editor.locator('[data-todo-course]').selectOption(String(f.classOfferingId));
  const saved = page.waitForResponse(response => response.url().endsWith(`/classrooms/${f.classOfferingId}/todos`) && response.request().method() === 'POST');
  await editor.locator('[data-todo-submit]').click();
  expect((await saved).status()).toBe(200);
  await expect(editor).toBeHidden();
  await page.getByRole('button', { name: '查看此待办', exact: true }).click();
  const row = page.locator('.dw-agenda-row').filter({ hasText: title });
  await expect(row).toBeVisible();
  await expect(page.locator('.dw-agenda-row')).toHaveCount(1);
  await row.getByRole('button', { name: '编辑', exact: true }).click();
  await expect(editor).toBeVisible();
  await editor.locator('[name="title"]').fill(`${title} 已编辑`);
  await editor.locator('[data-todo-submit]').click();
  await expect(editor).toBeHidden();
  await expect(row).toContainText('已编辑');
  await row.getByRole('button', { name: '完成', exact: true }).click();
  await expect(row.getByRole('button', { name: '恢复待办', exact: true })).toBeVisible();
  await row.getByRole('button', { name: '恢复待办', exact: true }).click();
  await expect(row.getByRole('button', { name: '完成', exact: true })).toBeVisible();
  expect(navigations).toBe(0);
  expect(page.url()).toBe(initialURL);
});

test('moving activities retains discussion history quote attachment and both unsent drafts', async ({ page }) => {
  await loginStudent(page, fixture());
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  await expect(page.locator('#cw-primary-content #classroom-activity-sidebar')).toHaveCount(1);
  await page.locator('#classroom-activity-tab-discussion').click();
  const messages = page.locator('#chat-messages');
  await messages.locator('.chat-message:not(.system) .chat-message-main').first().click({ button: 'right' });
  await page.locator('#chat-message-menu [data-message-action="quote"]').click();
  await expect(page.locator('#chat-quote-preview')).toBeVisible();
  const quote = await page.locator('#chat-quote-preview').innerText();
  await page.locator('#chat-input').fill('未发送的公共讨论草稿');
  await page.locator('#chat-attachment-file-input').setInputFiles({
    name: 'synthetic-unsent.png', mimeType: 'image/png',
    buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGRkAAAAASUVORK5CYII=', 'base64'),
  });
  await expect(page.locator('#chat-attachment-preview-row .chat-attachment-preview-card')).toHaveCount(1);
  await messages.evaluate(node => { node.scrollTop = 20; });
  const scroll = await messages.evaluate(node => node.scrollTop);
  await page.locator('#discussion-tab-private').click();
  await page.locator('#classroom-private-contact-input').click();
  const contact = page.locator('#classroom-private-contact-list [role="option"]').first();
  await expect(contact).toBeVisible();
  await contact.click();
  await page.locator('#classroom-private-input').fill('未发送的一对一草稿');
  await page.locator('#cw-tasks-preview').getByRole('button', { name: '历史作业与考试', exact: true }).click();
  await expect(page.locator('.cw-dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#classroom-private-input')).toHaveValue('未发送的一对一草稿');
  await page.locator('[data-classroom-message-tab="broadcast"]').click();
  await expect(page.locator('#chat-input')).toHaveValue('未发送的公共讨论草稿');
  await expect(page.locator('#chat-quote-preview')).toHaveText(quote, { useInnerText: true });
  await expect(page.locator('#chat-attachment-preview-row .chat-attachment-preview-card')).toHaveCount(1);
  expect(await messages.evaluate(node => node.scrollTop)).toBe(scroll);
  for (const width of [1366, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const send = page.locator('#chat-form button[type="submit"]');
    await send.scrollIntoViewIfNeeded();
    const hit = await send.evaluate(node => {
      const box = node.getBoundingClientRect();
      const target = document.elementFromPoint(box.x+box.width/2, box.y+box.height/2);
      return target === node || node.contains(target);
    });
    expect(hit).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width+1);
  }
});

test('lesson drag only browses while keyboard activation opens complete detail with responsive compact actions', async ({ page }) => {
  await loginStudent(page, fixture());
  await page.goto(`/classroom/${fixture().classOfferingId}`);
  const rail = page.locator('#teachingTimelineScroll');
  const selected = rail.locator('[aria-pressed="true"]');
  const initial = await selected.getAttribute('data-session-id');
  const box = await rail.boundingBox();
  await page.mouse.move(box!.x+box!.width*.7, box!.y+50);
  await page.mouse.down();
  await page.mouse.move(box!.x+box!.width*.3, box!.y+50, { steps: 12 });
  await page.mouse.up();
  await expect(page.locator('.cw-dialog')).toBeHidden();
  expect(await selected.getAttribute('data-session-id')).toBe(initial);
  expect(await rail.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  const card = rail.locator(`[data-session-id="${fixture().visualSessionIds[1]}"]`);
  await card.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.cw-dialog')).toContainText('核对长说明末行可读。');
  await expect(page.locator('.cw-dialog')).toContainText('五合校区');
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    const dialog = page.locator('.cw-dialog');
    await expect(dialog).toBeInViewport();
    expect(await dialog.evaluate(node => node.scrollWidth-node.clientWidth)).toBeLessThanOrEqual(1);
    const action = page.locator('#teachingSessionOpenMaterialBtn');
    await action.scrollIntoViewIfNeeded();
    await expect(action).toBeInViewport();
  }
  await page.keyboard.press('Escape');
  await expect(card).toBeFocused();
});

test('student API scope and concurrent read latency are measured on the isolated application', async ({ page }, testInfo) => {
  const f = fixture();
  await loginStudent(page, f);
  const overview = await (await page.request.get('/api/dashboard/course-schedule/overview')).json();
  expect(overview.overview.authorized_courses.some((course: { id: number }) => course.id === f.otherClassOfferingId)).toBe(false);
  const denied = await page.request.get(`/api/classrooms/${f.otherClassOfferingId}/learning-materials?session_id=0&generate_blurbs=false`);
  expect([403, 404]).toContain(denied.status());
  const results = [];
  for (const endpoint of [
    '/api/dashboard/course-schedule/overview',
    '/api/dashboard/workspace?limit=20',
    `/api/classrooms/${f.classOfferingId}/learning-materials?session_id=${f.visualSessionIds[2]}&generate_blurbs=false`,
    '/api/profile/ui-preferences',
  ]) {
    const samples: number[] = [];
    for (let batch = 0; batch < 5; batch++) {
      await Promise.all(Array.from({ length: 4 }, async () => {
        const started = performance.now();
        const response = await page.request.get(endpoint);
        await response.body();
        samples.push(performance.now()-started);
        expect(response.status()).toBe(200);
      }));
    }
    samples.sort((a, b) => a-b);
    results.push({ endpoint, samples: samples.length, concurrent: 4, p50_ms: Math.round(samples[9]), p95_ms: Math.round(samples[18]), max_ms: Math.round(samples[19]), errors: 0 });
  }
  await testInfo.attach('isolated-http-read-latency', { body: JSON.stringify({ scope: 'local synthetic fixture; not production capacity or INP', results }, null, 2), contentType: 'application/json' });
});
