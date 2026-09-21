import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginTeacher } from '../fixtures/p03';

test('S3 simultaneous grading clicks create one effective revision and one in-app result notification', async ({ page, context }) => {
  const fixture = readS3Fixture(), sid = fixture.s3.concurrencySubmissionId;
  await loginTeacher(page, fixture);
  const second = await context.newPage();
  await Promise.all([page.goto(`/submission/${sid}`), second.goto(`/submission/${sid}`)]);
  const editors = [page, second];
  for (const [index, editor] of editors.entries()) {
    await editor.getByTestId('p03-submission-score-input').fill(index ? '0' : '82.75');
    await editor.locator('#grade-feedback').fill(`S3 competing editor ${index}`);
  }
  const notificationCount = () => Number(readS3Rows<{ count: number }>(
    'SELECT COUNT(*) AS count FROM message_center_notifications WHERE category=? AND link_url=?',
    ['grading_result', `/submission/${sid}`])[0].count);
  const beforeNotifications = notificationCount();
  const beforeRevisions = Number(readS3Rows<{ count: number }>(
    'SELECT COUNT(*) AS count FROM submission_grade_revisions WHERE submission_id=?', [sid])[0].count);
  const responses = editors.map(editor => editor.waitForResponse(response =>
    new URL(response.url()).pathname === `/api/submissions/${sid}/grade` && response.request().method() === 'POST'));
  await Promise.all(editors.map(editor => editor.getByTestId('p03-submit-manual-grade').click()));
  const outcomes = await Promise.all(responses);
  expect(outcomes.map(response => response.status()).sort()).toEqual([200, 409]);
  const payloads = outcomes.map(response => response.request().postDataJSON());
  expect(payloads[0].expected_review_revision).toBe(payloads[1].expected_review_revision);
  expect(payloads[0].expected_assignment_revision).toBe(payloads[1].expected_assignment_revision);
  const winner = outcomes.findIndex(response => response.status() === 200), loser = 1 - winner;
  await expect(editors[loser].locator('#grade-conflict')).toBeVisible();
  await expect(editors[loser].getByTestId('p03-submit-manual-grade')).toBeDisabled();
  await expect(editors[loser].locator('#grade-feedback')).toHaveValue(`S3 competing editor ${loser}`);
  await expect(editors[loser].getByTestId('p03-submission-score-input')).toHaveValue(loser ? '0' : '82.75');
  expect(readS3Rows('SELECT score,feedback_md FROM submissions WHERE id=?', [sid])).toEqual([
    { score: winner ? 0 : 82.75, feedback_md: `S3 competing editor ${winner}` },
  ]);
  expect(readS3Rows('SELECT status FROM submission_grade_revisions WHERE submission_id=? AND status=?', [sid, 'active'])).toEqual([{ status: 'active' }]);
  expect(Number(readS3Rows<{ count: number }>('SELECT COUNT(*) AS count FROM submission_grade_revisions WHERE submission_id=?', [sid])[0].count)).toBe(beforeRevisions + 1);
  expect(notificationCount()).toBe(beforeNotifications + 1);
  await second.close();
});
