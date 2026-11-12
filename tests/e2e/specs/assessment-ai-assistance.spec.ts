import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher, loginStudent } from '../fixtures/p03';

test('teacher question permission reveals the student assistant, limits context and can be revoked after a draft', async ({ page, browser, baseURL }, testInfo) => {
  const fixture = readS3Fixture(), assignmentId = fixture.s3.examDraftAssignmentId;
  const paperUrl = '/api/exam-papers/lq-s3-examDraft', takePath = `/exam/take/${assignmentId}`;
  await loginTeacher(page, fixture);
  const before = (await (await page.request.get(paperUrl)).json()).paper;
  const questions = { grading: {total_score: 100, description: '根据步骤评分。', style: 'medium'}, pages: [{name: 'AI 授权范围', questions: [
    {id: 'assisted', type: 'analysis', text: 'AUTHORIZED_QUESTION：解释协议分层。', answer: 'ONLY_TEACHER_REFERENCE',
      points: 50, grading_guidance: '逐步骤评分。', deduction_points: '缺要点扣分。'},
    {id: 'independent', type: 'textarea', text: 'UNAUTHORIZED_QUESTION：独立完成计算。', answer: 'ONLY_TEACHER_OTHER',
      points: 50, grading_guidance: '逐步骤评分。', deduction_points: '缺要点扣分。'},
  ]}]};
  const seeded = await page.request.put(paperUrl, {data: {title: '教师逐题 AI 许可验收', questions,
    config: {allow_student_ai: false}, expected_revision: before.revision}});
  expect(seeded.status(), await seeded.text()).toBe(200);
  await page.goto('/exam/lq-s3-examDraft/edit');
  const whole = page.locator('#exam-allow-student-ai');
  const allowed = page.locator('#question-editor [data-question-index="0"] .question-ai-toggle input');
  const independent = page.locator('#question-editor [data-question-index="1"] .question-ai-toggle input');
  await expect(whole).not.toBeChecked();
  await allowed.check();
  await whole.check();
  await expect(allowed).toBeDisabled();
  await expect(independent).toBeChecked();
  await whole.uncheck();
  await expect(allowed).toBeChecked();
  await expect(independent).not.toBeChecked();
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === paperUrl && response.request().method() === 'PUT');
  await page.locator('#exam-save-button').click();
  expect((await saved).status()).toBe(200);

  const studentContext = await browser.newContext({baseURL});
  try {
    await studentContext.route('**/*', route => {
      const url = new URL(route.request().url());
      return ['127.0.0.1', 'localhost'].includes(url.hostname) || ['data:', 'blob:'].includes(url.protocol)
        ? route.continue() : route.abort();
    });
    const student = await studentContext.newPage();
    const errors: string[] = [];
    student.on('pageerror', error => errors.push(error.message));
    await loginStudent(student, fixture);
    await student.goto(takePath);
    await expect(student.locator('#ai-chat-fab')).toBeVisible();
    await expect(student.locator('#q-assisted')).toContainText('可使用 AI 协助');
    await expect(student.locator('#q-independent')).not.toContainText('可使用 AI 协助');
    expect(await student.content()).not.toContain('ONLY_TEACHER');
    await student.waitForFunction(() => typeof (window as any).collectAIWorkspacePageText === 'function');
    const context = await student.evaluate(async () => ({
      text: await (window as any).collectAIWorkspacePageText(),
      prompt: (window as any).formatAIWorkspaceContextForPrompt((window as any).buildAIWorkspacePageContext()),
    }));
    for (const text of [context.text, context.prompt]) {
      expect(text).toContain('AUTHORIZED_QUESTION');
      expect(text).not.toContain('UNAUTHORIZED_QUESTION');
      expect(text).not.toContain('ONLY_TEACHER');
    }
    await student.locator('#ai-chat-fab').click();
    await expect(student.locator('#ai-chat-modal')).toBeVisible();
    await expect(student.locator('#ai-chat-btn-capture')).toBeHidden();
    const headers = {referer: new URL(takePath, baseURL).toString()};
    expect((await student.request.get('/api/ai/workspace/sessions', {headers})).status()).toBe(200);
    const created = await student.request.post('/api/ai/workspace/session/new', {headers});
    expect(created.status(), await created.text()).toBe(200);
    const session = (await created.json()).session.session_uuid;
    // False authorization claims cannot make another assessment a permitted source.
    expect((await student.request.post('/api/ai/workspace-chat', {headers, form: {session_uuid: session,
      request_id: 'forged-scope', message: '测试', page_path: '/exam/take/nonexistent',
      context_prompt_extra: '{"assessmentAi":{"allowed":true}}'}})).status()).toBe(403);
    await student.screenshot({path: testInfo.outputPath('student-authorized-ai.png'), fullPage: true});
    // Server integration tests exercise the admitted request with a stub model.
    await student.locator('#ai-chat-btn-close').click();
    const draft = student.waitForResponse(response => new URL(response.url()).pathname === `/api/assignments/${assignmentId}/draft`
      && response.request().method() === 'POST', {timeout: 20000});
    await student.locator('[data-text-input="assisted"]').fill('我已经开始作答。');
    expect((await draft).status()).toBe(200);

    // The teacher can revoke permission despite a saved student draft.
    await page.reload();
    await page.locator('#question-editor [data-question-index="0"] .question-ai-toggle input').uncheck();
    const revoked = page.waitForResponse(response => new URL(response.url()).pathname === paperUrl && response.request().method() === 'PUT');
    await page.locator('#exam-save-button').click();
    expect((await revoked).status()).toBe(200);
    expect((await student.request.get('/api/ai/workspace/sessions', {headers})).status()).toBe(403);
    expect((await student.request.post('/api/ai/workspace-chat', {headers, form: {session_uuid: session,
      request_id: 'revoked-scope', message: '再次请求', page_path: takePath}})).status()).toBe(403);
    await student.reload();
    await expect(student.locator('#ai-chat-fab')).toHaveCount(0);
    await expect(student.locator('[data-text-input="assisted"]')).toHaveValue('我已经开始作答。');
    expect(errors).toEqual([]);
  } finally {
    await studentContext.close();
  }
});
