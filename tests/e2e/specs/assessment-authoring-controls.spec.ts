import { test, expect, readS3Fixture } from '../fixtures/lq-s3';
import { loginTeacher } from '../fixtures/p03';

test('teacher edits numbered blanks and scoring transactionally without changing legacy answers or losing code', async ({ page }, testInfo) => {
  const fixture = readS3Fixture();
  await loginTeacher(page, fixture);
  const url = '/api/exam-papers/lq-s3-examFailure';
  const before = await page.request.get(url);
  expect(before.status()).toBe(200);
  const original = (await before.json()).paper;
  const reference = '1. 说明步骤\n\n```python\nprint(42)\n```';
  const metadata = { points: 30, grading_guidance: '完整答案得满分。', deduction_points: '缺少步骤扣分。' };
  const seeded = await page.request.put(url, { data: { title: '教师八题型编辑交互', expected_revision: original.revision,
    questions: { grading: { total_score: 90, description: '按标准答案和步骤评分。', style: 'medium' }, pages: [{ name: '编辑验收', questions: [
      { id: 'legacy', type: 'text', text: '原版填空', answer: '原答案A, B；C', ...metadata },
      { id: 'program', type: 'code', text: '**程序题**', answer: reference, attachment_requirements: { required: true, min_count: 1, allow_drawing: true }, ...metadata },
      { id: 'analysis', type: 'analysis', text: '**分析题**', answer: '分析过程与结论', ...metadata },
    ] }] } } });
  expect(seeded.status(), await seeded.text()).toBe(200);
  await page.goto('/exam/lq-s3-examFailure/edit');
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const card = (index: number) => page.locator(`#question-editor [data-question-index="${index}"]`);
  await expect(card(0).getByRole('button', { name: '启用编号填空' })).toBeVisible();
  await expect(card(0).locator('[data-blank-id]')).toHaveCount(0);

  await page.getByRole('group', { name: '添加题目', exact: true }).getByRole('button', { name: '填空题', exact: true }).click();
  await expect(card(3).locator('[data-blank-id="1"]')).toHaveValue('');
  await card(3).locator('[data-blank-id="1"]').fill('第一空, 保留标点');
  await card(3).locator('.q-title-input').press('Control+End');
  await card(3).getByRole('button', { name: '添加空位', exact: true }).click();
  await expect(card(3).locator('[data-blank-id]')).toHaveCount(2);
  await card(3).locator('[data-blank-id="2"]').fill('第二空');
  await card(3).locator('.q-title-input').press('Control+End');
  await card(3).getByRole('button', { name: '添加空位', exact: true }).click();
  await card(3).locator('[data-blank-id="3"]').fill('第三空\n第二行');
  await card(3).getByRole('button', { name: '删除第 2 空', exact: true }).click();
  await page.getByRole('dialog', { name: '删除空位', exact: true }).getByRole('button', { name: '删除空位', exact: true }).click();
  await expect(card(3).locator('[data-blank-id]')).toHaveCount(2);
  await expect(card(3).locator('[data-blank-id="1"]')).toHaveValue('第一空, 保留标点');
  await expect(card(3).locator('[data-blank-id="3"]')).toHaveValue('第三空\n第二行');
  await card(3).locator('.q-title-input').fill('分别填写 {{1}} 和 {{3}}。');

  await card(0).getByRole('button', { name: '启用编号填空' }).click();
  await page.getByRole('dialog', { name: '启用编号填空', exact: true }).getByRole('button', { name: '启用', exact: true }).click();
  await expect(card(0).locator('[data-blank-id="1"]')).toHaveValue('原答案A, B；C');

  // Use the visible shared dropdown; switching subjective labels retains text and attachments.
  await card(1).locator('.q-actions .lq-dropdown__trigger').click();
  await page.getByRole('option', { name: '综合题', exact: true }).click();
  await expect(card(1).locator('.q-type-select')).toHaveValue('comprehensive');

  await page.getByRole('button', { name: '评分标准', exact: true }).click();
  const modal = page.locator('#scoring-modal');
  await expect(modal).toBeVisible();
  await modal.locator('[data-action="select-scoring-question"][data-scoring-index="3"]').click();
  await modal.getByRole('textbox', { name: '第 1 空参考答案', exact: true }).fill('此修改应取消');
  await modal.getByRole('button', { name: '取消', exact: true }).click();
  await expect(modal).toBeHidden();
  await expect(card(3).locator('[data-blank-id="1"]')).toHaveValue('第一空, 保留标点');

  await page.getByRole('button', { name: '评分标准', exact: true }).click();
  await modal.locator('[data-action="select-scoring-question"][data-scoring-index="1"]').click();
  await expect(modal.getByRole('textbox', { name: '第 2 题参考答案', exact: true })).toHaveValue(reference);
  await expect(modal.locator('.scoring-question-card.is-active').getByRole('button', { name: '插入代码块', exact: true })).toBeVisible();
  await modal.locator('[data-action="select-scoring-question"][data-scoring-index="3"]').click();
  await modal.getByRole('textbox', { name: '第 1 空参考答案', exact: true }).fill('第一空确认答案');
  await modal.locator('.scoring-question-card.is-active input[type="number"]').fill('10');
  await modal.getByRole('button', { name: '应用评分标准', exact: true }).click();
  await expect(modal).toBeHidden();
  await expect(card(3).locator('[data-blank-id="1"]')).toHaveValue('第一空确认答案');
  const saved = page.waitForResponse(response => new URL(response.url()).pathname === url && response.request().method() === 'PUT');
  await page.locator('#exam-save-button').click();
  const savedResponse = await saved;
  expect(savedResponse.status(), await savedResponse.text()).toBe(200);
  const paper = (await (await page.request.get(url)).json()).paper;
  const questions = JSON.parse(paper.questions_json).pages[0].questions;
  expect(questions[0]).toMatchObject({ type: 'text', blanks: [{ id: '1', answer: '原答案A, B；C' }] });
  expect(questions[1]).toMatchObject({ type: 'comprehensive', answer: reference, attachment_requirements: { required: true, min_count: 1 } });
  expect(questions[3]).toMatchObject({ type: 'text', blanks: [{ id: '1', answer: '第一空确认答案' }, { id: '3', answer: '第三空\n第二行' }] });
  await page.reload();
  await expect(card(3).locator('[data-blank-id="3"]')).toHaveValue('第三空\n第二行');
  await page.setViewportSize({ width: 390, height: 844 });
  await card(3).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(2);
  await page.screenshot({ path: testInfo.outputPath('teacher-numbered-blanks-390.png'), fullPage: true });
  expect(errors).toEqual([]);
});
