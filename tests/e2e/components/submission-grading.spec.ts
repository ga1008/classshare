import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const controller = fs.readFileSync('static/js/submission_grading.js', 'utf8');
const template = fs.readFileSync('templates/submission_detail.html', 'utf8');
const globalCss = fs.readFileSync('static/css/tailwind-app.css', 'utf8');
const pageCss = fs.readFileSync('static/css/submission_detail.css', 'utf8');
// The conflict panel is the production HTML, so missing recovery controls fail here.
const panel = template.slice(template.indexOf('    <div class="status-card warning mb-md" id="grade-conflict"'),
  template.indexOf('    {% if submission.resubmission_allowed %}', template.indexOf('<!-- Grading Section')));
const reviewActions = [
  template.match(/^    window\.uploadSubmissionAttachments = async function[^]*?^    };/m)?.[0],
  template.match(/^    async function deleteSubmissionFile\([^]*?^    }/m)?.[0],
  template.match(/^    function scheduleSubmissionReload\([^]*?^    }/m)?.[0],
  template.match(/^    window\.aiRegrade = async function[^]*?^    };/m)?.[0],
].join('\n');

type GradeResponse = { status: number; detail?: string; abort?: boolean };
async function mount(page: Page, responses: GradeResponse[] = [{ status: 200 }], options: { returnedAfterReload?: boolean; delayed?: boolean; missingTokens?: boolean } = {}) {
  const posts: any[] = [];
  const reviewMutationRequests: string[] = [];
  let pageLoads = 0;
  let release: (() => void) | undefined;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('http://grading.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/submission_grading.js') return route.fulfill({ contentType: 'text/javascript', body: controller });
    if (path === '/styles.css') return route.fulfill({ contentType: 'text/css', body: `${globalCss}\n${pageCss}` });
    if (path === '/api/submissions/7/grade') {
      posts.push(route.request().postDataJSON());
      if (options.delayed && posts.length === 1) await pending;
      const response = responses[Math.min(posts.length - 1, responses.length - 1)];
      if (response.abort) return route.abort('connectionreset');
      return route.fulfill({ status: response.status, json: response.status === 200
        ? { status: 'success', graded_submission_id: 7 } : { detail: response.detail || '服务器拒绝本次评分' } });
    }
    if (path.startsWith('/api/')) {
      reviewMutationRequests.push(path);
      return route.fulfill({ json: { status: 'success' } });
    }
    if (path === '/assignment/12') return route.fulfill({ contentType: 'text/html', body: '<h1>作业</h1>' });
    pageLoads += 1;
    const refreshed = pageLoads > 1;
    const score = refreshed ? 94.5 : 70;
    const returned = refreshed && options.returnedAfterReload;
    const config = {
      submissionId: 7, assignmentId: 12, teacherId: 10,
      expectedReviewRevision: options.missingTokens ? '' : `answer-v${refreshed ? 2 : 1}`,
      expectedAssignmentRevision: `assignment-v${refreshed ? 2 : 1}`,
      initialScore: score, initialFeedback: refreshed ? '其他教师的新评语' : '已有评语',
      requirements: '最新作业要求', rubric: '<img src=x onerror=alert(1)> 最新评分标准',
    };
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <link rel="stylesheet" href="/styles.css">
      ${panel}<div id="submission-grading-card">${returned ? '<p>该提交已撤回，等待重交。</p>' : `
        <input id="grade-score" type="number" min="0" max="100" step="any" value="${score}">
        <textarea id="grade-feedback">已有评语</textarea>
        <button id="insert-feedback-template-btn">插入模板</button>
        <button id="grade-save">保存评分</button><button id="ai" onclick="aiRegrade()">AI 辅助批改</button>
        <button id="grade-cancel">取消</button>
        <p id="grade-save-status" role="status" hidden></p>`}</div>
      <script type="module">import {setupSubmissionGrading} from '/submission_grading.js';
        window.toasts=[];window.gradeController=setupSubmissionGrading({...${JSON.stringify(config)},showToast:(...args)=>window.toasts.push(args)});
        const manualGrading=window.gradeController, submissionId=7, canManageSubmissionFiles=true;
        const teacherAttachmentUploadManager={hasFiles:()=>true,buildFormData:()=>new FormData()};
        const showToast=(...args)=>window.toasts.push(args);
        ${reviewActions}
        window.deleteSubmissionFile=deleteSubmissionFile;window.scheduleSubmissionReload=scheduleSubmissionReload;
      </script></html>` });
  });
  await page.goto('http://grading.test/submission/7');
  await page.waitForFunction(() => Boolean((window as any).gradeController));
  return { posts, reviewMutationRequests, release: () => release?.(), pageLoads: () => pageLoads };
}

for (const score of ['82.75', '0']) {
  test(`manual grade preserves ${score}, sends both reviewed versions and stays locked until navigation`, async ({ page }) => {
    const fixture = await mount(page, [{ status: 200 }], { delayed: true });
    await expect(page.locator('#grade-conflict')).toBeHidden();
    await page.locator('#grade-score').fill(score);
    await page.locator('#grade-feedback').fill('逐题反馈：保持原样');
    await page.evaluate(() => { void (window as any).gradeController.submitGrade(); void (window as any).gradeController.submitGrade(); });
    await expect.poll(() => fixture.posts.length).toBe(1);
    await expect(page.locator('#grade-save')).toBeDisabled();
    await expect(page.locator('#grade-feedback')).toBeDisabled();
    await expect(page.locator('#ai')).toBeDisabled();
    expect(fixture.posts[0]).toEqual({ score: Number(score), feedback_md: '逐题反馈：保持原样',
      expected_review_revision: 'answer-v1', expected_assignment_revision: 'assignment-v1' });
    fixture.release();
    await expect(page.locator('#grade-save-status')).toHaveText('评分已保存，正在返回作业。');
    await page.evaluate(() => (window as any).gradeController.submitGrade());
    expect(fixture.posts).toHaveLength(1);
    expect(await page.evaluate(() => (window as any).toasts)).toEqual([['评分已保存', 'success']]);
    await expect(page).toHaveURL('http://grading.test/assignment/12');
  });
}

test('empty, out-of-range and nonfinite scores never write; decimal has no step mismatch', async ({ page }) => {
  const { posts } = await mount(page);
  for (const value of ['', '-1', '101']) {
    await page.locator('#grade-score').fill(value);
    await page.locator('#grade-save').click();
    await expect(page.locator('#grade-save-status')).toContainText('请输入有效得分');
  }
  await page.locator('#grade-score').evaluate((node: HTMLInputElement) => { node.value = '1e309'; });
  await page.locator('#grade-save').click();
  expect(posts).toHaveLength(0);
  await page.locator('#grade-score').fill('82.75');
  expect(await page.locator('#grade-score').evaluate((node: HTMLInputElement) => node.validity.stepMismatch)).toBe(false);
  expect(template).toContain('max="100" step="any"');
});

for (const first of [{ status: 400, detail: '输入已拒绝' }, { status: 500 }, { status: 0, abort: true }]) {
  test(`failure ${first.abort ? 'network' : first.status} preserves inputs and restores retry controls`, async ({ page }) => {
    const { posts } = await mount(page, [first, { status: 200 }]);
    await page.locator('#grade-score').fill('65.125');
    await page.locator('#grade-feedback').fill('失败后保留');
    await page.locator('#grade-save').click();
    await expect(page.locator('#grade-save-status')).not.toHaveText('正在保存评分…');
    await expect(page.locator('#grade-save')).toBeEnabled();
    await expect(page.locator('#ai')).toBeEnabled();
    await expect(page.locator('#grade-score')).toHaveValue('65.125');
    await expect(page.locator('#grade-feedback')).toHaveValue('失败后保留');
    await page.locator('#grade-save').click();
    await expect.poll(() => posts.length).toBe(2);
    expect(posts[1]).toEqual(posts[0]);
  });
}

test('409 retains draft, refresh shows latest differences, and explicit recheck is required before new versions are sent', async ({ page }) => {
  const fixture = await mount(page, [{ status: 409, detail: '答卷或成绩已更新' }, { status: 200 }]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#grade-score').fill('81.25');
  await page.locator('#grade-feedback').fill('我正在编写的反馈');
  await page.locator('#grade-save').click();
  await expect(page.locator('#grade-conflict')).toBeVisible();
  await expect(page.locator('#grade-save')).toBeDisabled();
  await expect(page.locator('#grade-score')).toHaveValue('81.25');
  await expect(page.locator('#grade-feedback')).toHaveValue('我正在编写的反馈');
  await page.evaluate(() => (window as any).gradeController.submitGrade());
  expect(fixture.posts).toHaveLength(1);
  await page.locator('#grade-conflict-refresh').click();
  await expect(page.locator('#grade-conflict-summary')).toContainText('70 → 94.5');
  await expect(page.locator('#grade-conflict-summary')).toContainText('作业要求或评分标准版本已变化');
  await expect(page.locator('#grade-feedback')).toHaveValue('我正在编写的反馈');
  await expect(page.locator('#grade-save')).toBeDisabled();
  await page.locator('#grade-conflict-latest summary').click();
  await expect(page.locator('#grade-conflict-server-feedback')).toHaveText('其他教师的新评语');
  await expect(page.locator('#grade-conflict-rubric')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#grade-conflict img')).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('#grade-conflict-confirm').click();
  await page.locator('#grade-save').click();
  await expect.poll(() => fixture.posts.length).toBe(2);
  expect(fixture.posts[1]).toEqual({ score: 81.25, feedback_md: '我正在编写的反馈',
    expected_review_revision: 'answer-v2', expected_assignment_revision: 'assignment-v2' });
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('lanshare:manual-grade:10:7'))).toBeNull();
});

test('reloaded withdrawn submission retains a copyable draft without restoring forbidden grading controls', async ({ page }) => {
  const { posts } = await mount(page, [{ status: 409 }], { returnedAfterReload: true });
  await page.locator('#grade-score').fill('0');
  await page.locator('#grade-feedback').fill('等待重交也不能丢失');
  await page.locator('#grade-save').click();
  await page.locator('#grade-conflict-refresh').click();
  await expect(page.locator('#grade-conflict-message')).toContainText('暂不可评分');
  await expect(page.locator('#grade-save')).toHaveCount(0);
  await expect(page.locator('#grade-conflict-confirm')).toBeHidden();
  await page.locator('#grade-conflict-draft summary').click();
  await expect(page.locator('#grade-conflict-draft-content')).toContainText('本地原始分：0');
  await expect(page.locator('#grade-conflict-draft-content')).toContainText('等待重交也不能丢失');
  expect(posts).toHaveLength(1);
});

test('blocked draft storage prevents a destructive conflict reload', async ({ page }) => {
  const fixture = await mount(page, [{ status: 409 }]);
  await page.locator('#grade-feedback').fill('仍在页面中');
  await page.locator('#grade-save').click();
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('storage blocked'); }; });
  await page.locator('#grade-conflict-refresh').click();
  await expect(page.locator('#grade-conflict-summary')).toContainText('未重新加载');
  await expect(page.locator('#grade-feedback')).toHaveValue('仍在页面中');
  expect(fixture.pageLoads()).toBe(1);
});

test('missing version fails closed instead of using the legacy unguarded grading contract', async ({ page }) => {
  const { posts } = await mount(page, [{ status: 200 }], { missingTokens: true });
  await page.locator('#grade-save').click();
  await expect(page.locator('#grade-conflict-message')).toContainText('缺少答卷或作业版本');
  await expect(page.locator('#grade-save')).toBeDisabled();
  expect(posts).toHaveLength(0);
});

test('a conflict blocks AI, attachment writes/deletes, cancel, and already queued reloads without losing the draft', async ({ page }) => {
  const fixture = await mount(page, [{ status: 409 }]);
  await page.clock.install();
  await page.locator('#grade-feedback').fill('保护尚未持久化的草稿');
  await page.evaluate(() => (window as any).scheduleSubmissionReload(2000));
  await page.locator('#grade-save').click();
  await expect(page.locator('#grade-conflict')).toBeVisible();
  await page.evaluate(async () => {
    await (window as any).aiRegrade();
    await (window as any).uploadSubmissionAttachments(false);
    await (window as any).uploadSubmissionAttachments(true);
    await (window as any).deleteSubmissionFile(1, '附件');
  });
  await page.locator('#grade-cancel').click();
  await page.clock.runFor(2500);
  expect(fixture.reviewMutationRequests).toEqual([]);
  expect(fixture.pageLoads()).toBe(1);
  await expect(page.locator('#grade-feedback')).toHaveValue('保护尚未持久化的草稿');
  await expect(page.locator('#grade-save-status')).toContainText('评分草稿尚未保存');
});

test('a restored and confirmed draft also blocks auto-reloading mutations until save or explicit discard', async ({ page }) => {
  const fixture = await mount(page, [{ status: 409 }]);
  await page.locator('#grade-feedback').fill('核对后仍需保留');
  await page.locator('#grade-save').click();
  await page.locator('#grade-conflict-refresh').click();
  await page.locator('#grade-conflict-confirm').click();
  await page.evaluate(async () => {
    await (window as any).aiRegrade();
    await (window as any).uploadSubmissionAttachments(true);
  });
  expect(fixture.reviewMutationRequests).toEqual([]);
  await expect(page.locator('#grade-feedback')).toHaveValue('核对后仍需保留');
  await expect(page.locator('#grade-save')).toBeEnabled();
  await expect(page.locator('#grade-conflict-message')).toContainText('已核对但尚未保存');
});
