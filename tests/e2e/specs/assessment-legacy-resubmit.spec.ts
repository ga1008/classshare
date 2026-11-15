import type { Page } from '@playwright/test';
import { test, expect, guardS3Page, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginStudent, loginTeacher } from '../fixtures/p03';

for (const kind of ['homework', 'exam'] as const) {
  test(`legacy ${kind}: real teacher return restores old answers and files; a stale round cannot overwrite resubmission`, async ({ page, context, browser, baseURL }, testInfo) => {
    test.setTimeout(120000);
    const fixture = readS3Fixture();
    const id = kind === 'exam' ? fixture.s3.legacyExamRetakeAssignmentId : fixture.s3.legacyHomeworkRetakeAssignmentId;
    const endpoint = `/api/assignments/${id}`;
    const route = kind === 'exam' ? `/exam/take/${id}` : `/assignment/${id}`;
    const original = `旧${kind}答案：各层分工，通过接口协作。`;
    const revised = `重交${kind}答案：保留原有分析，并补充上下层接口约定。`;
    const staleText = `旧代次${kind}草稿必须保留在页面，不得覆盖新代次。`;
    const fileName = `legacy-${kind}-retained.txt`;
    const removedFileName = 'legacy-exam-remove.txt';
    const lostResponseFileName = 'legacy-exam-response-lost.txt';
    const fileBytes = Buffer.from(`Original ${kind} attachment must survive the teacher return.\n`);
    const pageErrors: string[] = [];
    const observe = (target: Page) => target.on('pageerror', error => pageErrors.push(error.message));
    const answer = (target: Page) => kind === 'exam'
      ? target.locator('textarea[data-text-input="q1"]') : target.locator('.answer-textarea').first();
    const files = (target: Page) => target.locator(kind === 'exam' ? '#exam-file-chips' : '#file-chips');
    const waitMutation = (target: Page, action: string) => target.waitForResponse(response =>
      new URL(response.url()).pathname === `${endpoint}/${action}` && response.request().method() === 'POST');
    const teacherContext = await browser.newContext({ baseURL });
    await teacherContext.route('**/*', intercepted => {
      const url = new URL(intercepted.request().url());
      return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
        ? intercepted.continue() : intercepted.abort();
    });
    const teacher = await teacherContext.newPage();
    observe(page);
    observe(teacher);
    try {
      await guardS3Page(teacher);
      await loginStudent(page, fixture);
      const openedDraft = page.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft`
        && response.request().method() === 'GET');
      const initial = await page.goto(route);
      expect(initial?.status()).toBe(200);
      expect((await (await openedDraft).json()).submission_version).toBe('unsubmitted');
      await expect(answer(page)).toBeVisible();
      if (kind === 'exam') {
        // Historical third-party blanks labels never declared numbered answer
        // fields: their scalar text answer must stay a single editable input.
        await expect(page.locator('textarea[data-text-input="q1"]')).toHaveCount(1);
        await expect(page.locator('textarea[data-blank-index]')).toHaveCount(0);
      }

      // Submit supported historical JSON through the real API. This preserves
      // the historical representation without write-capable fixture access or
      // a mocked successful server response. The open page remains old-round.
      const oldPayload = kind === 'exam'
        ? { answers: [null, 7, 'invalid', { question_id: 'q1', question: '说明协议分层的作用。', answer: original }] }
        : { answers: { '1. 说明协议分层的作用。': original } };
      const uploadNames = kind === 'exam' ? [fileName, removedFileName, lostResponseFileName] : [fileName];
      const firstForm = new FormData();
      firstForm.append('answers_json', JSON.stringify(oldPayload));
      firstForm.append('expected_submission_version', 'unsubmitted');
      firstForm.append('manifest', JSON.stringify(uploadNames.map(name => ({ relative_path: name, kind: 'file', question_id: '' }))));
      for (const name of uploadNames) {
        const bytes = name === fileName ? fileBytes : Buffer.from(`Remove ${name} explicitly before resubmission.`);
        firstForm.append('files', new Blob([new Uint8Array(bytes)], { type: 'text/plain' }), name);
      }
      const first = await page.request.post(`${endpoint}/submit`, { multipart: firstForm });
      expect(first.status(), await first.text()).toBe(200);
      const firstResult = await first.json();
      const submissionId = firstResult.submission_id;
      expect(JSON.parse(readS3Rows<{ answers_json: string }>('SELECT answers_json FROM submissions WHERE id=?', [submissionId])[0].answers_json).answers).toEqual(oldPayload.answers);
      await loginTeacher(teacher, fixture);
      const reviewResponse = await teacher.request.get(`/api/submissions/${submissionId}/review`);
      expect(reviewResponse.status()).toBe(200);
      const review = await reviewResponse.json();
      const graded = await teacher.request.post(`/api/submissions/${submissionId}/grade`, { data: {
        score: 49, feedback_md: '旧轮次：请补充接口协作说明。',
        expected_review_revision: review.expected_review_revision,
        expected_assignment_revision: review.expected_assignment_revision,
      } });
      expect(graded.status(), await graded.text()).toBe(200);
      const returned = await teacher.request.post(`${endpoint}/submissions/withdraw`, { data: {
        submission_ids: [submissionId], reason: '旧格式真实退回回归：保留答案与原附件', extension_minutes: 60,
      } });
      expect(returned.status(), await returned.text()).toBe(200);
      expect((await returned.json()).updated_count).toBe(1);

      const current = await context.newPage();
      observe(current);
      const loadDraft = current.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft`
        && response.request().method() === 'GET');
      const opened = await current.goto(route);
      expect(opened?.status()).toBe(200);
      if (kind === 'exam') {
        await expect(current.locator('.readonly-answer')).toContainText(original);
        await current.locator('#resubmitExamBtn').click();
      }
      const draftResponse = await loadDraft;
      expect(draftResponse.status()).toBe(200);
      const restored = await draftResponse.json();
      expect(restored.exists).toBe(true);
      expect(restored.submission_version).not.toBe('unsubmitted');
      expect(restored.answers_json).toContain(original);
      await expect(answer(current)).toHaveValue(original);
      await expect(files(current)).toContainText(fileName);
      if (kind === 'exam') {
        const removedId = restored.files.find((file: any) => file.file_name === removedFileName)?.id;
        expect(removedId).toBeTruthy();
        const deletionPath = `${endpoint}/draft-files/${removedId}`;
        const removal = () => files(current).locator('.file-chip').filter({ hasText: removedFileName }).locator('[data-remove-upload]');
        await expect(removal()).toBeVisible();
        await current.route(`**${deletionPath}*`, intercepted => intercepted.request().method() === 'DELETE'
          ? intercepted.fulfill({ status: 502, contentType: 'application/json', body: JSON.stringify({ detail: '合成附件删除暂时失败' }) })
          : intercepted.continue(), { times: 1 });
        const waitDelete = () => current.waitForResponse(response => new URL(response.url()).pathname === deletionPath
          && response.request().method() === 'DELETE');
        const rejectedDelete = waitDelete();
        await removal().click();
        expect((await rejectedDelete).status()).toBe(502);
        // A failed remote mutation must restore the chip for an explicit retry.
        await expect(removal()).toBeVisible();
        const acceptedDelete = waitDelete();
        await removal().click();
        const deletion = await acceptedDelete;
        expect(deletion.status()).toBe(200);
        expect(new URL(deletion.request().url()).searchParams.get('expected_submission_version')).toBe(restored.submission_version);
        await expect(removal()).toHaveCount(0);
        const lostId = restored.files.find((file: any) => file.file_name === lostResponseFileName)?.id;
        expect(lostId).toBeTruthy();
        const lostPath = `${endpoint}/draft-files/${lostId}`;
        const lostChip = () => files(current).locator('.file-chip').filter({ hasText: lostResponseFileName });
        await current.route(`**${lostPath}*`, async intercepted => {
          if (intercepted.request().method() !== 'DELETE') return intercepted.continue();
          // The service commits the real DELETE, but its response is lost.
          // A later authoritative draft must converge without a forced reload.
          const remote = await intercepted.fetch();
          expect(remote.status()).toBe(200);
          await intercepted.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '合成删除已完成但响应丢失' }) });
        }, { times: 1 });
        const responseLost = current.waitForResponse(response => new URL(response.url()).pathname === lostPath
          && response.request().method() === 'DELETE');
        await lostChip().locator('[data-remove-upload]').click();
        expect((await responseLost).status()).toBe(503);
        await expect(lostChip()).toBeVisible();
        const reconciled = waitMutation(current, 'draft');
        const continuedAnswer = `${original}\n删除响应恢复后继续作答。`;
        await answer(current).fill(continuedAnswer);
        const reconciledResponse = await reconciled;
        expect(reconciledResponse.status()).toBe(200);
        expect((await reconciledResponse.json()).files.map((file: any) => file.file_name)).toEqual([fileName]);
        await expect(lostChip()).toHaveCount(0);
        const reloadedDraft = current.waitForResponse(response => new URL(response.url()).pathname === `${endpoint}/draft`
          && response.request().method() === 'GET');
        expect((await current.reload())?.status()).toBe(200);
        await current.locator('#resubmitExamBtn').click();
        const reloaded = await (await reloadedDraft).json();
        expect(reloaded.files.map((file: any) => file.file_name)).toEqual([fileName]);
        await expect(answer(current)).toHaveValue(continuedAnswer);
        await expect(files(current)).toContainText(fileName);
        await expect(removal()).toHaveCount(0);
      }
      for (const width of [1440, 390]) {
        await current.setViewportSize({ width, height: 1000 });
        await answer(current).scrollIntoViewIfNeeded();
        const bounds = await answer(current).boundingBox();
        expect(bounds).not.toBeNull();
        // Allow the same two CSS-pixel border/rounding tolerance as the
        // existing eight-type responsive acceptance, never a hidden field.
        expect(bounds!.x).toBeGreaterThanOrEqual(-2);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 2);
        await current.screenshot({ path: testInfo.outputPath(`retake-${kind}-${width}.png`), fullPage: true });
      }
      await current.setViewportSize({ width: 1440, height: 1000 });

      // Exercise the old page's actual UI request. Exam stops at the draft
      // conflict; ordinary homework reaches the final submission version guard.
      await answer(page).fill(staleText);
      if (kind === 'exam') page.once('dialog', dialog => dialog.accept());
      const rejected = waitMutation(page, kind === 'exam' ? 'draft' : 'submit');
      await (kind === 'exam' ? page.locator('#topbarSubmitBtn') : page.getByTestId('p03-submit-assignment')).click();
      if (kind === 'homework') await page.getByRole('dialog').getByRole('button', { name: '确认提交', exact: true }).click();
      expect((await rejected).status()).toBe(409);
      await expect(answer(page)).toHaveValue(staleText);
      expect(readS3Rows<{ answers_json: string }>('SELECT answers_json FROM submissions WHERE id=?', [submissionId])[0].answers_json).toContain(original);

      await answer(current).fill(revised);
      if (kind === 'exam') current.once('dialog', dialog => dialog.accept());
      const completed = waitMutation(current, 'submit');
      await (kind === 'exam' ? current.locator('#topbarSubmitBtn') : current.getByTestId('p03-submit-assignment')).click();
      if (kind === 'homework') await current.getByRole('dialog').getByRole('button', { name: '确认提交', exact: true }).click();
      const completedResponse = await completed;
      expect(completedResponse.status(), await completedResponse.text()).toBe(200);
      expect((await completedResponse.json()).submission_id).toBe(submissionId);
      await expect(current.locator(kind === 'exam' ? '.readonly-answer' : '#submitted-answers-container')).toContainText(revised);
      const stored = readS3Rows<{ answers_json: string; resubmission_allowed: number; score: number | null }>(
        'SELECT answers_json,resubmission_allowed,score FROM submissions WHERE id=?', [submissionId])[0];
      expect(stored.answers_json).toContain(revised);
      expect(stored.answers_json).not.toContain(staleText);
      expect(stored.resubmission_allowed).toBe(0);
      expect(stored.score).toBeNull();
      const retained = readS3Rows<{ id: number; original_filename: string }>(
        'SELECT id,original_filename FROM submission_files WHERE submission_id=?', [submissionId]);
      expect(retained).toHaveLength(1);
      expect(retained[0].original_filename).toBe(fileName);
      const download = await current.request.get(`/submissions/download/${retained[0].id}`);
      expect(download.status()).toBe(200);
      expect(await download.body()).toEqual(fileBytes);
      expect(pageErrors).toEqual([]);
      await current.close();
    } finally {
      await teacherContext.close();
    }
  });
}
