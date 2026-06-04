import { expect, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

export type P03Fixture = {
  runtimeRoot: string;
  databasePath: string;
  password: string;
  teacher: { id: number; email: string; name: string };
  superTeacher: { id: number; email: string; name: string };
  otherTeacher: { id: number; email: string; name: string };
  student: { id: number; studentNumber: string; name: string };
  otherStudent: { id: number; studentNumber: string; name: string };
  classOfferingId: number;
  otherClassOfferingId: number;
  studentSubmissionAssignmentId: number;
  teacherReviewAssignmentId: number;
  teacherReviewSubmissionId: number;
  aiSuccessAssignmentId: number;
  aiSuccessSubmissionId: number;
  aiStopAssignmentId: number;
  aiStopSubmissionId: number;
  teacherNotificationId: number;
  studentNotificationId: number;
};

export function readFixture(): P03Fixture {
  const runtimeRoot = process.env.P03_RUNTIME_ROOT || path.join(process.cwd(), '.codex-temp', 'p03-runtime');
  const fixturePath = path.join(runtimeRoot, 'fixture.json');
  if (!fs.existsSync(fixturePath)) {
    throw new Error(`Missing P03 fixture at ${fixturePath}. Start the Playwright webServer first.`);
  }
  return JSON.parse(fs.readFileSync(fixturePath, 'utf8')) as P03Fixture;
}

export function collectBrowserErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (/favicon|ResizeObserver loop completed/i.test(text)) return;
    errors.push(text);
  });
  page.on('pageerror', (error) => {
    errors.push(error.message);
  });
  return errors;
}

export async function expectNoBrowserErrors(errors: string[], testInfo: TestInfo) {
  if (errors.length > 0) {
    await testInfo.attach('browser-errors', {
      body: errors.join('\n'),
      contentType: 'text/plain',
    });
  }
  expect(errors).toEqual([]);
}

export async function apiJson<T = any>(page: Page, url: string, init?: RequestInit): Promise<T> {
  const requestUrl = /^https?:\/\//i.test(url) ? url : new URL(url, page.url()).toString();
  const response = await page.context().request.fetch(requestUrl, {
    method: init?.method || 'GET',
    headers: init?.headers as Record<string, string> | undefined,
    data: init?.body as any,
  });
  const body = await response.json().catch(() => ({}));
  return {
    status: response.status(),
    ok: response.ok(),
    body,
  } as T;
}

export async function expectSessionRole(page: Page, role: 'teacher' | 'student') {
  const payload = await apiJson<{ status: number; ok: boolean; body: any }>(page, '/api/session/my-info');
  expect(payload.status).toBe(200);
  expect(payload.body.session_info.role).toBe(role);
  expect(payload.body.session_info.session_active).toBe(true);
}

export async function loginTeacher(
  page: Page,
  fixture: P03Fixture,
  teacher: P03Fixture['teacher'] | P03Fixture['superTeacher'] | P03Fixture['otherTeacher'] = fixture.teacher,
) {
  await page.goto('/teacher/login');
  await expect(page.locator('#email')).toBeVisible();
  await page.locator('#email').fill(teacher.email);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('button[type="submit"]').click(),
  ]);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await dismissTeacherOnboardingIfOpen(page);
  await expectSessionRole(page, 'teacher');
}

export async function loginStudent(
  page: Page,
  fixture: P03Fixture,
  student: P03Fixture['student'] | P03Fixture['otherStudent'] = fixture.student,
) {
  await page.goto('/student/login');
  await expect(page.locator('#student-password-login-form')).toBeVisible();
  await page.locator('#identifier').fill(student.studentNumber);
  await page.locator('#password').fill(fixture.password);
  await Promise.all([
    page.waitForURL(/\/dashboard(?:\?|$)/, { timeout: 20_000 }),
    page.locator('#student-password-login-form button[type="submit"]').click(),
  ]);
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await expectSessionRole(page, 'student');
}

export async function dismissTeacherOnboardingIfOpen(page: Page) {
  const closeButton = page.locator('[data-teacher-onboarding-dismiss]').first();
  if (await closeButton.isVisible({ timeout: 1_000 }).catch(() => false)) {
    await closeButton.click();
    await expect(page.locator('[data-teacher-onboarding-modal]')).toBeHidden({ timeout: 5_000 });
  }
}

export async function expectProtectedPageRejected(page: Page, pathOrUrl: string, blockedSelector: string) {
  await page.goto(pathOrUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForLoadState('networkidle').catch(() => undefined);
  await expect(page.locator(blockedSelector)).toHaveCount(0);
  expect(page.url()).not.toContain(pathOrUrl.replace(/^\//, ''));
}

export async function expectHealthUsesRuntimeDb(page: Page, fixture: P03Fixture) {
  const payload = await apiJson<{ status: number; ok: boolean; body: any }>(page, '/api/internal/health');
  expect(payload.status).toBe(200);
  const databasePath = String(payload.body.database_path).replaceAll('\\', '/');
  expect(databasePath).toContain('/.codex-temp/');
  expect(databasePath).toMatch(/\/db\/classroom\.db$/);
  expect(String(payload.body.database_path)).toBe(fixture.databasePath);
}
