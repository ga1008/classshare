import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginTeacher, loginStudent } from '../fixtures/p03';

test('S3 exam editor validates a title, preserves failed edits, and reads back the saved paper and revision', async ({ page }) => {
  const fixture = readS3Fixture(), id = fixture.s3.authoringPaperId, url = `/api/exam-papers/${id}`;
  await loginTeacher(page, fixture);
  await page.goto(`/exam/${id}/edit`);
  await expect(page.locator('.q-title-input').first()).toBeVisible();
  let writes = 0;
  page.on('request', request => { if (new URL(request.url()).pathname === url && request.method() === 'PUT') writes++; });
  await page.locator('#exam-title').fill('');
  await page.locator('#exam-save-button').press('Enter');
  await expect(page.getByText('请输入试卷标题', { exact: true })).toBeVisible();
  expect(writes).toBe(0);
  const title = 'S3 网络协议：保留未保存内容与完整评分标准';
  const question = '说明协议分层为什么有助于接口协作。\n\n**任务**：联系网络地址与网关，写出一个具体例子。';
  await page.locator('#exam-title').fill(title);
  await page.locator('.q-title-input').first().fill(question);
  await page.route(`**${url}`, route => route.request().method() === 'PUT'
    ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'S3 controlled save failure' }) })
    : route.continue(), { times: 1 });
  const failed = page.waitForResponse(response => new URL(response.url()).pathname === url && response.request().method() === 'PUT');
  await page.locator('#exam-save-button').press('Enter');
  expect((await failed).status()).toBe(503);
  await expect(page.locator('#exam-title')).toHaveValue(title);
  await expect(page.locator('.q-title-input').first()).toHaveValue(question);
  await expect(page.locator('#exam-save-button')).toBeEnabled();
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === url && response.request().method() === 'PUT');
  await page.locator('#exam-save-button').press('Enter');
  const response = await saved;
  expect(response.status()).toBe(200);
  const sent = response.request().postDataJSON(), result = await response.json();
  expect(sent.expected_revision).toMatch(/^[a-f0-9]{64}$/);
  expect(result.revision).toMatch(/^[a-f0-9]{64}$/);
  expect(result.revision).not.toBe(sent.expected_revision);
  const loaded = await (await page.request.get(url)).json();
  expect(loaded.paper.revision).toBe(result.revision);
  expect(loaded.paper.title).toBe(title);
  expect(JSON.parse(loaded.paper.questions_json).pages[0].questions[0]).toMatchObject({ text: question, points: 100 });
  await page.reload();
  await expect(page.locator('#exam-title')).toHaveValue(title);
  await expect(page.locator('.q-title-input').first()).toHaveValue(question);
  expect(writes).toBe(2);
});

test('S3 two exam editor windows cannot silently overwrite a newer revision', async ({ page, context }) => {
  const fixture = readS3Fixture(), id = fixture.s3.authoringPaperId, url = `/api/exam-papers/${id}`;
  await loginTeacher(page, fixture);
  const second = await context.newPage();
  await Promise.all([page.goto(`/exam/${id}/edit`), second.goto(`/exam/${id}/edit`)]);
  await page.locator('#exam-title').fill('S3 first editor accepted');
  await second.locator('#exam-title').fill('S3 second editor draft remains local');
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === url && response.request().method() === 'PUT');
  await page.locator('#exam-save-button').press('Enter');
  const first = await saved;
  expect(first.status()).toBe(200);
  const rejected = second.waitForResponse(response => new URL(response.url()).pathname === url && response.request().method() === 'PUT');
  await second.locator('#exam-save-button').press('Enter');
  const conflict = await rejected;
  expect(conflict.status()).toBe(409);
  expect((await conflict.json()).detail.code).toBe('revision_conflict');
  expect(conflict.request().postDataJSON().expected_revision).toBe(first.request().postDataJSON().expected_revision);
  await expect(second.locator('#exam-save-conflict')).toBeVisible();
  await expect(second.locator('#exam-title')).toHaveValue('S3 second editor draft remains local');
  expect((await (await page.request.get(url)).json()).paper.title).toBe('S3 first editor accepted');
  await second.close();
});

test('S3 exam answer lock and private ownership survive the optional revision contract', async ({ page, browser, baseURL }) => {
  const fixture = readS3Fixture(), id = 'lq-s3-wrong', url = `/api/exam-papers/${id}`;
  await loginTeacher(page, fixture);
  const before = readS3Rows('SELECT title,questions_json,updated_at FROM exam_papers WHERE id=?', [id]);
  const paper = (await (await page.request.get(url)).json()).paper;
  const questions = JSON.parse(paper.questions_json);
  questions.pages[0].questions[0].text = 'This existing answered paper must stay locked';
  const blocked = await page.request.put(url, { data: { title: paper.title, questions, expected_revision: paper.revision } });
  expect(blocked.status()).toBe(409);
  expect(JSON.stringify(await blocked.json())).toContain('已有学生提交或草稿');
  expect(readS3Rows('SELECT title,questions_json,updated_at FROM exam_papers WHERE id=?', [id])).toEqual(before);
  for (const role of ['teacher', 'student'] as const) {
    const isolated = await browser.newContext({ baseURL });
    try {
      const other = await isolated.newPage();
      if (role === 'teacher') await loginTeacher(other, fixture, fixture.otherTeacher);
      else await loginStudent(other, fixture);
      const denied = await other.request.put(url, { data: { title: 'Unauthorized write', expected_revision: paper.revision } });
      expect([403, 404]).toContain(denied.status());
      expect(readS3Rows('SELECT title,questions_json,updated_at FROM exam_papers WHERE id=?', [id])).toEqual(before);
    } finally { await isolated.close(); }
  }
});
