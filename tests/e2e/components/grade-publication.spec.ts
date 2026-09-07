import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const source = fs.readFileSync('static/js/grade_publication_controls.js', 'utf8');
const modal = fs.readFileSync('static/js/process_material_modal.js', 'utf8');
const api = fs.readFileSync('static/js/api.js', 'utf8');
const styles = fs.readFileSync('static/css/grade_publication.css', 'utf8');
const ui = `export function escapeHtml(value){const node=document.createElement('span');node.textContent=String(value??'');return node.innerHTML.replaceAll('"','&quot;');} export function showToast(){}`;

async function mount(page: Page, failure: 'none' | 'conflict' | 'unknown' | 'source-removed' = 'none') {
  const posts: any[] = [];
  let version = failure === 'source-removed' ? 7 : 0;
  await page.route('http://publication.test/**', async route => {
    const url = new URL(route.request().url());
    const modules: Record<string, string> = { '/grade_publication_controls.js': source, '/process_material_modal.js': modal, '/api.js': api, '/ui.js': ui };
    if (url.pathname.endsWith('/grade_publication.css')) return route.fulfill({ contentType: 'text/css', body: styles });
    if (modules[url.pathname]) return route.fulfill({ contentType: 'text/javascript', body: modules[url.pathname] });
    const current = version ? { publication_id: version, version, status: 'active', published_at: '2026-09-07', source_stale: false } : null;
    if (url.pathname.endsWith('/grade-publication')) return route.fulfill({ json: { current, history: current ? [current] : [] } });
    if (url.pathname.endsWith('/preview') && failure === 'source-removed') throw new Error('Classroom management must not depend on source material');
    if (url.pathname.endsWith('/withdraw')) { posts.push(route.request().postDataJSON()); version = 0; return route.fulfill({json: {status:'withdrawn'}}); }
    if (url.pathname.endsWith('/preview')) return route.fulfill({ json: { preview: {
      source_hash: 'a'.repeat(64), expected_version: version, can_publish: true,
      course_name: '课程', semester_name: '2026-2027第1学期', formula: { text: '平时 × 40% + 期末 × 60%' },
      students: [{ student_number: '20260001', student_name: '<img src=x onerror=alert(1)>', ordinary_score: 0, final_exam_score: 0, overall_score: 0 }],
      warnings: [{ code: 'legacy_execution', message: '历史成绩缺少执行记录，请核对。' }], blocking_reasons: [],
    } } });
    if (url.pathname.endsWith('/publish')) {
      posts.push(route.request().postDataJSON());
      if (failure === 'conflict') return route.fulfill({ status: 409, json: { detail: '来源材料已更新，请重新预览' } });
      version += 1;
      if (failure === 'unknown') return route.abort('connectionreset');
      return route.fulfill({ json: { version, student_count: 1 } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>*{box-sizing:border-box}body{font-family:Arial;margin:0}.lp-modal-overlay{position:fixed;inset:0;padding:16px;display:flex}.lp-modal{width:100%;max-width:900px;background:white;display:flex;flex-direction:column;max-height:90vh}.lp-modal__body{padding:16px;overflow:auto}.lp-modal__foot{padding:16px} ${styles}</style><script type="module">import {openGradePublicationModal} from '/grade_publication_controls.js';openGradePublicationModal({name:'期末成绩单',can_manage:true,ai_import_record:${failure === 'source-removed' ? 'null' : '{id:901}'},assignments:[{class_offering_id:8,course_name:'课程',class_name:'班级',semester:'2026-2027'}]});</script></html>` });
  });
  await page.goto('http://publication.test/');
  if (failure === 'source-removed') await expect(page.locator('.grade-publication__status')).toContainText('当前公布第 7 版');
  else await expect(page.locator('[data-gp-warning]')).toBeVisible();
  return posts;
}

async function confirm(page: Page) {
  await page.locator('[data-gp-warning]').check();
  await page.locator('[data-gp-note]').fill('已核对原始材料和名单');
  await page.locator('[data-gp-confirm]').check();
}

test('publication requires warning and teacher confirmation; zero, escaping and narrow preview remain correct', async ({ page }) => {
  const posts = await mount(page);
  await expect(page.locator('tbody img')).toHaveCount(0);
  await expect(page.locator('tbody td').last()).toHaveText('0');
  await page.locator('[data-gp-confirm]').check();
  await expect(page.locator('[data-gp-publish]')).toBeDisabled();
  await confirm(page);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.lp-modal__body').evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await page.locator('[data-gp-publish]').click();
  await expect(page.locator('[data-gp-message]')).toContainText('已公布第 1 版');
  await expect(page.locator('[data-gp-publish]')).toBeDisabled();
  expect(posts).toEqual([{ material_id: 901, expected_source_hash: 'a'.repeat(64), expected_version: 0, confirmed: true,
    accepted_warning_codes: ['legacy_execution'], confirmation_note: '已核对原始材料和名单' }]);
});

for (const mode of ['conflict', 'unknown'] as const) {
  test(`${mode} resets confirmation and reloads state without automatically publishing twice`, async ({ page }) => {
    const posts = await mount(page, mode);
    await confirm(page);
    await page.locator('[data-gp-publish]').click();
    await expect(page.locator('[data-gp-message]')).toContainText('请核对当前公布版本');
    await expect(page.locator('[data-gp-publish]')).toBeDisabled();
    await expect(page.locator('[data-gp-confirm]')).not.toBeChecked();
    expect(posts).toHaveLength(1);
    await expect(page.locator('.grade-publication__status')).toContainText(mode === 'unknown' ? '当前公布第 1 版' : '尚无正在公布');
  });
}


test('classroom can withdraw an active publication after source removal without previewing or republishing', async ({ page }) => {
  const posts = await mount(page, 'source-removed');
  await expect(page.locator('[data-gp-publish]')).toHaveCount(0);
  await page.getByText('撤回当前公布', {exact:true}).click();
  await expect(page.locator('[data-gp-withdraw]')).toBeDisabled();
  await page.locator('[data-gp-reason]').fill('来源材料已删除，重新核定');
  await page.locator('[data-gp-withdraw]').click();
  await expect(page.locator('[data-gp-message]')).toContainText('已撤回当前公布版本');
  expect(posts).toEqual([{publication_id: 7, reason: '来源材料已删除，重新核定'}]);
});
