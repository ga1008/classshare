import { expect, test } from '@playwright/test';
import {
  apiJson,
  collectBrowserErrors,
  expectNoBrowserErrors,
  loginStudent,
  loginTeacher,
  readFixture,
} from '../fixtures/p03';

test.describe('P03 assignment submission workflow', () => {
  test('student submits homework and teacher sees the submitted row and detail page', async ({ browser }, testInfo) => {
    const fixture = readFixture();
    const studentContext = await browser.newContext();
    const teacherContext = await browser.newContext();
    const studentPage = await studentContext.newPage();
    const teacherPage = await teacherContext.newPage();
    const studentErrors = collectBrowserErrors(studentPage);
    const teacherErrors = collectBrowserErrors(teacherPage);

    await loginStudent(studentPage, fixture);
    await studentPage.goto(`/assignment/${fixture.studentSubmissionAssignmentId}`);
    await expect(studentPage.getByTestId('p03-assignment-answer-area')).toBeVisible();
    await expect(studentPage.locator('.answer-textarea').first()).toBeVisible();
    await studentPage.locator('.answer-textarea').first().fill(
      `P03 browser submission ${Date.now()}: permissions must be checked in real UI workflows.`,
    );

    const submitResponsePromise = studentPage.waitForResponse((response) =>
      response.url().includes(`/api/assignments/${fixture.studentSubmissionAssignmentId}/submit`)
      && response.request().method() === 'POST',
    );
    await studentPage.getByTestId('p03-submit-assignment').click();
    const submitResponse = await submitResponsePromise;
    expect(submitResponse.ok()).toBeTruthy();
    await studentPage.waitForTimeout(1_800);
    await studentPage.reload({ waitUntil: 'domcontentloaded' });
    await expect(studentPage.locator('#submitted-answers-container')).toBeVisible();

    await loginTeacher(teacherPage, fixture);
    await teacherPage.goto(`/assignment/${fixture.studentSubmissionAssignmentId}`);
    await expect(teacherPage.locator('#submissions-list')).toBeVisible();
    const submissions = await apiJson<{ status: number; ok: boolean; body: any }>(
      teacherPage,
      `/api/assignments/${fixture.studentSubmissionAssignmentId}/submissions`,
    );
    expect(submissions.status).toBe(200);
    const rowPayload = submissions.body.submissions.find((item: any) => Number(item.student_pk_id) === fixture.student.id);
    expect(rowPayload).toBeTruthy();
    expect(['submitted', 'grading', 'graded']).toContain(rowPayload.status);

    const row = teacherPage.locator(
      `[data-testid="p03-submission-row"][data-student-pk-id="${fixture.student.id}"]`,
    );
    await expect(row).toBeVisible();
    await expect(row.getByTestId('p03-submission-status')).toBeVisible();
    await Promise.all([
      teacherPage.waitForURL(/\/submission\/\d+/, { timeout: 15_000 }),
      row.getByTestId('p03-submission-detail').click(),
    ]);
    await expect(teacherPage.locator('#answers-container')).toBeVisible();
    await expect(teacherPage.locator('#answers-container')).toContainText('P03 browser submission');

    await expectNoBrowserErrors(studentErrors, testInfo);
    await expectNoBrowserErrors(teacherErrors, testInfo);
    await studentContext.close();
    await teacherContext.close();
  });
});
