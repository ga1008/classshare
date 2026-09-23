import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { loginTeacher } from '../fixtures/p03';
import { readS3Fixture } from '../fixtures/lq-s3';

async function prepare(page: Page, appearance: 'light' | 'dark' = 'dark') {
  const fixture = readS3Fixture();
  const health = await page.request.get('/api/internal/health');
  expect(health.status()).toBe(200);
  expect((await health.json()).database_path).toBe(fixture.databasePath);
  await loginTeacher(page, fixture);
  const pref = (await (await page.request.get('/api/profile/ui-preferences')).json()).preferences;
  const changed = await page.request.patch('/api/profile/ui-preferences', {
    headers: { 'X-UI-Preferences-Context': pref.context_token },
    data: { backdrop: 'scene', appearance, version: pref.version },
  });
  expect(changed.status()).toBe(200);
  return fixture;
}

async function expectMaterial(page: Page, selector: string) {
  const panel = page.locator(selector).first();
  await expect(panel).toBeVisible();
  const material = await panel.evaluate(node => {
    const css = getComputedStyle(node);
    return { fill: css.backgroundColor, image: css.backgroundImage, filter: css.backdropFilter };
  });
  expect(material.fill !== 'rgba(0, 0, 0, 0)' || material.image.includes('gradient'), JSON.stringify(material)).toBe(true);
  expect(material.image !== 'none' || material.filter !== 'none', JSON.stringify(material)).toBe(true);
}

async function expectContrast(page: Page, selector: string) {
  const scan = await new AxeBuilder({ page }).include(selector).withRules(['color-contrast', 'nested-interactive']).analyze();
  expect(scan.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) }))).toEqual([]);
}

test('semester selection and management are separate keyboard targets with retained focus', async ({ page }) => {
  await prepare(page);
  await page.goto('/manage/teaching/semesters');
  const select = page.locator('#semesterList [data-action="select"]').last();
  await expect(select).toBeVisible();
  await select.focus();
  await select.press('Enter');
  await expect(select).toHaveAttribute('aria-pressed', 'true');
  await expect(select).toBeFocused();
  await expect(page.locator('#semesterList [role="button"] button')).toHaveCount(0);
  const editable = page.locator('#semesterList [data-action="edit"]').first();
  await editable.press('Enter');
  await expect(page.locator('#semesterModalBackdrop')).toHaveClass(/is-open/);
  await page.locator('#semesterModalCancelBtn').click();
  await expect(editable).toBeFocused();
  await expectContrast(page, '#semesterList');
});

for (const width of [390, 1440]) for (const appearance of ['light', 'dark'] as const) {
  test(`classroom ${width} ${appearance} has readable material and one task dialog`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await prepare(page, appearance);
    await page.goto(`/classroom/${fixture.classOfferingId}`);
    await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
    await expectMaterial(page, '.cw-topbar');
    await expectMaterial(page, '#timeline-panel');
    await expectMaterial(page, '#assignment-panel');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    const opener = page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ });
    await opener.click();
    const dialog = page.getByRole('dialog', { name: '全部课堂任务' });
    await expect(dialog).toBeVisible();
    await expectMaterial(page, '.cw-dialog');
    await expect(page.locator('[data-cw-source="tasks"]')).toHaveCount(1);
    await expectContrast(page, '.cw-dialog');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(opener).toBeFocused();
    await expectContrast(page, '#timeline-panel');
    await expect(page.locator('.classroom-activity-tab.is-active')).toHaveCSS('background-image', 'none');
    await expectContrast(page, '.classroom-activity-tabs');
    await page.screenshot({ path: `.codex-temp/glass-teacher-20260923/classroom-${width}-${appearance}.png`, fullPage: false });
  });
}

