import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const runtime = path.resolve(process.env.TEACHER_IDENTITY_RUNTIME || '.codex-temp/teacher-identity-20261020-runtime');
const output = path.resolve(process.env.TEACHER_IDENTITY_OUTPUT || '.codex-temp/teacher-identity-browser');
fs.mkdirSync(output, { recursive: true });
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!fixture.uiV3Synthetic || path.resolve(fixture.databasePath) !== path.join(runtime, 'db', 'classroom.db')) {
  throw Error('Teacher identity tests require the explicitly owned synthetic runtime');
}
const number = '000186';
async function login(page: Page, email = fixture.superTeacher.email, school = '') {
  const base = new URL(String(test.info().project.use.baseURL));
  if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(base.hostname)) {
    throw Error('Teacher identity browser writes require a loopback synthetic server');
  }
  const health = await page.request.get('/api/internal/health');
  expect(health.ok()).toBeTruthy();
  expect(path.resolve((await health.json()).database_path)).toBe(path.resolve(fixture.databasePath));
  const response = await page.request.post('/teacher/login', {
    form: { email, password: fixture.password, school_name: school, next: '/manage/system/users' },
    maxRedirects: 0,
  });
  expect(response.status()).toBe(303);
  await page.request.post('/api/manage/teacher-onboarding/dismiss', { data: { reason: 'later' } });
}
async function users(page: Page) {
  await page.goto('/manage/system/users');
  await expect(page.locator('#create-school-name')).toHaveAttribute('list', /suggestions$/);
  await expect(page.locator('#teacher-card-grid')).toBeVisible();
}
async function openDetail(page: Page, id: number) {
  await page.locator(`[data-teacher-card][data-id="${id}"]`).click();
  await expect(page.locator('#teacher-edit-form [name="name"]')).toBeEnabled();
}
const editSubmit = (page: Page) => page.locator('button[form="teacher-edit-form"][type="submit"]');
async function discardDraft(page: Page) {
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: '放弃未保存的修改？', exact: true })
    .getByRole('button', { name: '放弃修改', exact: true }).click();
}
test.describe.serial('school-scoped teacher identities on production assets', () => {
  test('admin saves required employee number and alias, deduplicated editable recommendations', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    await login(page);
    // Make the recommendation source explicit: this suite also runs against a
    // brand-new UI runtime whose other teachers have no network-department data.
    const sourceResponse = await page.request.get(`/api/manage/system/teachers/${fixture.teacher.id}`);
    expect(sourceResponse.ok()).toBeTruthy();
    const source = (await sourceResponse.json()).teacher;
    const seedTag = Date.now().toString(36);
    const recommendation = await page.request.post('/api/manage/system/teachers', { form: {
      name: '推荐目录合成教师', email: `identity-recommendation-${seedTag}@example.test`, password: fixture.password,
      employee_number: `REC-${seedTag}`, school_code: source.school_code, school_name: source.school_name,
      college: source.college, department: '网络工程系',
    } });
    expect(recommendation.ok()).toBeTruthy();
    await users(page);
    const card = page.locator(`[data-teacher-card][data-id="${fixture.teacher.id}"]`);
    await openDetail(page, fixture.teacher.id);
    const form = page.locator('#teacher-edit-form');
    await expect(form).toBeVisible();
    await expect(form.locator('[name="employee_number"]')).toHaveAttribute('required', '');
    await form.locator('[name="employee_number"]').fill(number);
    await form.locator('[name="department"]').fill('网工系');
    const saved = page.waitForResponse(r => r.url().endsWith(`/api/manage/system/teachers/${fixture.teacher.id}`) && r.request().method() === 'POST');
    await editSubmit(page).click();
    const response = await saved;
    expect(response.ok()).toBeTruthy();
    const data = await response.json();
    expect(data.teacher.employee_number).toBe(number);
    expect(data.teacher.department).toBe('网络工程系');
    await expect(card).toContainText(number);
    await page.locator('#teacher-membership-list [data-membership-action="edit"]').first().click();
    const membership = page.locator('#teacher-membership-form');
    await expect(membership.locator('[name="employee_number"]')).toHaveValue(number);
    await membership.locator('[name="department"]').focus();
    await expect(page.locator('#membership-department-suggestions option[value="网络工程系"]')).toHaveCount(1);
    const school = await membership.locator('[name="school_name"]').inputValue();
    const college = await membership.locator('[name="college"]').inputValue();
    const rec = await page.request.get('/api/manage/organization-suggestions', { params: { school_name: school, college } });
    const options = await rec.json();
    expect(options.departments.filter((v: string) => v === '网络工程系')).toHaveLength(1);
    expect(options.department_aliases['网络工程系']).toContain('网工系');
    await expect(membership.locator('[name="school_name"]')).toHaveAttribute('readonly', '');
    await page.keyboard.press('Escape');
    await expect(membership).toBeHidden();
    await page.locator('#teacher-detail-modal [data-teacher-action="memberships"]').click();
    await membership.locator('[name="school_name"]').fill(school);
    await membership.locator('[name="employee_number"]').fill(number);
    await membership.locator('[name="school_name"]').fill('另一所合成学校');
    await expect(membership.locator('[name="employee_number"]')).toHaveValue('');
    await expect(membership.locator('[name="school_code"]')).toHaveValue('');
    await page.screenshot({ path: path.join(output, 'desktop-membership.png') });
    expect(errors).toEqual([]);
  });

  test('profile saves number, old email and employee logins work; conflict retains draft', async ({ page }) => {
    await login(page, fixture.teacher.email);
    await page.goto('/profile?section=settings');
    await expect(page.locator('#profile-employee-number')).toHaveValue(number);
    await page.locator('#profile-employee-number').fill('000187');
    const save = page.waitForResponse(r => r.url().endsWith('/api/profile/basic') && r.request().method() === 'PUT');
    await page.locator('#profile-basic-form [type="submit"]').click();
    expect((await save).ok()).toBeTruthy();
    await login(page, '000187');
    await login(page, fixture.teacher.email);
    const missing = await page.request.put('/api/profile/basic', { data: { employee_number: '' } });
    expect(missing.status()).toBe(400);
    await login(page);
    await users(page);
    await openDetail(page, fixture.otherTeacher.id);
    const form = page.locator('#teacher-edit-form');
    await form.locator('[name="employee_number"]').fill('000187');
    const rejected = page.waitForResponse(r => r.url().endsWith(`/api/manage/system/teachers/${fixture.otherTeacher.id}`) && r.request().method() === 'POST');
    await editSubmit(page).click();
    expect((await rejected).status()).toBe(400);
    await expect(form).toBeVisible();
    await expect(form.locator('[name="employee_number"]')).toHaveValue('000187');
    await expect(editSubmit(page)).toBeEnabled();
  });

  test('cross-school duplicates require school and wrong passwords stay rejected', async ({ page }) => {
    await login(page);
    const created = await page.request.post('/api/manage/system/teachers', { form: {
      name: '跨校合成教师', email: 'cross-school-identity@example.test', password: fixture.password,
      employee_number: '000187', school_name: '跨校登录合成学校', college: '合成学院', department: '软件工程',
    } });
    expect(created.ok()).toBeTruthy();
    const ambiguous = await page.request.post('/teacher/login', {
      form: { email: '000187', password: fixture.password }, maxRedirects: 0,
    });
    expect(ambiguous.status()).toBe(409);
    await login(page, '000187', '跨校登录合成学校');
    const wrong = await page.request.post('/teacher/login', {
      form: { email: '000187', school_name: '跨校登录合成学校', password: 'incorrect-synthetic' }, maxRedirects: 0,
    });
    expect(wrong.status()).toBe(400);
  });

  test('mobile dark modal, editable recommendation failure and native login constraints', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.emulateMedia({ colorScheme: 'dark', reducedMotion: 'reduce' });
    await login(page);
    await users(page);
    await page.route('**/api/manage/organization-suggestions?*', route => route.fulfill({
      status: 503, contentType: 'application/json', body: '{"detail":"synthetic unavailable"}',
    }));
    await openDetail(page, fixture.teacher.id);
    const opener = page.locator('#teacher-membership-list [data-membership-action="edit"]').first();
    await opener.click();
    const form = page.locator('#teacher-membership-form');
    await form.locator('[name="college"]').fill('未收录学院');
    await expect(form.locator('[data-organization-hint]')).toContainText('仍可直接输入');
    await form.locator('[name="department"]').fill('自由输入测试系');
    await expect(form.locator('[name="department"]')).toHaveValue('自由输入测试系');
    const modal = page.locator('#teacher-membership-modal [role="dialog"]');
    const bounds = await modal.boundingBox();
    expect(bounds!.width).toBeLessThanOrEqual(390);
    await page.screenshot({ path: path.join(output, 'mobile-membership.png') });
    await discardDraft(page);
    await expect(form).toBeHidden();
    await expect(opener).toBeFocused();
    // Signed-in users now continue to their dashboard from the login URL.
    // Test the native login field in its actual unauthenticated state.
    await page.context().clearCookies();
    await page.goto('/teacher/login');
    await expect(page).toHaveURL(/\/teacher\/login$/);
    await expect(page.locator('#email')).toHaveAttribute('type', 'text');
    await page.locator('#email').fill('000187');
    expect(await page.locator('#email').evaluate((el: HTMLInputElement) => el.checkValidity())).toBe(true);
    await page.screenshot({ path: path.join(output, 'mobile-login.png') });
  });

  test('pending save cannot close the modal; cached suggestions and IME preserve input', async ({ page }) => {
    await login(page);
    await users(page);
    await openDetail(page, fixture.teacher.id);
    const form = page.locator('#teacher-edit-form');
    const college = form.locator('[name="college"]');
    await college.dispatchEvent('compositionstart');
    await college.fill('输入中的学院');
    await college.dispatchEvent('compositionend');
    await expect(college).toHaveValue('输入中的学院');
    await expect(form.locator('[data-organization-hint]')).toContainText('可从推荐中选择');
    for (let i = 0; i < 4; i++) {
      await form.locator('[name="department"]').focus();
      await college.focus();
    }
    expect(await form.locator('datalist').count()).toBe(3);
    const resources = await page.evaluate(() => performance.getEntriesByType('resource')
      .filter(item => item.name.includes('/organization-suggestions?')).length);
    expect(resources).toBeLessThanOrEqual(5);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    await page.route(`**/api/manage/system/teachers/${fixture.teacher.id}`, async route => {
      await pending;
      await route.fulfill({ status: 409, contentType: 'application/json', body: '{"detail":"合成并发冲突，请核对"}' });
    });
    await form.locator('[name="employee_number"]').fill('000187');
    await editSubmit(page).click();
    await expect(editSubmit(page)).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(form).toBeVisible();
    await page.locator('#teacher-detail-modal [data-close-modal]').first().click();
    await expect(form).toBeVisible();
    release();
    await expect(editSubmit(page)).toBeEnabled();
    await expect(college).toHaveValue('输入中的学院');
    await expect(form.locator('[name="employee_number"]')).toHaveValue('000187');
  });
});
