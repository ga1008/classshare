import { test, expect, type Page, type Locator } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

// This suite never prepares a copy of an existing database. Create a fresh
// runtime with prepare_ui_v3_runtime.py and serve it with serve_ui_v3.py.
const runtime = path.resolve(process.env.MANAGE_USERS_RUNTIME || '.codex-temp/manage-users-20261021-runtime');
const output = path.resolve(process.env.MANAGE_USERS_OUTPUT || '.codex-temp/manage-users-browser');
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
const ownedRoot = path.resolve('.codex-temp') + path.sep;
if (!runtime.startsWith(ownedRoot) || !fixture.uiV3Synthetic
    || path.resolve(fixture.databasePath) !== path.join(runtime, 'db', 'classroom.db')) {
  throw Error('User management browser tests require a new owned synthetic runtime');
}
const runTag = Date.now().toString(36);
const createNumber = `MG-${runTag.toUpperCase()}`;
const createEmail = `manage-users-${runTag}@example.test`;
const createdName = '卡片验收合成教师';
// Optional targeted layout recheck of an account created by a completed run in
// the same health-checked synthetic runtime; normal full runs create their own.
let createdId = Number(process.env.MANAGE_USERS_TEACHER_ID || 0);
if (!Number.isInteger(createdId) || createdId < 0) throw Error('Invalid synthetic teacher ID');

