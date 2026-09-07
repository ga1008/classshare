import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const source = fs.readFileSync('static/js/assessment_classification_batch.js', 'utf8');
const api = fs.readFileSync('static/js/api.js', 'utf8');
const partial = fs.readFileSync('templates/partials/assessment_classification_batch.html', 'utf8')
  .replace(/\{#[\s\S]*?#\}/g, '')
  .replace(/\{\{[\s\S]*?\}\}/g, match => match.includes('classroom.id') ? '100' : '/static/js/assessment_classification_batch.js');

async function mount(page: Page, mode: 'success' | 'conflict' | 'unknown' = 'success') {
  const posts: any[] = [];
  const assignments = [{ assignment_id: 1, title: '<img src=x onerror=alert(1)>期末考试', assessment_kind: null,
    assessment_kind_label: '历史任务', classification_status: 'legacy_unknown', assessment_kind_version: 0,
    suggested_assessment_kind: 'final', impact: { scored_count: 2, grading_count: 1, referenced_materials: [{ record_id: 9 }] } }];
  await page.route('http://classification.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/assessment_classification_batch.js')) return route.fulfill({ contentType: 'text/javascript', body: source });
    if (url.pathname.endsWith('/api.js')) return route.fulfill({ contentType: 'text/javascript', body: api });
    if (url.pathname.endsWith('/ui.js')) return route.fulfill({ contentType: 'text/javascript', body: 'export function showToast(message){ window.lastToast = message; }' });
    if (url.pathname.endsWith('/assessment-classifications')) return route.fulfill({ json: { assignments, next_offset: null,
      assessment_kind_options: [{value:'homework',label:'平时作业'},{value:'midterm',label:'期中测验'},{value:'final',label:'期末测验'}] } });
    if (url.pathname.endsWith('/assessment-kinds/confirm')) {
      const data = route.request().postDataJSON();
      posts.push(data);
      if (mode === 'conflict') return route.fulfill({ status: 409, json: { detail: '任务分类已被修改，请刷新后重试' } });
      Object.assign(assignments[0], { assessment_kind: 'final', assessment_kind_label: '期末测验', classification_status: 'confirmed', assessment_kind_version: 1 });
      if (mode === 'unknown') return route.abort('connectionreset');
      return route.fulfill({ json: { changed_count: 1, assignments } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>body{margin:20px;font-family:Arial}*{box-sizing:border-box}</style>${partial}</html>` });
  });
  await page.goto('http://classification.test/');
  await page.getByText('核对历史任务分类', { exact: true }).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  return posts;
}

test('suggestions require explicit selection; safe text and narrow layout preserve the confirmation flow', async ({ page }) => {
  const posts = await mount(page);
  await expect(page.locator('tbody select')).toHaveValue('');
  await expect(page.locator('tbody input')).not.toBeChecked();
  await expect(page.locator('[data-classification-batch-save]')).toBeDisabled();
  await expect(page.locator('tbody img')).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 840 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('tbody select').selectOption('final');
  await expect(page.locator('tbody input')).toBeChecked();
  await page.locator('[data-classification-batch-save]').click();
  await expect(page.locator('tbody select')).toHaveValue('final');
  await expect(page.locator('tbody input')).not.toBeChecked();
  expect(posts).toEqual([{ items: [{ assignment_id: 1, assessment_kind: 'final', expected_version: 0 }], reason: '教师在课堂批量核对任务分类' }]);
});

for (const mode of ['conflict', 'unknown'] as const) {
  test(`${mode} response reloads current state without another mutation`, async ({ page }) => {
    const posts = await mount(page, mode);
    await page.locator('tbody select').selectOption('final');
    await page.locator('[data-classification-batch-save]').click();
    await expect(page.locator('[data-classification-batch-status]')).toContainText(mode === 'conflict' ? '本批未保存' : '未收到保存确认');
    await expect(page.locator('[data-classification-batch-save]')).toBeDisabled();
    await expect(page.locator('tbody select')).toHaveValue(mode === 'unknown' ? 'final' : '');
    expect(posts).toHaveLength(1);
  });
}
