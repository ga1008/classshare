import type { Browser, Page, Request } from '@playwright/test';
import { test, expect, guardS3Page, readS3Fixture, readS3Rows, type S3Fixture } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

const answerInput = (page: Page) => page.locator('textarea[data-text-input="q1"]');
const endpoint = (id: number, action: string) => `/api/assignments/${id}/${action}`;
const responseFor = (page: Page, id: number, action: string, method = 'POST') => page.waitForResponse(
  response => response.url().endsWith(endpoint(id, action)) && response.request().method() === method,
  { timeout: 20_000 },
);
async function postedForm(request: Request) {
  const bytes = request.postDataBuffer();
  expect(bytes).not.toBeNull();
  return new Response(new Uint8Array(bytes!), { headers: { 'Content-Type': request.headers()['content-type'] } }).formData();
}
async function openExam(page: Page, id: number) {
  const loading = responseFor(page, id, 'draft', 'GET');
  await page.goto(`/exam/take/${id}`);
  const response = await loading;
  expect(response.status()).toBe(200);
  await expect(page.locator('#examTopbar')).toBeVisible();
  await expect(answerInput(page)).toBeVisible();
  await expect(page.locator('#topbarSubmitBtn')).toBeEnabled();
  return response.json();
}
async function localAnswer(page: Page, id: number, answer: string) {
  await answerInput(page).fill(answer);
  await expect.poll(() => page.evaluate(key => JSON.parse(localStorage.getItem(key) || '{}').answers?.q1, `exam_${id}`)).toBe(answer);
}
function submissions(id: number, fixture: S3Fixture) {
  return readS3Rows<{ id: number; answers_json: string; status: string; resubmission_allowed: number }>(
    'SELECT id,answers_json,status,resubmission_allowed FROM submissions WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id]);
}
async function teacherPage(browser: Browser, baseURL: string | undefined, fixture: S3Fixture) {
  const context = await browser.newContext({ baseURL });
  try {
    // The automatic S3 guard belongs to the student context. Explicitly guard
    // this additional authenticated context before any business request.
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol) ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    await guardS3Page(page);
    await loginTeacher(page, fixture);
    return { page, context };
  } catch (error) { await context.close(); throw error; }
}

test('S3 exam uses native manual confirmation and the opened version for the real draft and final submit', async ({ page }) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.examTakeAssignmentId;
  await loginStudent(page, fixture);
  const opened = await openExam(page, id);
  expect(opened.submission_version).toBe('unsubmitted');
  if (opened.exists) {
    const restoredNotice = page.locator('.lq-toast').filter({ hasText: '已恢复服务器自动保存的作答进度。' });
    await restoredNotice.getByRole('button', { name: '关闭通知', exact: true }).click();
    await expect(restoredNotice).toHaveCount(0);
  }
  // A previous interrupted attempt may have a real server draft. Establish
  // this empty-answer case through the editor, not by resetting its database.
  await answerInput(page).fill('');
  const mutations: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith(endpoint(id, 'submit'))) mutations.push(request.url()); });
  await page.locator('#topbarSubmitBtn').click();
  const emptyNotice = page.locator('.lq-toast').filter({ hasText: '请至少作答一道题或上传附件后再提交。' });
  await expect(emptyNotice).toBeVisible();
  await emptyNotice.getByRole('button', { name: '关闭通知', exact: true }).click();
  await expect(emptyNotice).toHaveCount(0);
  expect(mutations).toEqual([]);
  const answer = 'S3 手动交卷：各层分工，通过接口协作。';
  await localAnswer(page, id, answer);
  page.once('dialog', dialog => dialog.dismiss());
  await page.locator('#topbarSubmitBtn').click();
  await expect(answerInput(page)).toHaveValue(answer);
  expect(mutations).toEqual([]);
  const draft = responseFor(page, id, 'draft');
  const final = responseFor(page, id, 'submit');
  page.once('dialog', async dialog => { expect(dialog.type()).toBe('confirm'); await dialog.accept(); });
  await page.locator('#topbarSubmitBtn').click();
  const draftResponse = await draft;
  const submitResponse = await final;
  expect(draftResponse.status()).toBe(200);
  expect(submitResponse.status()).toBe(200);
  expect((await postedForm(draftResponse.request())).get('expected_submission_version')).toBe('unsubmitted');
  const form = await postedForm(submitResponse.request());
  expect(form.get('expected_submission_version')).toBe('unsubmitted');
  expect(form.get('use_server_draft')).toBe('1');
  expect(String(form.get('started_at'))).toMatch(/^\d{4}-\d{2}-\d{2}T/);
  expect(String(form.get('answers_json'))).toContain(answer);
  expect((await submitResponse.json()).auto_ai_grading_scheduled).toBe(false);
  await expect(page.locator('body')).toHaveClass(/exam-is-submitted/);
  expect(await page.evaluate(key => localStorage.getItem(key), `exam_${id}`)).toBeNull();
  expect(submissions(id, fixture)).toHaveLength(1);
  expect(submissions(id, fixture)[0].answers_json).toContain(answer);
  expect(mutations).toHaveLength(1);
});

