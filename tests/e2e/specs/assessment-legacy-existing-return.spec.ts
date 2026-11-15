import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginStudent } from '../fixtures/p03';

for (const kind of ['Homework', 'ExamFiles', 'ExamLocal'] as const) {
  test(`legacy already returned ${kind} opens without teacher intervention and preserves original attachments`, async ({ page }) => {
    test.setTimeout(90000);
    const fixture = readS3Fixture();
    const id = fixture.s3[`legacyReturned${kind}AssignmentId`];
    const sid = fixture.s3[`legacyReturned${kind}SubmissionId`];
    const exam = kind.startsWith('Exam');
    const route = exam ? `/exam/take/${id}` : `/assignment/${id}`;
    const endpoint = `/api/assignments/${id}`;
    const fileName = `legacy-returned-${kind.toLowerCase()}.txt`;
    const newerLocalAnswer = '本代次最新本机答案：不能被延迟补建的旧答卷草稿覆盖。';
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    expect(readS3Rows('SELECT id FROM submission_drafts WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id])).toEqual([]);
    await loginStudent(page, fixture);
    if (kind === 'ExamLocal') {
      // SSR alone does not create a draft. Recover the actual round token,
      // then represent a student's newer, already saved local draft.
      const snapshot = await page.request.get(route);
      expect(snapshot.status()).toBe(200);
      const match = (await snapshot.text()).match(/const SUBMISSION_VERSION = (.*?);/);
      expect(match).not.toBeNull();
      const version = JSON.parse(match![1]);
      await page.evaluate(({ key, text }) => localStorage.setItem(key, JSON.stringify({
        answers: { q1: text }, drawings: {}, currentPage: 0, updatedAt: new Date().toISOString(),
      })), { key: `lsdraft:v2:${fixture.student.id}:exam:${id}:${version}`, text: newerLocalAnswer });
    }
    const loading = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft`
      && response.request().method() === 'GET');
    expect((await page.goto(route))?.status()).toBe(200);
    if (exam) await page.locator('#resubmitExamBtn').click();
    const response = await loading;
    expect(response.status()).toBe(200);
    const restored = await response.json();
    expect(restored.exists).toBe(true);
    expect(restored.files.map((file: any) => file.file_name)).toEqual([fileName]);
    expect(readS3Rows('SELECT id FROM submission_drafts WHERE assignment_id=? AND student_pk_id=?', [id, fixture.student.id])).toHaveLength(1);
    if (exam) {
      await expect(page.locator('#topbarPageLabel')).toHaveText('第 1 页 / 共 2 页');
      // The generic-file footer exists only on page two. Submitting here must
      // consult the restored server file state, even without a mounted manager.
      await expect(page.locator('#exam-file-chips')).toHaveCount(0);
      await expect(page.locator('textarea[data-text-input="q1"]')).toHaveValue(kind === 'ExamLocal' ? newerLocalAnswer : '');
    } else {
      await expect(page.locator('.answer-textarea').first()).toHaveValue('存量旧答案：分层分工、接口协作。');
      await expect(page.locator('#file-chips')).toContainText(fileName);
      await page.locator('.answer-textarea').first().fill('存量重交的新答案：补充协议层次与协作。');
    }
    const submitted = page.waitForResponse(result => new URL(result.url()).pathname === `${endpoint}/submit`
      && result.request().method() === 'POST');
    if (exam) {
      page.on('dialog', dialog => dialog.accept());
      await page.locator('#topbarSubmitBtn').click();
    } else {
      await page.getByTestId('p03-submit-assignment').click();
      await page.getByRole('dialog').getByRole('button', { name: '确认提交', exact: true }).click();
    }
    const finalResponse = await submitted;
    expect(finalResponse.status(), await finalResponse.text()).toBe(200);
    expect((await finalResponse.json()).submission_id).toBe(sid);
    const current = readS3Rows<{ resubmission_allowed: number; answers_json: string }>(
      'SELECT resubmission_allowed,answers_json FROM submissions WHERE id=?', [sid])[0];
    expect(current.resubmission_allowed).toBe(0);
    if (kind === 'ExamLocal') expect(current.answers_json).toContain(newerLocalAnswer);
    const files = readS3Rows<{ id: number; original_filename: string }>('SELECT id,original_filename FROM submission_files WHERE submission_id=?', [sid]);
    expect(files).toHaveLength(1);
    expect(files[0].original_filename).toBe(fileName);
    const downloaded = await page.request.get(`/submissions/download/${files[0].id}`);
    expect(downloaded.status()).toBe(200);
    expect((await downloaded.body()).toString()).toBe('Stored attachment before lazy resubmission recovery.\n');
    expect(errors).toEqual([]);
  });
}
