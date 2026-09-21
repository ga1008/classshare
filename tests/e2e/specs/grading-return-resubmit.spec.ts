import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

test('S3 return and resubmission retire the old grade and reject both stale student and teacher windows', async ({ page, browser }) => {
  test.setTimeout(120000);
  const fixture = readS3Fixture(), aid = fixture.s3.returnAssignmentId, sid = fixture.s3.returnSubmissionId;
  await loginTeacher(page, fixture);
  const firstReview = await page.request.get(`/api/submissions/${sid}/review`);
  expect(firstReview.status()).toBe(200);
  const first = await firstReview.json();
  const firstGrade = await page.request.post(`/api/submissions/${sid}/grade`, { data: {
    score: 79, feedback_md: 'S3 round one grade',
    expected_review_revision: first.expected_review_revision,
    expected_assignment_revision: first.expected_assignment_revision,
  } });
  expect(firstGrade.status()).toBe(200);
  await page.goto(`/submission/${sid}`);
  await page.getByTestId('p03-submission-score-input').fill('99');
  await page.locator('#grade-feedback').fill('S3 stale teacher draft');
  const returned = await page.request.post(`/api/assignments/${aid}/submissions/withdraw`, {
    data: { submission_ids: [sid], reason: 'S3 correct the missing explanation', extension_minutes: 60 },
  });
  expect(returned.status()).toBe(200);
  expect((await returned.json()).updated_count).toBe(1);

  const studentContext = await browser.newContext();
  const student = await studentContext.newPage();
  await loginStudent(student, fixture);
  const staleStudent = await studentContext.newPage();
  await Promise.all([student.goto(`/assignment/${aid}`), staleStudent.goto(`/assignment/${aid}`)]);
  await staleStudent.locator('.answer-textarea').first().fill('S3 stale student draft must survive rejection.');
  // Reopen a newer round while the first round's window stays open. This reaches
  // the actual version guard; an already-completed round is rejected earlier
  // with the separate existing 400 "already submitted" business response.
  const reopened = await page.request.post(`/api/assignments/${aid}/submissions/withdraw`, {
    data: { submission_ids: [sid], reason: 'S3 replace the resubmission window', extension_minutes: 90 },
  });
  expect(reopened.status()).toBe(200);
  const studentRejection = staleStudent.waitForResponse(response => new URL(response.url()).pathname === `/api/assignments/${aid}/submit`
    && response.request().method() === 'POST');
  await staleStudent.getByTestId('p03-submit-assignment').click();
  expect((await studentRejection).status()).toBe(409);
  await expect(staleStudent.locator('.answer-textarea').first()).toHaveValue('S3 stale student draft must survive rejection.');
  await student.reload();
  await student.locator('.answer-textarea').first().fill('S3 round two: layers divide responsibilities and cooperate through interfaces.');
  const saved = student.waitForResponse(response => new URL(response.url()).pathname === `/api/assignments/${aid}/submit`
    && response.request().method() === 'POST');
  await student.getByTestId('p03-submit-assignment').click();
  expect((await saved).status()).toBe(200);
  await expect.poll(() => readS3Rows<{ resubmission_allowed: number }>(
    'SELECT resubmission_allowed FROM submissions WHERE id=?', [sid])[0].resubmission_allowed).toBe(0);
  const currentReview = await page.request.get(`/api/submissions/${sid}/review`);
  expect(currentReview.status()).toBe(200);
  const current = await currentReview.json();
  expect(current.submission.answers_json).toContain('S3 round two');
  expect(current.submission.score).toBeNull();
  expect(current.expected_review_revision).not.toBe(first.expected_review_revision);
  const secondGrade = await page.request.post(`/api/submissions/${sid}/grade`, { data: {
    score: 33, feedback_md: 'S3 round two grade only',
    expected_review_revision: current.expected_review_revision,
    expected_assignment_revision: current.expected_assignment_revision,
  } });
  expect(secondGrade.status()).toBe(200);

  const teacherRejection = page.waitForResponse(response => new URL(response.url()).pathname === `/api/submissions/${sid}/grade`
    && response.request().method() === 'POST');
  await page.getByTestId('p03-submit-manual-grade').click();
  expect((await teacherRejection).status()).toBe(409);
  await expect(page.locator('#grade-feedback')).toHaveValue('S3 stale teacher draft');
  await expect(page.locator('#grade-conflict')).toBeVisible();
  const stored = readS3Rows<{ score: number; answers_json: string; feedback_md: string }>(
    'SELECT score,answers_json,feedback_md FROM submissions WHERE id=?', [sid])[0];
  expect(stored.score).toBe(33);
  expect(stored.answers_json).toContain('S3 round two');
  expect(stored.answers_json).not.toContain('S3 stale');
  expect(stored.feedback_md).toBe('S3 round two grade only');
  expect(readS3Rows('SELECT score FROM submission_grade_revisions WHERE submission_id=? AND status=?', [sid, 'active'])).toEqual([{ score: 33 }]);
  await studentContext.close();
});