test('S3 injected final 503 preserves exam input and local File until an explicit real retry succeeds', async ({ page }) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.examFailureAssignmentId;
  await loginStudent(page, fixture);
  await openExam(page, id);
  const answer = 'S3 失败保值：这段作答和附件必须保留，不能伪装为已交卷。';
  const fileName = 's3-exam-retry.txt';
  await localAnswer(page, id, answer);
  await page.locator('#exam-file-input').setInputFiles({ name: fileName, mimeType: 'text/plain', buffer: Buffer.from('Keep this local exam File until success.') });
  await expect(page.locator('#exam-file-chips')).toContainText(fileName);
  // This is explicitly a transport-failure probe; later 200 and DB evidence
  // come from the real service, never a mocked successful response.
  await page.route(`**${endpoint(id, 'submit')}`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'S3 注入的暂时故障，请保留作答后重试' }) }), { times: 1 });
  page.on('dialog', dialog => dialog.accept());
  const failed = responseFor(page, id, 'submit');
  await page.locator('#submitExamBtn').click();
  const failure = await failed;
  expect(failure.status()).toBe(503);
  const before = await postedForm(failure.request());
  expect(before.getAll('files').map(file => (file as File).name)).toContain(fileName);
  await expect(page.getByText('S3 注入的暂时故障，请保留作答后重试', { exact: true })).toBeVisible();
  await expect(page.locator('#submitExamBtn')).toBeEnabled();
  await expect(answerInput(page)).toHaveValue(answer);
  await expect(page.locator('#exam-file-chips')).toContainText(fileName);
  expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || '{}').answers?.q1, `exam_${id}`)).toBe(answer);
  expect(submissions(id, fixture)).toEqual([]);
  const retry = responseFor(page, id, 'submit');
  await page.locator('#submitExamBtn').click();
  const success = await retry;
  expect(success.status()).toBe(200);
  const after = await postedForm(success.request());
  expect(after.getAll('files').map(file => (file as File).name)).toContain(fileName);
  expect(after.get('expected_submission_version')).toBe(before.get('expected_submission_version'));
  expect(after.get('started_at')).toBe(before.get('started_at'));
  expect((await success.json()).auto_ai_grading_scheduled).toBe(false);
  await expect(page.locator('body')).toHaveClass(/exam-is-submitted/);
  expect(await page.evaluate(key => localStorage.getItem(key), `exam_${id}`)).toBeNull();
  expect(submissions(id, fixture)).toHaveLength(1);
  const stored = submissions(id, fixture)[0];
  expect(stored.answers_json).toContain(answer);
  expect(readS3Rows('SELECT original_filename FROM submission_files WHERE submission_id=?', [stored.id])).toEqual([{ original_filename: fileName }]);
});

