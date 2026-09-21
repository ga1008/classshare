import type { Page } from '@playwright/test';
import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

const summaryURL = (id: number) => `/assignment/${id}/wrong-summary`;
const statusURL = (id: number) => `/api/assignments/${id}/wrong-summary/status`;
const reorganizeURL = (id: number) => `/api/assignments/${id}/wrong-summary/reorganize`;
const jobsFor = (id: number) => readS3Rows(
  'SELECT id,status,run_token FROM assignment_wrong_summary_jobs WHERE assignment_id=? ORDER BY id', [id]);

async function cachedStatus(page: Page, id: number) {
  const response = await page.request.get(statusURL(id));
  expect(response.status()).toBe(200);
  const payload = await response.json();
  expect(payload).toMatchObject({ status: 'success', assignment_id: String(id), ai_status: { is_active: false } });
  return payload;
}

async function expectSummaryPermissionDenied(page: Page, id: number) {
  // HTML permission failures redirect to the existing warning page; API
  // failures retain their 403 response. A successful warning page is not data access.
  const response = await page.goto(summaryURL(id));
  expect(response?.status()).toBe(200);
  const deniedRequest = response?.request().redirectedFrom();
  expect(deniedRequest).not.toBeNull();
  expect(new URL(deniedRequest!.url()).pathname).toBe(summaryURL(id));
  expect((await deniedRequest!.response())?.status()).toBe(303);
  await expect(page).toHaveURL(/\/auth\/forbidden\?/);
  await expect(page.getByText('当前账号无权访问', { exact: true })).toBeVisible();
  await expect(page.locator('[data-wrong-summary-tabs]')).toHaveCount(0);
  await expect(page.locator('[data-wrong-summary-reorganize]')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('各层分别完成任务。');
}

test('S3 owner reads cached wrong/hard summaries, retains them on failed reorganize, and sees a real grade revision projection', async ({ page }, testInfo) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.wrongAssignmentId;
  const sid = fixture.s3.wrongSubmissionId;
  const original = readS3Rows<{ score: number; feedback_md: string; answers_json: string }>(
    'SELECT score,feedback_md,answers_json FROM submissions WHERE id=?', [sid])[0];
  expect(original.score).toBe(40);
  expect(original.feedback_md).toContain('得分：40/100');
  const jobsBefore = jobsFor(id);
  expect(jobsBefore).toEqual([]);
  const revisionsBefore = Number(readS3Rows<{ count: number }>(
    'SELECT COUNT(*) AS count FROM submission_grade_revisions WHERE submission_id=?', [sid])[0].count);
  let injectedStarts = 0;
  // Every attempted start in this test fails before reaching the application.
  // Never launch a real AI job just to manufacture an active polling state.
  await page.route(`**${reorganizeURL(id)}`, route => {
    injectedStarts++;
    return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'S3 bounded reorganize failure' }) });
  });
  await loginTeacher(page, fixture);
  const rendered = await page.goto(summaryURL(id));
  expect(rendered?.status()).toBe(200);
  await expect(page.locator('.wrong-summary-hero h1')).toHaveText('S3 wrong 独立任务');
  const wrong = page.locator('#wrong-summary-panel-errors');
  const hard = page.locator('#wrong-summary-panel-hard');
  await expect(wrong.locator('.wrong-summary-badge')).toHaveText('1 题');
  await expect(wrong.locator('.wrong-question-card')).toHaveCount(1);
  await expect(wrong.locator('.wrong-count-pill strong')).toHaveText('1');
  await expect(wrong.locator('[data-question-markdown]').first()).toContainText('说明协议分层的作用');
  await page.locator('#wrong-summary-tab-hard').click();
  await expect(hard).toBeVisible();
  await expect(hard.locator('.wrong-summary-badge')).toHaveText('1 题');
  await expect(hard.locator('.hard-question-card')).toHaveCount(1);
  await expect(hard.locator('.hard-question-body > p')).toContainText('40%');
  const initial = await cachedStatus(page, id);
  expect(initial.stats).toMatchObject({ question_count: 1, submitted_count: 1, wrong_question_count: 1,
    worst_wrong_count: 1, correct_question_count: 0 });
  expect((await cachedStatus(page, id)).stats).toEqual(initial.stats);
  expect(injectedStarts).toBe(0);
  expect(jobsFor(id)).toEqual(jobsBefore);

  await page.locator('#wrong-summary-tab-errors').click();
  await wrong.locator('[data-wrong-answer-group]').first().click();
  const detail = page.locator('[data-wrong-answer-modal]');
  await expect(detail).toBeVisible();
  await expect(detail.locator('[data-wrong-answer-modal-list]')).toContainText('各层分别完成任务。');
  await expect(detail.locator(`a[href^="/submission/${sid}?"]`)).toHaveCount(1);
  await page.locator('[data-wrong-answer-modal-close]').click();

  const failedStart = page.waitForResponse(response => new URL(response.url()).pathname === reorganizeURL(id));
  page.once('dialog', async dialog => { expect(dialog.type()).toBe('confirm'); await dialog.accept(); });
  await page.locator('[data-wrong-summary-reorganize]').click();
  expect((await failedStart).status()).toBe(503);
  const strip = page.locator('[data-wrong-summary-status-strip]');
  await expect(strip).toHaveClass(/is-failed/);
  await expect(strip).toContainText('整理没有发起成功，请稍后再试。');
  await expect(strip.locator('.wrong-summary-spinner')).toBeHidden();
  await expect(page.locator('[data-wrong-summary-reorganize]')).toBeEnabled();
  await expect(wrong.locator('.wrong-summary-badge')).toHaveText('1 题');
  await expect(hard.locator('.wrong-summary-badge')).toHaveText('1 题');
  expect((await cachedStatus(page, id)).stats).toEqual(initial.stats);
  expect(readS3Rows('SELECT score,feedback_md,answers_json FROM submissions WHERE id=?', [sid])).toEqual([original]);
  expect(jobsFor(id)).toEqual(jobsBefore);
  expect(injectedStarts).toBe(1);

  // Read the real review tokens and perform one authorized manual revision.
  // Neither the summary response nor the projected submissions row is mocked.
  const reviewResponse = await page.request.get(`/api/submissions/${sid}/review`);
  expect(reviewResponse.status()).toBe(200);
  const review = await reviewResponse.json();
  expect(review.expected_review_revision).toMatch(/^[a-f0-9]{64}$/);
  expect(review.expected_assignment_revision).toMatch(/^[a-f0-9]{64}$/);
  const feedback = '## 第1题\n得分：100/100\n分工与协作要点完整。';
  const graded = await page.request.post(`/api/submissions/${sid}/grade`, { data: {
    score: 100, feedback_md: feedback,
    expected_review_revision: review.expected_review_revision,
    expected_assignment_revision: review.expected_assignment_revision,
  } });
  expect(graded.status()).toBe(200);
  expect(readS3Rows('SELECT score,answers_json FROM submissions WHERE id=?', [sid])).toEqual([
    { score: 100, answers_json: original.answers_json },
  ]);
  expect(Number(readS3Rows<{ count: number }>('SELECT COUNT(*) AS count FROM submission_grade_revisions WHERE submission_id=?', [sid])[0].count))
    .toBe(revisionsBefore + 1);
  expect(readS3Rows('SELECT status FROM submission_grade_revisions WHERE submission_id=? AND status=?', [sid, 'active']))
    .toEqual([{ status: 'active' }]);
  const latest = await cachedStatus(page, id);
  expect(latest.stats).toMatchObject({ question_count: 1, submitted_count: 1, wrong_question_count: 0,
    worst_wrong_count: 0, correct_question_count: 1 });
  await page.reload();
  await expect(wrong.locator('.wrong-summary-badge')).toHaveText('0 题');
  await expect(wrong.locator('.wrong-question-card')).toHaveCount(0);
  await expect(wrong.locator('.wrong-summary-empty')).toContainText('暂未发现错答');
  await page.locator('#wrong-summary-tab-hard').click();
  await expect(hard.locator('.wrong-summary-badge')).toHaveText('0 题');
  await expect(hard.locator('.hard-question-card')).toHaveCount(0);
  await expect(hard.locator('.wrong-summary-empty')).toContainText('所有已识别逐题得分的题目均为满分');
  expect(jobsFor(id)).toEqual(jobsBefore);
  expect(injectedStarts).toBe(1);
  await testInfo.attach('wrong-summary-real-projection.json', {
    body: JSON.stringify({ before: initial.stats, after: latest.stats, revisionsBefore,
      revisionsAfter: revisionsBefore + 1, aiJobsBefore: jobsBefore, aiJobsAfter: jobsFor(id), injectedStarts }, null, 2),
    contentType: 'application/json',
  });
});

