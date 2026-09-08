import { expect, test, type Locator, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { expectHealthUsesRuntimeDb, loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

const evidenceDir = path.resolve('.codex-temp/p03-artifacts');
const reviewTitle = 'P03 QA 计算机网络原理 第2课课堂练习：互联网基础、网络命令与网络故障分析';

async function openTasks(page: Page) {
  await page.locator('#cw-tasks-preview').getByRole('button', { name: /全部任务/ }).click();
  const dialog = page.getByRole('dialog', { name: '全部课堂任务', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.locator('.cw-filterbar').getByLabel('任务状态').selectOption('all');
  return dialog;
}

async function expectNoHorizontalOverflow(surface: Locator) {
  const dimensions = await surface.evaluate(node => ({ client: node.clientWidth, scroll: node.scrollWidth }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
}

async function expectControlsInsideCard(card: Locator) {
  const outside = await card.evaluate(node => {
    const bounds = node.getBoundingClientRect();
    return Array.from(node.querySelectorAll('a, button, select, summary')).filter(control => {
      if (control.closest('details:not([open])') && control.tagName !== 'SUMMARY') return false;
      const box = control.getBoundingClientRect();
      return box.width > 0 && box.height > 0 && (box.left < bounds.left - 1 || box.right > bounds.right + 1);
    }).map(control => control.textContent?.trim() || control.tagName);
  });
  expect(outside).toEqual([]);
}

for (const viewport of [{ width: 1440, height: 980 }, { width: 390, height: 844 }]) {
  test.describe(`teacher task cards at ${viewport.width}px`, () => {
    test.use({ viewport, hasTouch: viewport.width < 600 });

    test('compact cards keep classification, group configuration and review navigation usable', async ({ page }) => {
      const fixture = readFixture();
      const errors: string[] = [];
      page.on('pageerror', error => errors.push(error.message));
      await loginTeacher(page, fixture);
      // All mutations below target the disposable P03 copy, never local or production data.
      await expectHealthUsesRuntimeDb(page, fixture);
      const endpoint = `/api/assignments/${fixture.teacherReviewAssignmentId}/assessment-kind`;
      const currentResponse = await page.request.get(endpoint);
      expect(currentResponse.status()).toBe(200);
      const current = await currentResponse.json();
      const normalized = await page.request.patch(endpoint, { data: {
        assessment_kind: 'homework', expected_version: current.assessment_kind_version,
      } });
      expect(normalized.status()).toBe(200);
      const updated = await page.request.put(`/api/assignments/${fixture.teacherReviewAssignmentId}`, { data: {
        title: reviewTitle,
        availability_mode: 'deadline',
        due_at: new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString(),
      } });
      expect(updated.status()).toBe(200);

      await page.goto(`/classroom/${fixture.classOfferingId}`);
      const dialog = await openTasks(page);
      await dialog.getByLabel('查找任务', { exact: true }).fill(reviewTitle);
      const card = dialog.locator(`[data-assignment-task-card][data-assignment-id="${fixture.teacherReviewAssignmentId}"]`);
      await expect(card).toBeVisible();
      await expect(card.locator('.assignment-card-title')).toHaveText(reviewTitle);
      const primary = card.locator('.assignment-card-primary-link');
      await expect(primary).toBeVisible();
      await expect(primary).toHaveText(/去批改|查看任务/);
      await expect(primary).toHaveAttribute('href', `/assignment/${fixture.teacherReviewAssignmentId}`);
      await expect(card.locator('.assignment-card-insights')).toContainText('待批改');
      await expect(card.locator('[data-assignment-clock-value]')).not.toHaveText('--:--:--');

      const classification = card.locator('details.assignment-card-classification');
      const summary = classification.locator('summary');
      await expect(summary).toContainText('调整分类');
      await expect(classification).not.toHaveAttribute('open', '');
      await expect(classification.getByLabel('设置任务分类', { exact: true })).toBeHidden();
      await expectNoHorizontalOverflow(dialog);
      await expectNoHorizontalOverflow(card);
      await expectControlsInsideCard(card);
      const cardBox = await card.boundingBox();
      expect(cardBox!.height).toBeLessThan(viewport.width < 600 ? 540 : 360);
      const dialogBox = await dialog.boundingBox();
      expect(dialogBox!.x).toBeGreaterThanOrEqual(0);
      expect(dialogBox!.x + dialogBox!.width).toBeLessThanOrEqual(viewport.width + 1);
      fs.mkdirSync(evidenceDir, { recursive: true });
      fs.writeFileSync(path.join(evidenceDir, `task-card-dimensions-${viewport.width}.json`), JSON.stringify(cardBox, null, 2));
      await card.scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(evidenceDir, `task-card-teacher-${viewport.width}.png`) });

      // The summary and form are interactive children of a navigable task card.
      await summary.focus();
      await summary.press('Enter');
      await expect(classification).toHaveAttribute('open', '');
      const select = classification.getByLabel('设置任务分类', { exact: true });
      await expect(select).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`/classroom/${fixture.classOfferingId}(?:\\?|$)`));
      await select.selectOption('midterm');
      const save = classification.getByRole('button', { name: '保存分类', exact: true });
      await expect(save).toBeEnabled();
      const savedResponse = page.waitForResponse(response => response.url().endsWith(endpoint) && response.request().method() === 'PATCH');
      await save.click();
      expect((await savedResponse).status()).toBe(200);
      await expect(card.locator('[data-assessment-kind-label]')).toHaveText('期中测验');
      await expect(classification.locator('[data-assessment-kind-note]')).toContainText('分类已保存');
      await expect(save).toBeDisabled();
      await expect(page).toHaveURL(new RegExp(`/classroom/${fixture.classOfferingId}(?:\\?|$)`));
      const expandedLayout = await card.evaluate(node => {
        const selectors = ['.assignment-card-footer', '.assignment-card-classification', '.ordinary-grade-kind-control'];
        return {
          stylesheets: Array.from(document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')).map(link => link.href),
          elements: selectors.map(selector => {
            const element = node.querySelector(selector)!;
            const style = getComputedStyle(element);
            return { selector, display: style.display, flexDirection: style.flexDirection, flexWrap: style.flexWrap,
              flexBasis: style.flexBasis, width: style.width, minWidth: style.minWidth,
              gridTemplateColumns: style.gridTemplateColumns, gridColumn: style.gridColumn,
              bounds: element.getBoundingClientRect().toJSON() };
          }),
        };
      });
      fs.writeFileSync(path.join(evidenceDir, `task-card-expanded-layout-${viewport.width}.json`), JSON.stringify(expandedLayout, null, 2));
      await expectNoHorizontalOverflow(card);
      await expectControlsInsideCard(card);
      await page.screenshot({ path: path.join(evidenceDir, `task-card-classification-${viewport.width}.png`) });
      const persisted = await page.request.get(endpoint);
      expect((await persisted.json()).assessment_kind).toBe('midterm');
      await summary.click();

      // Updating the existing control must also update the React collection filter.
      await dialog.locator('.cw-filterbar').getByLabel('任务分类').selectOption('homework');
      await expect(card).toBeHidden();
      await dialog.locator('.cw-filterbar').getByLabel('任务分类').selectOption('midterm');
      await expect(card).toBeVisible();

      const groupResponse = page.waitForResponse(response => response.url().endsWith(`/api/assignments/${fixture.teacherReviewAssignmentId}/group-config`) && response.request().method() === 'GET');
      await card.locator('[data-group-config-btn]').click();
      expect((await groupResponse).status()).toBe(200);
      const groupDialog = page.getByRole('dialog', { name: '分组配置', exact: true });
      await expect(groupDialog).toBeVisible();
      await expect(groupDialog).toContainText(reviewTitle);
      await expect(page).toHaveURL(new RegExp(`/classroom/${fixture.classOfferingId}(?:\\?|$)`));
      await groupDialog.locator('[data-ga-close]').first().click();
      await expect(groupDialog).toBeHidden();
      await expect(dialog).toBeVisible();
      await expect(dialog.locator('.cw-filterbar').getByLabel('任务分类')).toHaveValue('midterm');
      await primary.click();
      await page.waitForURL(new RegExp(`/assignment/${fixture.teacherReviewAssignmentId}(?:\\?|$)`));
      await expect(page.getByTestId('p03-submission-row').first()).toBeVisible();
      expect(errors).toEqual([]);
    });
  });
}

test('student task cards preserve keyboard navigation and hide teacher controls', async ({ page }) => {
  const fixture = readFixture();
  await loginStudent(page, fixture);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const dialog = await openTasks(page);
  const card = dialog.locator(`[data-assignment-task-card][data-assignment-id="${fixture.studentSubmissionAssignmentId}"]`);
  await expect(card).toBeVisible();
  await expect(card.locator('.assignment-card-classification, [data-group-config-btn]')).toHaveCount(0);
  await card.focus();
  await card.press('Enter');
  await page.waitForURL(new RegExp(`/assignment/${fixture.studentSubmissionAssignmentId}(?:\\?|$)`));
});

test('online paper cards expose draft editing and wrong-question review without generic source copy', async ({ page }) => {
  const fixture = readFixture();
  await loginTeacher(page, fixture);
  await expectHealthUsesRuntimeDb(page, fixture);
  const title = 'P03 QA 计算机网络原理 第2课课堂练习：互联网基础与网络命令';
  const created = await page.request.post('/api/exam-papers', { data: {
    title, scope_level: 'private',
    questions: { grading: { total_score: 100, description: '按命令、目标地址和结果解释评分，总分 100 分。' }, pages: [{ name: '网络命令', questions: [{
      id: 'network-command', type: 'textarea', text: '说明如何检查网关是否可达。',
      answer: '使用 ping 命令检查网关的响应。', points: 100,
      grading_guidance: '正确说明命令、地址和结果各步骤。', deduction_points: '命令或目标地址错误扣 50 分。',
    }] }] },
  } });
  expect(created.status()).toBe(200);
  const { paper_id: paperId } = await created.json();
  const assigned = await page.request.post(`/api/exam-papers/${paperId}/assign`, { data: {
    class_offering_id: fixture.classOfferingId, assessment_kind: 'homework', status: 'new',
  } });
  expect(assigned.status()).toBe(200);
  const { assignment_id: assignmentId } = await assigned.json();
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  const dialog = await openTasks(page);
  await dialog.getByLabel('查找任务').fill(title);
  const card = dialog.locator(`[data-assignment-task-card][data-assignment-id="${assignmentId}"]`);
  const primary = card.locator('.assignment-card-primary-link');
  await expect(primary).toHaveText('继续编辑');
  await primary.click();
  await page.waitForURL(new RegExp(`/assignment/${assignmentId}(?:\\?|$)`));
  const published = await page.request.put(`/api/assignments/${assignmentId}`, { data: {
    title, status: 'published', availability_mode: 'deadline',
    due_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
  } });
  expect(published.status()).toBe(200);
  await page.goto(`/classroom/${fixture.classOfferingId}`);
  await expect(dialog).toBeVisible();
  await expect(card).toBeVisible();
  await expect(primary).toHaveText('查看任务');
  await expect(card.locator('.assignment-card-tags')).toContainText('在线试卷');
  await expect(card.locator('.assignment-card-desc')).toHaveCount(0);
  const wrongSummary = card.locator('.assignment-wrong-summary-link');
  await expect(wrongSummary).toHaveAttribute('href', `/assignment/${assignmentId}/wrong-summary`);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 980 });
    await card.scrollIntoViewIfNeeded();
    await expectNoHorizontalOverflow(card);
    await expectControlsInsideCard(card);
    await expect(wrongSummary).toBeVisible();
    fs.mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, `task-card-exam-${width}.png`) });
  }
  await wrongSummary.click();
  await page.waitForURL(new RegExp(`/assignment/${assignmentId}/wrong-summary(?:\\?|$)`));
  await expect(page.locator('body')).toContainText('错题归集');
});