test('S3 exam restores a server draft then rejects an old page after a real submit and teacher return', async ({ page, context, browser, baseURL }) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.examDraftAssignmentId;
  const teacher = await teacherPage(browser, baseURL, fixture);
  try {
    await loginStudent(page, fixture);
    const openedVersion = (await openExam(page, id)).submission_version;
    expect(openedVersion).toBe('unsubmitted');
    const saved = responseFor(page, id, 'draft');
    const original = 'S3 跨页考试草稿：服务器保留分工与协作。';
    await localAnswer(page, id, original);
    expect((await saved).status()).toBe(200);
    await page.goto('/dashboard');
    await page.evaluate(key => localStorage.removeItem(key), `exam_${id}`);
    const restored = await openExam(page, id);
    expect(restored.answers_json).toContain(original);
    await expect(answerInput(page)).toHaveValue(original);
    const winner = await context.newPage();
    await openExam(winner, id);
    const staleAnswer = '旧页面仍未提交的改动：必须保留，不自动覆盖新轮次。';
    await localAnswer(page, id, staleAnswer);
    const winnerAnswer = '另一页面已经正式提交的作答。';
    await localAnswer(winner, id, winnerAnswer);
    winner.once('dialog', dialog => dialog.accept());
    const completed = responseFor(winner, id, 'submit');
    await winner.locator('#topbarSubmitBtn').click();
    const accepted = await completed;
    expect(accepted.status()).toBe(200);
    const result = await accepted.json();
    expect(result.auto_ai_grading_scheduled).toBe(false);
    const returned = await teacher.page.request.post(`/api/assignments/${id}/submissions/withdraw`, {
      data: { submission_ids: [result.submission_id], reason: 'S3 固定版本冲突验收', extension_minutes: 60 },
    });
    expect(returned.status()).toBe(200);
    expect((await returned.json()).updated_count).toBe(1);
    const fresh = await page.request.get(endpoint(id, 'draft'));
    expect(fresh.status()).toBe(200);
    expect((await fresh.json()).submission_version).not.toBe(openedVersion);
    // The real GET must not silently rebase the already-open page's constant.
    const finalRequests: string[] = [];
    page.on('request', request => { if (request.method() === 'POST' && request.url().endsWith(endpoint(id, 'submit'))) finalRequests.push(request.url()); });
    page.on('dialog', dialog => dialog.accept());
    const conflicted = responseFor(page, id, 'draft');
    await page.locator('#topbarSubmitBtn').click();
    const conflict = await conflicted;
    expect(conflict.status()).toBe(409);
    expect((await postedForm(conflict.request())).get('expected_submission_version')).toBe(openedVersion);
    await expect(page.locator('#saveStatus')).toHaveClass(/error/);
    await expect(answerInput(page)).toHaveValue(staleAnswer);
    await expect(page.locator('#topbarSubmitBtn')).toBeEnabled();
    expect(finalRequests).toEqual([]);
    const current = submissions(id, fixture);
    expect(current).toHaveLength(1);
    expect(current[0].answers_json).toContain(winnerAnswer);
    expect(current[0].resubmission_allowed).toBe(1);
    await winner.close();
  } finally { await teacher.context.close(); }
});

test('S3 a request begun before the deadline is rejected by the real server after the deadline', async ({ page, browser, baseURL }, testInfo) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.examDeadlineAssignmentId;
  const teacher = await teacherPage(browser, baseURL, fixture);
  let release!: () => void;
  const released = new Promise<void>(resolve => { release = resolve; });
  let requestHeld = false;
  try {
    await loginStudent(page, fixture);
    await openExam(page, id);
    const answer = 'S3 截止竞态：以服务器接受请求时的规则为准，失败后仍保留作答。';
    await localAnswer(page, id, answer);
    const timeURL = `/api/assignments/time-state?ids=${id}`;
    const time = await teacher.page.request.get(timeURL);
    expect(time.status()).toBe(200);
    const initial = await time.json();
    // Preserve the server's naive local wall-time representation. Do not use
    // the browser clock or silently convert its timezone to choose the cutoff.
    const due = new Date(Date.parse(`${initial.server_now}Z`) + 12_000).toISOString().slice(0, 19);
    const configured = await teacher.page.request.put(`/api/assignments/${id}`, {
      data: { availability_mode: 'deadline', due_at: due, late_submission_enabled: false },
    });
    expect(configured.status()).toBe(200);
    expect((await configured.json()).assignment_status).toBe('published');
    await page.route(`**${endpoint(id, 'submit')}`, async route => {
      requestHeld = true;
      await released;
      // Delay only; the actual backend receives and decides this request.
      await route.continue();
    }, { times: 1 });
    page.once('dialog', dialog => dialog.accept());
    const rejected = responseFor(page, id, 'submit');
    await page.locator('#topbarSubmitBtn').click();
    await expect.poll(() => requestHeld).toBe(true);
    const atArrival = await (await teacher.page.request.get(timeURL)).json();
    expect(atArrival.assignments[0]).toMatchObject({ is_accepting_submissions: true, deadline_phase: 'regular' });
    let closed: any;
    await expect.poll(async () => {
      const state = await teacher.page.request.get(timeURL);
      expect(state.status()).toBe(200);
      closed = await state.json();
      return closed.assignments[0]?.is_accepting_submissions;
    }, { timeout: 20_000, intervals: [250, 500, 1000] }).toBe(false);
    expect(closed.assignments[0].deadline_phase).toBe('closed');
    expect(closed.server_now >= due).toBe(true);
    release();
    const response = await rejected;
    expect(response.status()).toBe(400);
    expect(JSON.stringify(await response.json())).toContain('截止');
    await expect(answerInput(page)).toHaveValue(answer);
    expect(await page.evaluate(key => JSON.parse(localStorage.getItem(key) || '{}').answers?.q1, `exam_${id}`)).toBe(answer);
    expect(submissions(id, fixture)).toEqual([]);
    await testInfo.attach('real-server-deadline-boundary.json', {
      body: JSON.stringify({ due, atArrival, closed, rejectedStatus: response.status() }, null, 2), contentType: 'application/json',
    });
  } finally { release(); await teacher.context.close(); }
});
