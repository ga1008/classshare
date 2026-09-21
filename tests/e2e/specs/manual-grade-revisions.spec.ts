import { expect, test, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { apiJson, expectHealthUsesRuntimeDb, loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

function storedReview(databasePath: string, submissionId: number) {
  const temporary = path.resolve('.codex-temp') + path.sep;
  expect(path.resolve(databasePath).startsWith(temporary)).toBe(true);
  const code = `import json, sqlite3, sys
conn=sqlite3.connect('file:'+sys.argv[1].replace(chr(92),'/')+'?mode=ro', uri=True)
conn.row_factory=sqlite3.Row
row=dict(conn.execute('SELECT score,feedback_md,status FROM submissions WHERE id=?',(int(sys.argv[2]),)).fetchone())
row['revision_count']=conn.execute('SELECT COUNT(*) FROM submission_grade_revisions WHERE submission_id=?',(int(sys.argv[2]),)).fetchone()[0]
print(json.dumps(row))`;
  return JSON.parse(execFileSync('python', ['-c', code, databasePath, String(submissionId)], { encoding: 'utf8' }));
}

async function gradeResponse(page: Page, submissionId: number, score: string, feedback: string) {
  await page.getByTestId('p03-submission-score-input').fill(score);
  await page.locator('#grade-feedback').fill(feedback);
  const pending = page.waitForResponse(response => response.url().endsWith(`/api/submissions/${submissionId}/grade`)
    && response.request().method() === 'POST');
  await page.getByTestId('p03-submit-manual-grade').click();
  return pending;
}

test('real grading service rejects a stale second tab, preserves draft, and saves decimals then zero after explicit review', async ({ page, context }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await expectHealthUsesRuntimeDb(page, fixture);
  const sid = fixture.teacherReviewSubmissionId;
  const before = storedReview(fixture.databasePath, sid);
  await page.goto(`/submission/${sid}`);
  const second = await context.newPage();
  await second.goto(`/submission/${sid}`);
  await expect(page.getByTestId('p03-submit-manual-grade')).toBeVisible();
  await expect(second.getByTestId('p03-submit-manual-grade')).toBeVisible();
  const saved = await gradeResponse(page, sid, '82.75', 'First editor: decimal score');
  expect(saved.status()).toBe(200);
  const firstBody = saved.request().postDataJSON();
  expect(firstBody.score).toBe(82.75);
  expect(firstBody.expected_review_revision).toMatch(/^[a-f0-9]{64}$/);
  expect(firstBody.expected_assignment_revision).toMatch(/^[a-f0-9]{64}$/);
  await expect(page).toHaveURL(new RegExp(`/assignment/${fixture.teacherReviewAssignmentId}$`));
  expect(storedReview(fixture.databasePath, sid)).toMatchObject({ score: 82.75,
    feedback_md: 'First editor: decimal score', revision_count: before.revision_count + 1 });

  const rejected = await gradeResponse(second, sid, '0', 'Second editor: keep this zero-score draft');
  expect(rejected.status()).toBe(409);
  expect(rejected.request().postDataJSON().expected_review_revision).toBe(firstBody.expected_review_revision);
  await expect(second.locator('#grade-conflict')).toBeVisible();
  await expect(second.getByTestId('p03-submit-manual-grade')).toBeDisabled();
  await expect(second.getByTestId('p03-submission-score-input')).toHaveValue('0');
  await expect(second.locator('#grade-feedback')).toHaveValue('Second editor: keep this zero-score draft');
  const otherMutations: string[] = [];
  const dialogs: string[] = [];
  second.on('dialog', async dialog => {
    dialogs.push(dialog.message());
    await dialog.accept();
  });
  second.on('request', request => {
    if (request.method() !== 'GET' && /\/(regrade|files)$/.test(request.url())) otherMutations.push(request.url());
  });
  await second.getByTestId('p03-ai-regrade-detail').click();
  await second.evaluate(async () => {
    await (window as any).uploadSubmissionAttachments(false);
    await (window as any).uploadSubmissionAttachments(true);
  });
  expect(dialogs).toEqual([]);
  expect(otherMutations).toEqual([]);
  await expect(second.locator('#grade-save-status')).toContainText('评分草稿尚未保存');
  await expect(second.locator('#grade-feedback')).toHaveValue('Second editor: keep this zero-score draft');
  expect(storedReview(fixture.databasePath, sid)).toMatchObject({ score: 82.75,
    feedback_md: 'First editor: decimal score', revision_count: before.revision_count + 1 });

  await second.locator('#grade-conflict-refresh').click();
  await expect(second.locator('#grade-conflict-summary')).toContainText('82.75');
  await expect(second.getByTestId('p03-submit-manual-grade')).toBeDisabled();
  await expect(second.getByTestId('p03-submission-score-input')).toHaveValue('0');
  await second.locator('#grade-conflict-latest summary').click();
  await expect(second.locator('#grade-conflict-server-feedback')).toHaveText('First editor: decimal score');
  await second.locator('#grade-conflict-confirm').click();
  const zero = await gradeResponse(second, sid, '0', 'Second editor: keep this zero-score draft');
  expect(zero.status()).toBe(200);
  expect(zero.request().postDataJSON().expected_review_revision).not.toBe(firstBody.expected_review_revision);
  await expect(second).toHaveURL(new RegExp(`/assignment/${fixture.teacherReviewAssignmentId}$`));
  expect(storedReview(fixture.databasePath, sid)).toMatchObject({ score: 0,
    feedback_md: 'Second editor: keep this zero-score draft', revision_count: before.revision_count + 2 });
  await second.close();
});

test('real assignment rubric edit invalidates an opened grading page without creating a grade revision', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  const sid = fixture.teacherReviewSubmissionId;
  await page.goto(`/submission/${sid}`);
  const before = storedReview(fixture.databasePath, sid);
  const rubric = `Updated manual grading criterion ${Date.now()}`;
  const update = await apiJson(page, `/api/assignments/${fixture.teacherReviewAssignmentId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rubric_md: rubric }),
  });
  expect(update.status).toBe(200);
  const rejected = await gradeResponse(page, sid, '63.125', 'My draft after a rubric edit');
  expect(rejected.status()).toBe(409);
  expect(storedReview(fixture.databasePath, sid)).toEqual(before);
  await expect(page.locator('#grade-feedback')).toHaveValue('My draft after a rubric edit');
  await page.locator('#grade-conflict-refresh').click();
  await expect(page.locator('#grade-conflict-summary')).toContainText('作业要求或评分标准版本已变化');
  await page.locator('#grade-conflict-latest summary').click();
  await expect(page.locator('#grade-conflict-rubric')).toHaveText(rubric);
  await expect(page.getByTestId('p03-submit-manual-grade')).toBeDisabled();
});

test('real student detail exposes no grading controls and another teacher cannot mutate the score', async ({ browser }) => {
  const fixture = readFixture();
  const sid = fixture.teacherReviewSubmissionId;
  const before = storedReview(fixture.databasePath, sid);
  const otherContext = await browser.newContext();
  const other = await otherContext.newPage();
  await loginTeacher(other, fixture, fixture.otherTeacher);
  const denied = await apiJson(other, `/api/submissions/${sid}/grade`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ score: 100 }),
  });
  expect(denied.status).toBe(403);
  expect(storedReview(fixture.databasePath, sid)).toEqual(before);
  const studentContext = await browser.newContext();
  const student = await studentContext.newPage();
  await loginStudent(student, fixture);
  await student.goto(`/submission/${sid}`);
  await expect(student.locator('#answers-container')).toBeVisible();
  await expect(student.getByTestId('p03-submit-manual-grade')).toHaveCount(0);
  await expect(student.locator('#grade-conflict')).toHaveCount(0);
  await otherContext.close();
  await studentContext.close();
});
