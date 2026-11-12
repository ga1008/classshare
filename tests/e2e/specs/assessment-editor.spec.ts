import { test, expect, readS3Fixture, readS3Rows } from '../fixtures/lq-s3';
import { loginTeacher, loginStudent } from '../fixtures/p03';

const types = ['radio', 'checkbox', 'boolean', 'text', 'textarea', 'analysis', 'code', 'comprehensive'];
function paper() {
  return { grading: { total_score: 80, description: '按每题参考答案与关键步骤评分。', style: 'balanced' }, pages: [{name: '八种题型', questions: types.map((type, index) => ({
    id: `new${index + 1}`, type, points: 10, text: type === 'text' ? '分别填写 **协议** ' + '{{2}}' + ' 和 **端口** ' + '{{7}}' : `**${type}** 题干，支持 Markdown`,
    answer: type === 'checkbox' ? ['A. 一', 'B. 二'] : type === 'text' ? ['参考协议ONLY_TEACHER', '参考端口ONLY_TEACHER'] : type === 'boolean' ? '正确' : type === 'radio' ? 'A. 一' : '说明步骤和结论。',
    grading_guidance: '完整答案得10分。', deduction_points: '每漏要点扣5分。',
    ...(['radio', 'checkbox', 'boolean'].includes(type) ? {options: type === 'boolean' ? ['正确','错误'] : ['A. 一','B. 二','C. 三','D. 四','E. 五']} : {}),
    ...(type === 'text' ? {blanks: [{id:'2',answer:'参考协议ONLY_TEACHER'}, {id:'7',answer:'参考端口ONLY_TEACHER'}]} : {}),
    attachment_requirements: { enabled: false, required: false },
  }))}] };
}

test('eight types survive teacher save, student draft, failed submit, retry and teacher review', async ({ page, browser, baseURL }, testInfo) => {
  const fixture = readS3Fixture(), id = fixture.s3.examTakeAssignmentId;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    (window as any).assessmentLongTasks = [];
    if (PerformanceObserver.supportedEntryTypes.includes('longtask')) new PerformanceObserver(list => {
      (window as any).assessmentLongTasks.push(...list.getEntries().map(entry => entry.duration));
    }).observe({entryTypes:['longtask']});
  });
  await loginTeacher(page, fixture);
  const url = '/api/exam-papers/lq-s3-examTake';
  const before = (await (await page.request.get(url)).json()).paper;
  const write = await page.request.put(url, {data:{title:'八题型完整链路',questions:paper(),expected_revision:before.revision}});
  expect(write.status(), await write.text()).toBe(200);
  await page.goto('/exam/lq-s3-examTake/edit');
  await expect(page.locator('.q-type-select')).toHaveCount(8);
  expect(await page.locator('.q-type-select').evaluateAll(nodes => nodes.map(node => (node as HTMLSelectElement).value))).toEqual(types);
  await page.screenshot({path:testInfo.outputPath('teacher-eight-types.png'),fullPage:true});
  await loginStudent(page, fixture);
  const loaded = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${id}/draft`) && response.request().method() === 'GET');
  await page.goto(`/exam/take/${id}`);
  expect((await loaded).status()).toBe(200);
  expect(await page.content()).not.toContain('ONLY_TEACHER');
  await expect(page.locator('#q-new1 input[type=radio]')).toHaveCount(5);
  await expect(page.locator('#q-new3 input[type=radio]')).toHaveCount(2);
  await expect(page.locator('#q-new3')).toContainText('判断题');
  await expect(page.locator('#q-new4 [data-blank-index]')).toHaveCount(2);
  await page.locator('#q-new1 input[type=radio]').first().check();
  await page.locator('#q-new2 input[type=checkbox]').first().check();
  await page.locator('#q-new3 input[type=radio]').first().check();
  const blanks = page.locator('#q-new4 [data-blank-index]');
  await blanks.nth(1).fill('443');
  await expect(blanks.nth(0)).toHaveValue('');
  await blanks.nth(0).fill('HTTPS');
  for (const index of [5,6,7,8]) await page.locator(`[data-text-input="new${index}"]`).fill(`1. 第一步\n2. 第二步 ${index}`);
  const draft = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${id}/draft`) && response.request().method() === 'POST', {timeout:20000});
  expect((await draft).status()).toBe(200);
  // Clear only this context's local cache so the next view proves server recovery.
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.locator('#q-new4 [data-blank-index]').nth(0)).toHaveValue('HTTPS');
  await expect(page.locator('#q-new4 [data-blank-index]').nth(1)).toHaveValue('443');
  await page.locator('[data-text-input="new5"]').fill(Array.from({length:30},(_,i)=>`第 ${i+1} 行`).join('\n'));
  const geometry = await page.locator('[data-text-input="new5"]').evaluate(node => {
    const area = node as HTMLTextAreaElement, style = getComputedStyle(area);
    return {height:area.clientHeight,scroll:area.scrollHeight,line:parseFloat(style.lineHeight),overflow:style.overflowY};
  });
  expect(geometry.height).toBeGreaterThan(geometry.line * 14);
  expect(geometry.scroll).toBeGreaterThan(geometry.height);
  expect(geometry.overflow).toBe('auto');
  for (const width of [390, 1440]) {
    await page.setViewportSize({width,height:900});
    await page.screenshot({path:testInfo.outputPath(`student-${width}.png`),fullPage:true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(2);
  }
  await testInfo.attach('editor-runtime-metrics.json', {contentType:'application/json',body:JSON.stringify(await page.evaluate(()=>({
    longTasks: (window as any).assessmentLongTasks,
    resources: performance.getEntriesByType('resource').filter(entry=>entry.name.includes('assessment_') || entry.name.includes('assessment-')).map(entry=>({name:entry.name,bytes:(entry as PerformanceResourceTiming).decodedBodySize,duration:entry.duration})),
    fields: document.querySelectorAll('textarea[data-text-input]').length,
    blurHosts: Array.from(document.querySelectorAll('*')).filter(node=>getComputedStyle(node).backdropFilter !== 'none').length,
  })),null,2)});
  await page.route(`**/api/assignments/${id}/submit`, route => route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'合成提交暂时失败'})}), {times:1});
  page.on('dialog', dialog => dialog.accept());
  const rejected = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${id}/submit`));
  await page.locator('#topbarSubmitBtn').click();
  expect((await rejected).status()).toBe(503);
  await expect(page.locator('#q-new4 [data-blank-index]').nth(1)).toHaveValue('443');
  const failureNotice = page.locator('.lq-toast').filter({hasText:'合成提交暂时失败'});
  for (const notice of await failureNotice.all()) await notice.getByRole('button',{name:'关闭通知'}).click();
  await expect(page.locator('#topbarSubmitBtn')).toBeEnabled();
  const submitted = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${id}/submit`));
  await page.locator('#topbarSubmitBtn').click();
  const accepted = await submitted;
  expect(accepted.status(), await accepted.text()).toBe(200);
  const result = await accepted.json();
  const rows = readS3Rows<{answers_json:string}>('SELECT answers_json FROM submissions WHERE id=?',[result.submission_id]);
  expect(JSON.parse(rows[0].answers_json).answers.find((q:any)=>q.question_id==='new4')).toMatchObject({answer:['HTTPS','443'],blank_ids:['2','7']});
  await page.reload();
  await expect(page.locator('#q-new4')).toContainText('第 2 空：HTTPS');
  await expect(page.locator('#q-new4')).toContainText('第 7 空：443');
  const teacherContext = await browser.newContext({baseURL});
  try {
    await teacherContext.route('**/*', route => {
      const target = new URL(route.request().url());
      return ['127.0.0.1', 'localhost'].includes(target.hostname) || ['data:', 'blob:'].includes(target.protocol) ? route.continue() : route.abort();
    });
    const teacher = await teacherContext.newPage();
    await loginTeacher(teacher,fixture);
    await teacher.goto(`/submission/${result.submission_id}`);
    await expect(teacher.locator('[data-question-id="new4"]')).toContainText('第 2 空：HTTPS');
    await expect(teacher.locator('[data-question-id="new4"]')).toContainText('第 7 空：443');
  } finally {await teacherContext.close();}
  expect(errors).toEqual([]);
});