for (const key of ['lesson-plan-editor', 'assessment-plan-editor', 'evaluation-editor', 'exam-editor']) {
  test(`${key} shares the backdrop and editor material`, async ({ page }) => {
    const fixture = await prepare(page);
    const routes = (fixture as typeof fixture & { lqCaptureRoutes: { teacher: [string, string][] } }).lqCaptureRoutes.teacher;
    const route = key === 'exam-editor' ? `/exam/${fixture.s3.authoringPaperId}/edit` : routes.find(([name]) => name === key)?.[1];
    expect(route).toBeTruthy();
    await page.goto(route!);
    if (key === 'exam-editor') await expect(page.locator('#exam-save-conflict')).toBeHidden();
    await expect(page.locator('[data-lq-page-backdrop]')).toHaveCount(1);
    await expectMaterial(page, '[data-lq-material="chrome"]');
    await expectContrast(page, key === 'exam-editor' ? '.editor-header' : '.lp-editor__header');
    if (key === 'evaluation-editor') {
      const opener = page.locator('#te-analysis-rewrite');
      await opener.click();
      await expectMaterial(page, '.te-ai-modal');
      await expect(page.locator('#te-ai-rewrite-prompt')).toBeFocused();
      await page.locator('#te-ai-rewrite-prompt').fill('保留尚未发送的编辑建议');
      await expectContrast(page, '.te-ai-modal');
      await page.keyboard.press('Escape');
      await expect(page.locator('#te-ai-rewrite-modal')).toBeHidden();
      await expect(opener).toBeFocused();
      await opener.click();
      await expect(page.locator('#te-ai-rewrite-prompt')).toHaveValue('保留尚未发送的编辑建议');
      await page.locator('#te-ai-rewrite-cancel').click();
      await expect(opener).toBeFocused();
      let releaseRewrite!: () => void;
      const holdRewrite = new Promise<void>(resolve => { releaseRewrite = resolve; });
      await page.route('**/api/teacher-evaluations/*/rewrite-analysis', async route => {
        await holdRewrite;
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ analysis: '合成测试：重写完成后仍可继续编辑。' }) });
      });
      await opener.click();
      const requested = page.waitForRequest('**/api/teacher-evaluations/*/rewrite-analysis');
      await page.locator('#te-ai-rewrite-confirm').click();
      await requested;
      await expect(page.locator('#te-ai-rewrite-cancel')).toBeDisabled();
      await page.keyboard.press('Escape');
      await expect(page.locator('#te-ai-rewrite-modal')).toHaveAttribute('data-lq-layer-state', 'open');
      releaseRewrite();
      await expect(page.locator('#te-ai-rewrite-modal')).toBeHidden();
      await expect(opener).toBeFocused();
      await expect(page.locator('#te-analysis')).toHaveValue('合成测试：重写完成后仍可继续编辑。');
    }
    await page.screenshot({ path: `.codex-temp/glass-teacher-20260923/${key}-dark.png`, fullPage: false });
    await page.setViewportSize({ width: 390, height: 900 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  });
}

for (const width of [390, 1440]) for (const kind of ['assignment', 'submission'] as const) {
  test(`teacher ${kind} ${width} preserves readable grading surfaces and menus`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const fixture = await prepare(page);
    const id = kind === 'assignment' ? fixture.teacherReviewAssignmentId : fixture.teacherReviewSubmissionId;
    await page.goto(`/${kind}/${id}`);
    await expectMaterial(page, 'header[data-lq-material]');
    await expectMaterial(page, '.card[data-lq-material]');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    if (kind === 'assignment') {
      await page.getByLabel('打开筛选菜单', { exact: true }).click();
      await expectMaterial(page, '.assignment-more-dropdown[open] .assignment-more-menu');
      await page.getByRole('menuitem', { name: '只看待批改提交', exact: true }).click();
      await expect(page.locator('.assignment-more-dropdown[open]')).toHaveCount(0);
      await expectContrast(page, '#submission-section');
    } else {
      await expect(page.getByTestId('p03-submission-score-input')).toBeVisible();
      await expectContrast(page, '#submission-grading-card');
    }
    await page.screenshot({ path: `.codex-temp/glass-teacher-20260923/${kind}-${width}-dark.png`, fullPage: false });
  });
}