function card(page: Page, id: number) {
  return page.locator(`[data-teacher-card][data-id="${id}"]`);
}
function opener(page: Page, id: number) {
  return page.locator(`[data-teacher-card][data-id="${id}"][data-open-teacher], [data-teacher-card][data-id="${id}"] [data-open-teacher]`);
}
function detail(page: Page) {
  return page.locator('#teacher-detail-modal');
}
function edit(page: Page) {
  return page.locator('#teacher-edit-form');
}
function submit(page: Page, formId = 'teacher-edit-form') {
  return page.locator(`button[type="submit"][form="${formId}"]`);
}
async function login(page: Page, email = fixture.superTeacher.email) {
  const base = new URL(String(test.info().project.use.baseURL));
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) {
    throw Error('User management browser writes require a loopback synthetic server');
  }
  const health = await page.request.get('/api/internal/health');
  expect(health.ok()).toBeTruthy();
  expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
  const response = await page.request.post('/teacher/login', {
    form: { email, password: fixture.password, next: '/manage/system/users' },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(303);
  await page.request.post('/api/manage/teacher-onboarding/dismiss', { data: { reason: 'later' } });
}
async function users(page: Page) {
  await page.addInitScript(() => {
    (window as any).__manageUsersLongTasks = [];
    if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
      new PerformanceObserver(list => {
        (window as any).__manageUsersLongTasks.push(...list.getEntries().map(entry => entry.duration));
      }).observe({ type: 'longtask', buffered: true });
    }
  });
  await login(page);
  await page.goto('/manage/system/users');
  await expect(page.locator('#teacher-card-grid')).toBeVisible();
}
async function openTeacher(page: Page, id: number) {
  await opener(page, id).click();
  await expect(detail(page)).toBeVisible();
  await expect(edit(page).locator('[name="name"]')).toBeEnabled();
  await expect(edit(page).locator('[name="expected_revision"]')).not.toHaveValue('');
}
async function teacherData(page: Page, id: number) {
  const response = await page.request.get(`/api/manage/system/teachers/${id}`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()).teacher;
}
async function confirmTop(page: Page, choice: RegExp) {
  const dialog = page.locator('.lq-confirm:visible').last();
  await expect(dialog).toBeVisible();
  const label = await dialog.getAttribute('aria-labelledby');
  await dialog.getByRole('button', { name: choice }).click();
  await expect(page.locator(`[role="dialog"][aria-labelledby="${label}"]`)).toBeHidden();
}
async function closeCleanDetail(page: Page) {
  await page.keyboard.press('Escape');
  await expect(detail(page)).toBeHidden();
}
async function saveForm(page: Page, form: Locator, endpoint: string) {
  const response = page.waitForResponse(r => new URL(r.url()).pathname === endpoint && r.request().method() === 'POST');
  await submit(page, (await form.getAttribute('id'))!).click();
  return response;
}
async function screenshot(page: Page, name: string) {
  fs.mkdirSync(output, { recursive: true });
  await page.screenshot({ path: path.join(output, name), fullPage: false });
}
async function recordMetrics(page: Page, scenario: string) {
  const measured = await page.evaluate(() => {
    const durations = ((window as any).__manageUsersLongTasks || []) as number[];
    const hosts = [...document.querySelectorAll<HTMLElement>('body *')].filter(node => {
      const style = getComputedStyle(node), rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
        && style.backdropFilter !== 'none' && style.backdropFilter !== '';
    });
    const resources = performance.getEntriesByType('resource');
    return {
      viewport: { width: innerWidth, height: innerHeight },
      appearance: document.documentElement.dataset.appearance,
      backdropHostCount: hosts.length,
      nestedBackdropHostCount: hosts.filter(node => hosts.some(parent => parent !== node && parent.contains(node))).length,
      backdropHosts: hosts.map(node => ({ tag: node.tagName, id: node.id, className: node.className })),
      longTasks: { count: durations.length, totalMs: durations.reduce((sum, value) => sum + value, 0),
        maxMs: durations.length ? Math.max(...durations) : 0 },
      organizationSuggestionRequests: resources.filter(entry => entry.name.includes('/organization-suggestions?')).length,
      sameOriginResourceRequests: resources.filter(entry => new URL(entry.name).origin === location.origin).length,
    };
  });
  fs.mkdirSync(output, { recursive: true });
  const file = path.join(output, 'metrics.json');
  const previous = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {};
  previous[scenario] = { recordedAt: new Date().toISOString(), ...measured };
  fs.writeFileSync(file, JSON.stringify(previous, null, 2) + '\n');
}
function seedOwnedRuntime() {
  // Data creation is restricted to the assertion-checked synthetic DB above;
  // no application configuration, dotenv, credentials or network are imported.
  const script = `
import json, pathlib, sqlite3, sys
runtime=pathlib.Path(sys.argv[1]).resolve()
fixture=json.loads((runtime/'fixture.json').read_text('utf-8'))
assert fixture.get('uiV3Synthetic') is True
assert pathlib.Path(fixture['databasePath']).resolve()==runtime/'db'/'classroom.db'
with sqlite3.connect(fixture['databasePath']) as conn:
    for key,number in [('teacher','MG-BASE-1'),('superTeacher','MG-BASE-2'),('otherTeacher','MG-BASE-3')]:
        tid=int(fixture[key]['id'])
        conn.execute("UPDATE teachers SET employee_number=? WHERE id=?", (number,tid))
        conn.execute("UPDATE teacher_organization_memberships SET employee_number=? WHERE teacher_id=? AND school_code=(SELECT school_code FROM teachers WHERE id=?)", (number,tid,tid))
    conn.execute("INSERT INTO teacher_organization_memberships(teacher_id,school_code,school_name,college,department,employee_number,is_active,is_primary) VALUES(?,'gxufl','广西外国语学院','数字科技学院','网络工程系','MG-ADMIN-GX',1,0) ON CONFLICT(teacher_id,school_code) DO NOTHING", (fixture['superTeacher']['id'],))
    profile=json.dumps({'teacher_no':sys.argv[2],'name':'卡片验收合成教师','college_name':'数字科技学院'},ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO academic_teacher_directory(school_code,teacher_no,name,college_name,title,profile_json,synced_at) VALUES('gxufl',?,'卡片验收合成教师','数字科技学院','',?,'2026-10-21T00:00:00+08:00')", (sys.argv[2],profile))
    lessons=json.dumps([{'id':'manage-users-synthetic-lesson','course_name':'卡片关联课程','course_code':'MG001','weekday':2,'weeks':list(range(1,21)),'sections':[2,3],'class_label':'合成班','classroom':'合成楼101','teaching_class_name':'测试-001','student_count':30}],ensure_ascii=False)
    summary=json.dumps({'course_count':1,'class_count':1,'lesson_count':1,'total_hours':40})
    conn.execute("INSERT OR REPLACE INTO academic_teacher_timetables(school_code,teacher_no,term_code,profile_json,snapshot_json,lessons_json,summary_json,synced_at) VALUES('gxufl',?,'2026-2027-1',?,'{}',?,?,'2026-10-21T00:00:00+08:00')", (sys.argv[2],profile,lessons,summary))
`;
  execFileSync(process.env.PYTHON || 'python', ['-c', script, runtime, createNumber], { encoding: 'utf8' });
}

