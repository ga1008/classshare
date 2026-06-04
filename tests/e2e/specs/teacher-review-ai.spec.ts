import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

async function submissionStatus(page: any, assignmentId: number, submissionId: number): Promise<string> {
  const payload = await apiJson<{ status: number; ok: boolean; body: any }>(
    page,
    `/api/assignments/${assignmentId}/submissions`,
  );
  expect(payload.status).toBe(200);
  const submission = payload.body.submissions.find((item: any) => Number(item.id) === submissionId);
  return String(submission?.status || '');
}

test.describe('P03 teacher review and AI grading state', () => {
  test('teacher opens an existing submission detail and sees answer content', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture);
    await page.goto(`/assignment/${fixture.teacherReviewAssignmentId}`);
    const row = page.locator(`[data-testid="p03-submission-row"][data-submission-id="${fixture.teacherReviewSubmissionId}"]`);
    await expect(row).toBeVisible();
    await Promise.all([
      page.waitForURL(new RegExp(`/submission/${fixture.teacherReviewSubmissionId}$`), { timeout: 15_000 }),
      row.getByTestId('p03-submission-detail').click(),
    ]);
    await expect(page.locator('#answers-container')).toBeVisible();
    await expect(page.locator('#answers-container')).toContainText('copied P03 runtime database');
    await expect(page.getByTestId('p03-submission-status')).toBeVisible();

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('mock AI success path updates backend status and visible teacher page state', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    page.on('dialog', (dialog) => dialog.accept());

    await loginTeacher(page, fixture);
    await page.goto(`/submission/${fixture.aiSuccessSubmissionId}`);
    await expect(page.getByTestId('p03-ai-regrade-detail')).toBeVisible();
    const regradeResponsePromise = page.waitForResponse((response) =>
      response.url().includes(`/api/submissions/${fixture.aiSuccessSubmissionId}/regrade`)
      && response.request().method() === 'POST',
    );
    await page.getByTestId('p03-ai-regrade-detail').click();
    const regradeResponse = await regradeResponsePromise;
    expect(regradeResponse.ok()).toBeTruthy();

    await expect.poll(
      () => submissionStatus(page, fixture.aiSuccessAssignmentId, fixture.aiSuccessSubmissionId),
      { timeout: 20_000 },
    ).toBe('graded');

    await page.goto(`/submission/${fixture.aiSuccessSubmissionId}`, { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('p03-submission-status')).toBeVisible();
    await expect(page.getByTestId('p03-submission-score-input')).toHaveValue('88');
    await expect(page.locator('body')).toContainText('P03 mock AI grading completed');

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('teacher can stop an in-flight AI grading job before stale callback overwrites state', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);
    page.on('dialog', (dialog) => dialog.accept());

    await loginTeacher(page, fixture);
    await page.goto(`/assignment/${fixture.aiStopAssignmentId}`);
    const row = page.locator(`[data-testid="p03-submission-row"][data-submission-id="${fixture.aiStopSubmissionId}"]`);
    await expect(row).toBeVisible();

    const regradeResponsePromise = page.waitForResponse((response) =>
      response.url().includes(`/api/submissions/${fixture.aiStopSubmissionId}/regrade`)
      && response.request().method() === 'POST',
    );
    await row.getByTestId('p03-submission-ai').click();
    expect((await regradeResponsePromise).ok()).toBeTruthy();

    await expect.poll(
      () => submissionStatus(page, fixture.aiStopAssignmentId, fixture.aiStopSubmissionId),
      { timeout: 10_000 },
    ).toBe('grading');

    const gradingRow = page.locator(`[data-testid="p03-submission-row"][data-submission-id="${fixture.aiStopSubmissionId}"]`);
    await expect(gradingRow.getByTestId('p03-submission-ai-stop')).toBeVisible();
    const stopResponsePromise = page.waitForResponse((response) =>
      response.url().includes(`/api/submissions/${fixture.aiStopSubmissionId}/stop-grading`)
      && response.request().method() === 'POST',
    );
    await gradingRow.getByTestId('p03-submission-ai-stop').click();
    expect((await stopResponsePromise).ok()).toBeTruthy();

    await expect.poll(
      () => submissionStatus(page, fixture.aiStopAssignmentId, fixture.aiStopSubmissionId),
      { timeout: 10_000 },
    ).toBe('submitted');
    await page.waitForTimeout(2_000);
    expect(await submissionStatus(page, fixture.aiStopAssignmentId, fixture.aiStopSubmissionId)).toBe('submitted');

    await expectNoBrowserErrors(errors, testInfo);
  });

  test('ordinary teacher cannot trigger AI grading for another teacher submission', async ({ page }, testInfo) => {
    const fixture = readFixture();
    const errors = collectBrowserErrors(page);

    await loginTeacher(page, fixture, fixture.otherTeacher);
    const payload = await apiJson<{ status: number; ok: boolean; body: any }>(
      page,
      `/api/submissions/${fixture.aiSuccessSubmissionId}/regrade`,
      { method: 'POST' },
    );
    expect(payload.status).toBe(403);

    await expectNoBrowserErrors(errors, testInfo);
  });
});
