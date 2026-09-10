import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';

const source = fs.readFileSync('static/js/agent_user_confirmation.js', 'utf8');
const modal = fs.readFileSync('static/js/process_material_modal.js', 'utf8');
const teaching = fs.readFileSync('static/js/teaching_lifecycle_review.js', 'utf8');
const styles = fs.readFileSync('static/css/grade_publication.css', 'utf8');
const builtStyles = fs.readFileSync('static/css/tailwind-app.css', 'utf8');
const ui = `export function escapeHtml(value){const n=document.createElement('span');n.textContent=String(value??'');return n.innerHTML.replaceAll('"','&quot;');}`;

async function mount(page: Page, mode: 'normal' | 'retry' | 'conflict' | 'roster' | 'unknown' = 'normal', kind: 'grades' | 'signature' | 'teaching' = 'grades', approveAllowed = true) {
  const posts: any[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let hash = 'a'.repeat(64);
  let reviewHash = 'c'.repeat(64);
  let executed = false;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const preview = () => kind === 'teaching' ? ({action: 'delete_empty_class', execution_mode: 'user_confirmation',
    params: {class_id: 82, expected_review_hash: reviewHash}, confirmation_token: 'token-' + hash,
    confirmation_review: {title: '删除空班级', summary: '网络工程2401班', expected_confirmation_text: '网络工程2401班', can_execute: approveAllowed,
      impact_sections: [{key: 'history',label: '历史记录',effect: 'detach',count: 2}],
      warnings: [{code: 'retain_history',message: '历史记录保留，解除原班级关联。'}], blockers: approveAllowed ? [] : ['有学生加入，不能删除此班级。']}
  }) : kind === 'signature' ? ({ action: 'review_signature_request', execution_mode: 'user_confirmation',
    params: { request_id: 82, expected_snapshot_id: hash, expected_review_hash: reviewHash }, confirmation_token: 'token-' + hash,
    confirmation_review: { can_execute: true, approve_allowed: approveAllowed,
      material_title: '试卷分析表 · 数据库原理', requester_name: '<img src=x onerror=alert(1)>', signature_name: 'Synthetic signer', point_label: '系（教研室）审核',
      request_note: '请审核申请时版本', scope_notice: '批准仅针对申请时的材料版本与签章点；实际应用签章另行进行。', document_url: 'javascript:alert(1)',
      warnings: [{code: approveAllowed ? 'admin_override' : 'approval_unavailable',message: approveAllowed ? '管理员审批将记录在你的账号下。' : '材料已变化，本次只能拒绝。'}], blocking_reasons: [] }
  }) : ({ action: 'publish_classroom_grades', execution_mode: 'user_confirmation',
    params: { class_offering_id: 40, material_id: 500, expected_source_hash: hash, expected_review_hash: reviewHash, expected_version: 0 }, confirmation_token: 'token-' + hash,
    confirmation_review: { source_hash: hash, expected_version: 0, can_publish: true,
      course_name: '数据库原理', semester_name: '2026—2027 第一学期', formula: { text: '平时 × 40% + 期末 × 60%' },
      students: [{ student_number: 'S7', student_name: '<img src=x onerror=alert(1)>', ordinary_score: 0, final_exam_score: null, overall_score: 0 }],
      warnings: [{ code: 'fixture_review', message: '请核对历史来源成绩。' }], blocking_reasons: [] } });
  await page.route('http://agent-confirmation.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const modules: Record<string, string> = { '/agent_user_confirmation.js': source, '/process_material_modal.js': modal, '/ui.js': ui,
      '/teaching_lifecycle_review.js': teaching, '/api.js': 'export async function apiFetch(){throw new Error("Unexpected ordinary Web API call in C fixture")}' };
    if (modules[path]) return route.fulfill({ contentType: 'text/javascript', body: modules[path] });
    if (path.endsWith('/grade_publication.css')) return route.fulfill({ contentType: 'text/css', body: styles });
    if (path.endsWith('/preview')) return route.fulfill({ json: preview() });
    if (path.endsWith('/execute')) {
      posts.push(route.request().postDataJSON());
      if (mode === 'normal') await gate;
      if (mode === 'retry' && posts.length === 1) return route.fulfill({ status: 503, json: { detail: '服务暂时繁忙' } });
      if ((mode === 'conflict' || mode === 'roster') && posts.length === 1) { if (mode === 'conflict') hash = 'b'.repeat(64); reviewHash = 'd'.repeat(64); return route.fulfill({ status: 409, json: { detail: '来源成绩已变化' } }); }
      executed = true;
      if (mode === 'unknown') return route.abort('connectionreset');
      return route.fulfill({ json: { task: { id: 10 }, result: { status: 'published', version: 1 } } });
    }
    if (path === '/api/agent-tasks/10') return route.fulfill({ json: { task: { id: 10, result_detail: { proposed_actions: [{ executed: executed ? { status: 'published', version: 1 } : null }] } } } });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${builtStyles}${styles}</style><button id="trigger">核对成绩</button><script type="module">import {openAgentUserConfirmation} from '/agent_user_confirmation.js'; window.completed=[];const apiJson=async(url,init)=>{const r=await fetch(url,init);const d=await r.json();if(!r.ok)throw new Error(d.detail);return d};document.querySelector('#trigger').onclick=()=>openAgentUserConfirmation({taskId:10,actionIndex:0,preview:${JSON.stringify(preview())},apiJson,onComplete:d=>window.completed.push(d),onClose:()=>document.querySelector('#trigger').focus()});</script></html>` });
  });
  await page.goto('http://agent-confirmation.test/');
  await page.locator('#trigger').click();
  await expect(page.locator('[data-agent-business-warning]')).toBeVisible();
  return { posts, errors, release };
}

async function confirm(page: Page) {
  await page.locator('[data-agent-business-warning]').check();
  await page.locator('[data-agent-business-note]').fill('已核对原始成绩与学生名单');
  await page.locator('[data-agent-business-reviewed]').check();
}

test('explicit human checks, zero versus missing, escaping and single submission remain correct on mobile', async ({ page }) => {
  const h = await mount(page);
  await expect(page.locator('tbody img')).toHaveCount(0);
  await expect(page.locator('tbody td').nth(3)).toHaveText('缺分');
  await expect(page.locator('tbody td').last()).toHaveText('0');
  await expect(page.locator('[data-agent-business-warning]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.locator('[data-agent-business-reviewed]').check();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await confirm(page);
  await page.screenshot({ path: '.codex-temp/agent-grade-confirmation-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.lp-modal__body').evaluate(n => n.scrollWidth <= n.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: '.codex-temp/agent-grade-confirmation-mobile.png', fullPage: true });
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-agent-business-confirmation]')).toBeVisible();
  expect(h.posts).toHaveLength(1);
  expect(h.posts[0]).toEqual({ params: { class_offering_id: 40, material_id: 500, expected_source_hash: 'a'.repeat(64), expected_review_hash: 'c'.repeat(64), expected_version: 0 }, confirmation_token: 'token-' + 'a'.repeat(64), confirmation_inputs: { accepted_warning_codes: ['fixture_review'], confirmation_note: '已核对原始成绩与学生名单' } });
  h.release();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  await expect(page.locator('#trigger')).toBeFocused();
  expect(h.errors).toEqual([]);
});

test('503 retains input and allows explicit retry with the identical declaration', async ({ page }) => {
  const h = await mount(page, 'retry');
  await confirm(page);
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('服务暂时繁忙');
  await expect(page.locator('[data-agent-business-note]')).toHaveValue('已核对原始成绩与学生名单');
  await expect(page.locator('[data-agent-business-reviewed]')).toBeChecked();
  expect(h.posts).toHaveLength(1);
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts[1]).toEqual(h.posts[0]);
  expect(h.errors).toEqual([]);
});

for (const mode of ['conflict', 'roster'] as const) {
test(`${mode} preserves notes but requires new checks and the latest server preview`, async ({ page }) => {
  const h = await mount(page, mode);
  await confirm(page);
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源成绩已变化');
  await page.locator('[data-agent-business-refresh]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源已更新');
  await expect(page.locator('[data-agent-business-note]')).toHaveValue('已核对原始成绩与学生名单');
  await expect(page.locator('[data-agent-business-warning]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-reviewed]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  expect(h.posts).toHaveLength(1);
  await confirm(page);
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts[1].params.expected_source_hash).toEqual((mode === 'conflict' ? 'b' : 'a').repeat(64));
  expect(h.posts[1].params.expected_review_hash).toEqual('d'.repeat(64));
  expect(h.posts[1].confirmation_token).toEqual('token-' + (mode === 'conflict' ? 'b' : 'a').repeat(64));
  expect(h.errors).toEqual([]);
});

}

test('lost response resolves the committed proposal receipt without repeating the publication', async ({ page }) => {
  const h = await mount(page, 'unknown');
  await confirm(page);
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).completed[0].result)).toEqual({ status: 'published', version: 1 });
  expect(h.errors).toEqual([]);
});

test('signature review requires a deliberate decision and document acknowledgement with a scoped preview link', async ({ page }) => {
  const h = await mount(page, 'normal', 'signature');
  await expect(page.locator('[data-agent-business-review] img')).toHaveCount(0);
  await expect(page.getByRole('link', { name: '打开申请时的文档' })).toHaveAttribute('href', '/api/signatures/requests/82/preview');
  await expect(page.locator('[data-agent-business-decision]')).toHaveValue('');
  await confirm(page);
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.locator('[data-agent-business-decision]').selectOption('approve');
  await expect(page.locator('[data-agent-business-publish]')).toBeEnabled();
  await page.screenshot({ path: '.codex-temp/agent-signature-confirmation-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.lp-modal__body').evaluate(n => n.scrollWidth <= n.clientWidth + 1)).toBe(true);
  await page.screenshot({ path: '.codex-temp/agent-signature-confirmation-mobile.png', fullPage: true });
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-decision]')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-agent-business-confirmation]')).toBeVisible();
  expect(h.posts).toHaveLength(1);
  expect(h.posts[0].confirmation_inputs).toEqual({ decision: 'approve', accepted_warning_codes: ['admin_override'], confirmation_note: '已核对原始成绩与学生名单' });
  h.release();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

test('changed signature review clears the decision and acknowledgements while retaining the note', async ({ page }) => {
  const h = await mount(page, 'roster', 'signature');
  await confirm(page);
  await page.locator('[data-agent-business-decision]').selectOption('approve');
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源成绩已变化');
  await page.locator('[data-agent-business-refresh]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源已更新');
  await expect(page.locator('[data-agent-business-decision]')).toHaveValue('');
  await expect(page.locator('[data-agent-business-reviewed]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-warning]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-note]')).toHaveValue('已核对原始成绩与学生名单');
  expect(h.posts).toHaveLength(1);
  await confirm(page);
  await page.locator('[data-agent-business-decision]').selectOption('reject');
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts[1].params.expected_review_hash).toEqual('d'.repeat(64));
  expect(h.posts[1].confirmation_inputs.decision).toEqual('reject');
  expect(h.errors).toEqual([]);
});

test('stale material disallows approval and requires an explained rejection', async ({ page }) => {
  const h = await mount(page, 'normal', 'signature', false);
  await expect(page.locator('[data-agent-business-decision] option[value=approve]')).toHaveJSProperty('disabled', true);
  await page.locator('[data-agent-business-decision]').selectOption('reject');
  await page.locator('[data-agent-business-warning]').check();
  await page.locator('[data-agent-business-reviewed]').check();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.locator('[data-agent-business-note]').fill('材料更新后请重新申请');
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  expect(h.posts[0].confirmation_inputs.decision).toEqual('reject');
  h.release();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

test('teaching confirmation requires typed name and never prepopulates it', async ({ page }) => {
  const h = await mount(page, 'normal', 'teaching');
  await expect(page.getByRole('region', {name: '本次操作影响'})).toContainText('历史记录');
  await expect(page.locator('[data-teaching-confirmation-text]')).toHaveValue('');
  await confirm(page);
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.locator('[data-teaching-confirmation-text]').fill('Wrong class');
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.locator('[data-teaching-confirmation-text]').fill('网络工程2401班');
  await expect(page.locator('[data-agent-business-publish]')).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.locator('.lp-modal__body').evaluate(n => n.scrollWidth <= n.clientWidth + 1)).toBe(true);
  await page.screenshot({path: '.codex-temp/agent-teaching-confirmation-mobile.png',fullPage:true});
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-teaching-confirmation-text]')).toBeDisabled();
  expect(h.posts).toHaveLength(1);
  expect(h.posts[0].confirmation_inputs.confirmation_text).toEqual('网络工程2401班');
  h.release();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

test('teaching changed preview clears typed name and acknowledgements while keeping explanatory notes', async ({ page }) => {
  const h = await mount(page, 'roster', 'teaching');
  await confirm(page);
  await page.locator('[data-teaching-confirmation-text]').fill('网络工程2401班');
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源成绩已变化');
  await page.locator('[data-agent-business-refresh]').click();
  await expect(page.locator('[data-agent-business-message]')).toContainText('来源已更新');
  await expect(page.locator('[data-teaching-confirmation-text]')).toHaveValue('');
  await expect(page.locator('[data-agent-business-reviewed]')).not.toBeChecked();
  await expect(page.locator('[data-agent-business-note]')).toHaveValue('已核对原始成绩与学生名单');
  expect(h.posts).toHaveLength(1);
  await confirm(page);
  await page.locator('[data-teaching-confirmation-text]').fill('网络工程2401班');
  await page.locator('[data-agent-business-publish]').click();
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts[1].params.expected_review_hash).toEqual('d'.repeat(64));
  expect(h.errors).toEqual([]);
});

test('teaching blockers keep a reviewed resource non-executable', async ({ page }) => {
  const h = await mount(page, 'normal', 'teaching', false);
  await expect(page.getByRole('alert')).toContainText('有学生加入');
  await expect(page.locator('[data-teaching-confirmation-text]')).toBeDisabled();
  await expect(page.locator('[data-agent-business-publish]')).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-agent-business-confirmation]')).toHaveCount(0);
  expect(h.posts).toHaveLength(0);
  expect(h.errors).toEqual([]);
});