test.describe.serial('compact user cards and complete modal management on production assets', () => {
  test.beforeAll(() => { if (!createdId) seedOwnedRuntime(); });

  test('desktop overview stays compact; cards filter and open by keyboard with focus returned', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await users(page);
    await expect(page.locator('#teacher-create-form')).toBeHidden();
    const overview = await page.locator('#teacher-overview').boundingBox();
    expect(overview).not.toBeNull();
    expect(overview!.height).toBeLessThan(180);
    const target = card(page, fixture.teacher.id);
    await expect(target).toBeVisible();
    expect(await target.locator('input, select, textarea').count()).toBe(0);
    await page.locator('#teacher-search').fill(fixture.teacher.email);
    await expect(target).toBeVisible();
    await expect(card(page, fixture.otherTeacher.id)).toBeHidden();
    await page.locator('#teacher-search').fill('');
    const trigger = opener(page, fixture.teacher.id);
    await trigger.focus();
    await page.keyboard.press('Enter');
    await expect(detail(page)).toBeVisible();
    await expect(edit(page).locator('[name="name"]')).toBeEnabled();
    await closeCleanDetail(page);
    await expect(trigger).toBeFocused();
    await screenshot(page, 'desktop-user-cards.png');
    await recordMetrics(page, 'desktop-cards-after-detail');
    expect(errors).toEqual([]);
  });

  test('create modal saves once, opens the new detail without reload and shows immediate timetable match', async ({ page }) => {
    await users(page);
    await page.evaluate(() => { (window as any).__manageUsersDocument = 'same-document'; });
    await page.locator('#teacher-create-open').click();
    const form = page.locator('#teacher-create-form');
    await expect(form).toBeVisible();
    await form.locator('[name="name"]').fill(createdName);
    await form.locator('[name="email"]').fill(createEmail);
    await form.locator('[name="password"]').fill(fixture.password);
    await form.locator('[name="school_name"]').fill('广西外国语学院');
    await form.locator('[name="college"]').fill('数字科技学院');
    await form.locator('[name="department"]').fill('网工系');
    await form.locator('[name="employee_number"]').fill(createNumber);
    const response = await saveForm(page, form, '/api/manage/system/teachers');
    expect(response.ok()).toBeTruthy();
    const payload = await response.json();
    createdId = payload.teacher.id;
    expect(payload.teacher.employee_number).toBe(createNumber);
    expect(payload.teacher.department).toBe('网络工程系');
    expect(payload.timetable_links.status).toBe('linked');
    expect(payload.timetable_links.linked_count).toBe(1);
    await expect(form).toBeHidden();
    await expect(detail(page)).toBeVisible();
    await expect(edit(page).locator('[name="email"]')).toHaveValue(createEmail);
    await expect(detail(page)).toContainText('已关联');
    expect(await page.evaluate(() => (window as any).__manageUsersDocument)).toBe('same-document');
    await closeCleanDetail(page);
    await expect(card(page, createdId)).toBeVisible();
    await openTeacher(page, createdId);
    const link = detail(page).locator('a[href*="/manage/academic/teachers/schedule?"]').first();
    await expect(link).toBeVisible();
    const posts: string[] = [];
    page.context().on('request', request => {
      if (request.method() === 'POST' && /\/api\/manage\/academic\/teachers\/(?:query|sync|[^/]+\/sync)/.test(request.url())) {
        posts.push(request.url());
      }
    });
    let timetablePage = page;
    if (await link.getAttribute('target') === '_blank') {
      const popup = page.waitForEvent('popup');
      await link.click();
      timetablePage = await popup;
    } else {
      await link.click();
    }
    await expect(timetablePage.getByRole('dialog', { name: `${createdName}的课表`, exact: true })).toBeVisible();
    await expect(timetablePage.locator('.teacher-timetable-dialog .cs-deck')).toBeVisible();
    expect(posts).toEqual([]);
    if (timetablePage !== page) await timetablePage.close();
  });

  test('detail edits update the card and keep revision conflicts from overwriting another save', async ({ page }) => {
    await users(page);
    await openTeacher(page, createdId);
    const form = edit(page);
    await form.locator('[name="name"]').fill('卡片更新后的合成教师');
    const saved = await saveForm(page, form, `/api/manage/system/teachers/${createdId}`);
    expect(saved.ok()).toBeTruthy();
    await expect(submit(page)).toBeEnabled();
    const current = await teacherData(page, createdId);
    expect(current.name).toBe('卡片更新后的合成教师');
    await expect(card(page, createdId)).toContainText(current.name);

    const external = await page.request.post(`/api/manage/system/teachers/${createdId}`, { form: {
      name: current.name, email: current.email, employee_number: current.employee_number,
      school_code: current.school_code, school_name: current.school_name,
      college: current.college, department: current.department,
      phone: '13800000123', expected_revision: current.revision,
    } });
    expect(external.ok()).toBeTruthy();
    await form.locator('[name="college"]').fill('冲突后需要保留的学院');
    const rejected = await saveForm(page, form, `/api/manage/system/teachers/${createdId}`);
    expect(rejected.status()).toBe(409);
    await expect(form).toBeVisible();
    await expect(form.locator('[name="college"]')).toHaveValue('冲突后需要保留的学院');
    await expect(submit(page)).toBeEnabled();
    expect((await teacherData(page, createdId)).phone).toBe('13800000123');
    await page.keyboard.press('Escape');
    await confirmTop(page, /放弃/);
    await expect(detail(page)).toBeHidden();
  });

  test('membership child dialog saves independent school identity, changes default and protects last membership', async ({ page }) => {
    await users(page);
    await openTeacher(page, createdId);
    const add = detail(page).locator('[data-teacher-action="memberships"]');
    await add.click();
    const membership = page.locator('#teacher-membership-form');
    await expect(membership).toBeVisible();
    await membership.locator('[name="school_name"]').fill('卡片验收第二学校');
    await membership.locator('[name="college"]').fill('第二学院');
    await membership.locator('[name="department"]').fill('软工系');
    await membership.locator('[name="employee_number"]').fill(`B-${createNumber}`);
    const saved = await saveForm(page, membership, `/api/manage/system/teachers/${createdId}/memberships`);
    expect(saved.ok()).toBeTruthy();
    const payload = await saved.json();
    const membershipId = payload.membership.id;
    await expect(membership).toBeHidden();
    await expect(detail(page)).toBeVisible();
    await expect(page.locator('#teacher-membership-list')).toContainText('卡片验收第二学校');

    const primary = page.locator(`#teacher-membership-list [data-membership-action="primary"][data-membership-id="${membershipId}"]`);
    await primary.click();
    await confirmTop(page, /确认|默认/);
    await expect.poll(async () => (await teacherData(page, createdId)).employee_number).toBe(`B-${createNumber}`);
    const remove = page.locator(`#teacher-membership-list [data-membership-action="delete"][data-membership-id="${membershipId}"]`);
    await remove.click();
    await confirmTop(page, /确认|停用/);
    await expect.poll(async () => (await teacherData(page, createdId)).employee_number).toBe(createNumber);
    const after = await teacherData(page, createdId);
    const remaining = after.active_memberships[0].id;
    await expect(page.locator(`#teacher-membership-list [data-membership-action="delete"][data-membership-id="${remaining}"]`)).toBeDisabled();
    await detail(page).locator('[data-teacher-action="reset"]').click();
    await expect(page.locator('#teacher-password-form')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#teacher-password-form')).toBeHidden();
    await expect(detail(page).locator('[data-teacher-action="reset"]')).toBeFocused();
  });

  test('busy save rejects close, errors preserve drafts and unsaved-close confirmation can be cancelled', async ({ page }) => {
    await users(page);
    await openTeacher(page, createdId);
    const form = edit(page);
    await form.locator('[name="college"]').fill('503之后仍然保留的学院');
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/api/manage/system/teachers/${createdId}`, async route => {
      if (route.request().method() !== 'POST') return route.continue();
      await pending;
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"合成暂时不可用"}' });
    });
    await submit(page).click();
    await expect(submit(page)).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(form).toBeVisible();
    await detail(page).locator('[data-close-modal]').first().click();
    await expect(form).toBeVisible();
    release();
    await expect(submit(page)).toBeEnabled();
    await expect(form.locator('[name="college"]')).toHaveValue('503之后仍然保留的学院');
    await page.keyboard.press('Escape');
    await confirmTop(page, /取消|继续编辑/);
    await expect(form).toBeVisible();
    await expect(form.locator('[name="college"]')).toHaveValue('503之后仍然保留的学院');
    await page.keyboard.press('Escape');
    await confirmTop(page, /放弃/);
    await expect(detail(page)).toBeHidden();
    await expect(opener(page, createdId)).toBeFocused();
  });

  test('390 touch dark detail stays within viewport and nested dialogs retain the parent and focus', async ({ browser }) => {
    const context = await browser.newContext({
      viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true,
      colorScheme: 'dark', reducedMotion: 'reduce', baseURL: test.info().project.use.baseURL,
    });
    const page = await context.newPage();
    try {
      await users(page);
      await page.evaluate(() => {
        const theme = (document as any)[Symbol.for('lanshare.theme.installation')];
        if (!theme) throw Error('Shared theme installation missing');
        theme.refresh({ appearance: 'dark' });
      });
      await expect(page.locator('html')).toHaveAttribute('data-appearance', 'dark');
      await screenshot(page, 'touch-dark-user-overview.png');
      await opener(page, createdId).tap();
      await expect(edit(page).locator('[name="name"]')).toBeEnabled();
      const dialog = detail(page).locator('[role="dialog"]');
      const bounds = await dialog.boundingBox();
      expect(bounds!.width).toBeLessThanOrEqual(390);
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.y).toBeGreaterThanOrEqual(0);
      expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(845);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
      const body = detail(page).locator('.lq-dialog__body');
      expect(await body.evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
      if (await page.locator('#ai-chat-fab').isVisible()) {
        const saveBounds = (await submit(page).boundingBox())!;
        const fabBounds = (await page.locator('#ai-chat-fab').boundingBox())!;
        const overlaps = saveBounds.x < fabBounds.x + fabBounds.width
          && saveBounds.x + saveBounds.width > fabBounds.x
          && saveBounds.y < fabBounds.y + fabBounds.height
          && saveBounds.y + saveBounds.height > fabBounds.y;
        expect(overlaps, 'shared assistant launcher must not cover the save action').toBe(false);
      }
      await screenshot(page, 'touch-dark-user-detail.png');
      await recordMetrics(page, 'touch-dark-detail');
      const add = detail(page).locator('[data-teacher-action="memberships"]');
      await add.tap();
      await expect(page.locator('#teacher-membership-form')).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(page.locator('#teacher-membership-form')).toBeHidden();
      await expect(detail(page)).toBeVisible();
      await expect(add).toBeFocused();
      for (let index = 0; index < 4; index++) {
        await closeCleanDetail(page);
        await openTeacher(page, createdId);
      }
      expect(await edit(page).locator('datalist').count()).toBe(3);
      const suggestionRequests = await page.evaluate(() => performance.getEntriesByType('resource')
        .filter(entry => entry.name.includes('/organization-suggestions?')).length);
      expect(suggestionRequests).toBeLessThanOrEqual(5);
      await closeCleanDetail(page);
      await screenshot(page, 'touch-dark-user-cards.png');
      await recordMetrics(page, 'touch-dark-cards-after-five-detail-opens');
    } finally {
      await context.close();
    }
  });

  test('account lifecycle keeps historical teaching data and makes cancellation read-only', async ({ page }) => {
    await users(page);
    await openTeacher(page, fixture.otherTeacher.id);
    const before = await teacherData(page, fixture.otherTeacher.id);
    await detail(page).locator('[data-teacher-action="deactivate"]').click();
    await confirmTop(page, /确认|停用/);
    await expect.poll(async () => (await teacherData(page, fixture.otherTeacher.id)).account_status).toBe('inactive');
    await expect(edit(page).locator('[name="name"]')).toBeDisabled();
    await detail(page).locator('[data-teacher-action="activate"]').click();
    await confirmTop(page, /确认|启用/);
    await expect.poll(async () => (await teacherData(page, fixture.otherTeacher.id)).account_status).toBe('active');
    await expect(edit(page).locator('[name="name"]')).toBeEnabled();
    await detail(page).locator('[data-teacher-action="cancel"]').click();
    await confirmTop(page, /确认|注销/);
    await expect.poll(async () => (await teacherData(page, fixture.otherTeacher.id)).account_status).toBe('cancelled');
    const after = await teacherData(page, fixture.otherTeacher.id);
    expect(after.email).toBe(before.email);
    expect(after.employee_number).toBe(before.employee_number);
    expect(after.class_count).toBe(before.class_count);
    expect(after.offering_count).toBe(before.offering_count);
    await expect(edit(page).locator('[name="name"]')).toBeDisabled();
    await expect(detail(page).locator('[data-teacher-action="activate"]')).not.toBeVisible();
    const denied = await page.request.post(`/api/manage/system/teachers/${fixture.otherTeacher.id}/activate`, {
      form: { expected_revision: after.revision },
    });
    expect([400, 409]).toContain(denied.status());
  });

  test('self-account protection and ordinary teacher permission hold through the real API', async ({ page }) => {
    await users(page);
    await openTeacher(page, fixture.superTeacher.id);
    for (const action of ['deactivate', 'cancel']) {
      await expect(detail(page).locator(`[data-teacher-action="${action}"]`)).toBeDisabled();
    }
    const current = await teacherData(page, fixture.superTeacher.id);
    const self = await page.request.delete(`/api/manage/system/teachers/${fixture.superTeacher.id}`, {
      form: { expected_revision: current.revision },
    });
    expect([400, 409]).toContain(self.status());
    const revoke = await page.request.post(`/api/manage/system/teachers/${fixture.superTeacher.id}/super-admin/revoke`, {
      form: { expected_revision: current.revision },
    });
    expect([400, 409]).toContain(revoke.status());
    await login(page, fixture.teacher.email);
    const denied = await page.request.get(`/api/manage/system/teachers/${createdId}`);
    expect(denied.status()).toBe(403);
  });
});