test('ordinary homework retains Markdown editing through server draft restoration', async ({page}) => {
  const fixture=readS3Fixture(), id=fixture.s3.draftAssignmentId;
  await loginStudent(page,fixture);
  // A pending learning snapshot has no numeric cultivation score yet. It must
  // not turn the common post-login page into a 500 before homework opens.
  const dashboard = await page.goto('/dashboard');
  expect(dashboard?.status()).toBe(200);
  await expect(page.locator('[data-dashboard-root]')).toBeVisible();
  await page.goto(`/assignment/${id}`);
  const answer=page.locator('.answer-textarea').first();
  await expect(answer).toBeVisible();
  await answer.fill('1. 第一项');
  await answer.press('End');
  await answer.press('Enter');
  await expect(answer).toHaveValue('1. 第一项\n2. ');
  await answer.pressSequentially('第二项');
  const saved=page.waitForResponse(response => response.url().endsWith(`/api/assignments/${id}/draft`) && response.request().method()==='POST',{timeout:20000});
  expect((await saved).status()).toBe(200);
  await page.evaluate(()=>localStorage.clear());
  await page.reload();
  await expect(page.locator('.answer-textarea').first()).toHaveValue('1. 第一项\n2. 第二项');
});

test('imported question identifiers stay inert in student markup', async ({page}) => {
  const fixture = readS3Fixture(), id = fixture.s3.examDeadlineAssignmentId;
  await loginTeacher(page,fixture);
  const url='/api/exam-papers/lq-s3-examDeadline';
  const before=(await (await page.request.get(url)).json()).paper;
  const malicious='x"><img src=x onerror="window.questionIdInjected=1">';
  const questions=paper();
  questions.pages[0].questions=questions.pages[0].questions.slice(0,1);
  questions.pages[0].questions[0].id=malicious;
  questions.grading.total_score=10;
  const saved=await page.request.put(url,{data:{title:'题号安全回归',questions,expected_revision:before.revision}});
  expect(saved.status(),await saved.text()).toBe(200);
  await loginStudent(page,fixture);
  await page.goto(`/exam/take/${id}`);
  await expect(page.locator('.question-card input[type=radio]')).toHaveCount(5);
  await expect(page.locator('.question-card')).toHaveAttribute('id',`q-${malicious}`);
  await expect(page.locator('.question-card [onerror]')).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).questionIdInjected)).toBeUndefined();
  await page.locator('.question-card input[type=radio]').first().check();
  await expect(page.locator('.question-card input[type=radio]').first()).toBeChecked();
});
