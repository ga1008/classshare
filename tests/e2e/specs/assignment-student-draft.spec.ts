import type { Request } from '@playwright/test';
import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { collectBrowserErrors, expectNoBrowserErrors, loginStudent } from '../fixtures/p03';

async function postedForm(request: Request) {
  const bytes = request.postDataBuffer();
  expect(bytes).not.toBeNull();
  return new Response(new Uint8Array(bytes!), {
    headers: { 'Content-Type': request.headers()['content-type'] },
  }).formData();
}

test('S3 homework restores real server text and attachments on another page before manual submission', async ({ page, context }, testInfo) => {
  const fixture = readS3Fixture();
  const id = fixture.s3.draftAssignmentId;
  const endpoint = `/api/assignments/${id}`;
  const answer = 'S3 草稿：权限必须由服务器复核，分层接口必须保留原有提交副作用。';
  const fileName = 's3-homework-draft.txt';
  const errors = collectBrowserErrors(page);
  await loginStudent(page, fixture);
  const initialDraft = page.waitForResponse(r => r.url().endsWith(`${endpoint}/draft`) && r.request().method() === 'GET');
  await page.goto(`/assignment/${id}`);
  expect((await (await initialDraft).json()).submission_version).toBe('unsubmitted');
  await expect(page.getByTestId('p03-assignment-answer-area')).toBeVisible();
  await page.locator('.answer-textarea').first().fill(answer);
  const localKey = `assignment_draft_${id}_unsubmitted`;
  await expect.poll(() => page.evaluate(key => localStorage.getItem(key), localKey)).toContain(answer);

  // The production attachment save also carries the text draft. No fixture
  // response is substituted for the actual storage operation.
  const savedDraft = page.waitForResponse(r => r.url().endsWith(`${endpoint}/draft`) && r.request().method() === 'POST');
  await page.locator('#file-input').setInputFiles({ name: fileName, mimeType: 'text/plain', buffer: Buffer.from('S3 homework attachment: keep across pages.') });
  const saved = await savedDraft;
  expect(saved.status()).toBe(200);
  const draftForm = await postedForm(saved.request());
  expect(draftForm.get('expected_submission_version')).toBe('unsubmitted');
  expect(String(draftForm.get('answers_json'))).toContain(answer);
  expect((await saved.json()).files.some((file: any) => file.file_name === fileName || file.original_filename === fileName)).toBe(true);
  expect(readS3Rows('SELECT id FROM submissions WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id])).toEqual([]);
  await page.goto('/dashboard');
  // Remove only this test's local copy after leaving the editor: the next
  // page must recover through GET /draft, not shared localStorage.
  await page.evaluate(key => localStorage.removeItem(key), localKey);

  const restored = await context.newPage();
  const restoredErrors = collectBrowserErrors(restored);
  const load = restored.waitForResponse(r => r.url().endsWith(`${endpoint}/draft`) && r.request().method() === 'GET');
  await restored.goto(`/assignment/${id}`);
  const payload = await (await load).json();
  expect(payload).toMatchObject({ exists: true, submission_version: 'unsubmitted' });
  expect(payload.answers_json).toContain(answer);
  await expect(restored.locator('.answer-textarea').first()).toHaveValue(answer);
  await expect(restored.locator('#file-chips')).toContainText(fileName);
  await expect(restored.locator('#answer-autosave-status')).toHaveAttribute('data-tone', 'saved');

  // A failed final response must retain both the restored attachment and text
  // and leave the production button usable for an explicit real retry.
  await restored.route(`**${endpoint}/submit`, route => route.fulfill({ status: 503,
    contentType: 'application/json', body: JSON.stringify({ detail: 'S3 homework temporary failure' }) }), { times: 1 });
  const failing = restored.waitForResponse(r => r.url().endsWith(`${endpoint}/submit`) && r.request().method() === 'POST');
  await restored.getByTestId('p03-submit-assignment').click();
  expect((await failing).status()).toBe(503);
  await expect(restored.getByTestId('p03-submit-assignment')).toBeEnabled();
  await expect(restored.locator('.answer-textarea').first()).toHaveValue(answer);
  await expect(restored.locator('#file-chips')).toContainText(fileName);
  expect(await restored.evaluate(key => localStorage.getItem(key), localKey)).toContain(answer);
  expect(readS3Rows('SELECT id FROM submissions WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id])).toEqual([]);

  const submitting = restored.waitForResponse(r => r.url().endsWith(`${endpoint}/submit`) && r.request().method() === 'POST');
  await restored.getByTestId('p03-submit-assignment').click();
  const submitted = await submitting;
  expect(submitted.status()).toBe(200);
  const form = await postedForm(submitted.request());
  expect(form.get('expected_submission_version')).toBe('unsubmitted');
  expect(form.get('use_server_draft')).toBe('1');
  expect(String(form.get('answers_json'))).toContain(answer);
  const result = await submitted.json();
  expect(result.auto_ai_grading_scheduled).toBe(false);
  await expect(restored.locator('#submitted-answers-container')).toContainText(answer);
  expect(await restored.evaluate(key => localStorage.getItem(key), localKey)).toBeNull();
  const rows = readS3Rows<{ id: number; answers_json: string; status: string }>(
    'SELECT id,answers_json,status FROM submissions WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id]);
  expect(rows).toHaveLength(1);
  expect(rows[0]).toMatchObject({ id: result.submission_id, status: 'submitted' });
  expect(rows[0].answers_json).toContain(answer);
  expect(readS3Rows('SELECT original_filename FROM submission_files WHERE submission_id=?', [rows[0].id]))
    .toEqual([{ original_filename: fileName }]);
  // Chromium reports the deliberately injected 503 as a resource error.
  await expectNoBrowserErrors([...errors, ...restoredErrors].filter(error => !/Failed to load resource:.*503/.test(error)), testInfo);
  await restored.close();
});