test('S3 student cannot read teacher wrong-summary HTML or cached status', async ({ page }) => {
  const fixture = readS3Fixture();
  await loginStudent(page, fixture);
  await expectSummaryPermissionDenied(page, fixture.s3.wrongAssignmentId);
  const denied = await page.request.get(statusURL(fixture.s3.wrongAssignmentId));
  expect(denied.status()).toBe(403);
  expect(await denied.text()).not.toContain('各层分别完成任务。');
});

test('S3 another teacher cannot read the owner classroom wrong-summary HTML or cached status', async ({ page }) => {
  const fixture = readS3Fixture();
  await loginTeacher(page, fixture, fixture.otherTeacher);
  await expectSummaryPermissionDenied(page, fixture.s3.wrongAssignmentId);
  const denied = await page.request.get(statusURL(fixture.s3.wrongAssignmentId));
  expect(denied.status()).toBe(403);
  const payload = await denied.text();
  expect(payload).toContain('无权查看');
  expect(payload).not.toContain('各层分别完成任务。');
});

test('S3 unsupported and missing tasks show unavailability instead of a successful zero-error summary', async ({ page }) => {
  const fixture = readS3Fixture();
  await loginTeacher(page, fixture);
  // This independent ordinary homework has no structured exam paper. It is
  // unavailable for aggregation even if its student has submitted an answer.
  const unsupported = await page.goto(summaryURL(fixture.s3.draftAssignmentId));
  expect(unsupported?.status()).toBe(200);
  await expect(page.locator('main > .wrong-summary-empty')).toContainText('暂不能生成错题归集');
  await expect(page.locator('main > .wrong-summary-empty')).toContainText('暂时没有结构化题目可归集');
  await expect(page.locator('[data-wrong-summary-tabs]')).toHaveCount(0);
  await expect(page.locator('[data-wrong-summary-reorganize]')).toHaveCount(0);
  await expect(page.getByText('暂未发现错答', { exact: true })).toHaveCount(0);
  // An absent task is a genuine service error, not a manufactured summary.
  const absentId = -1;
  expect(readS3Rows('SELECT id FROM assignments WHERE id=?', [absentId])).toEqual([]);
  const absent = await page.goto(summaryURL(absentId));
  expect(absent?.status()).toBe(404);
  await expect(page.locator('[data-wrong-summary-tabs]')).toHaveCount(0);
  const status = await page.request.get(statusURL(absentId));
  expect(status.status()).toBe(404);
  const error = await status.json();
  expect(error).not.toHaveProperty('stats');
  expect(error).not.toHaveProperty('knowledge_analysis');
});
