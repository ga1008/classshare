import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const source = fs.readFileSync('static/js/ai_workspace_widget.js', 'utf8');
const widget = source.slice(0, source.lastIndexOf("if (document.readyState === 'loading')"));
const styles = fs.readFileSync('static/css/ui-system.src.css', 'utf8');
const builtStyles = fs.readFileSync('static/css/tailwind-app.css', 'utf8');
const realShell = execFileSync('python', ['-X', 'utf8', '-c', "from jinja2 import Environment,FileSystemLoader; print(Environment(loader=FileSystemLoader('templates')).get_template('partials/ai_workspace_widget.html').render(user_info={'role':'teacher','name':'示例教师'}))"], { encoding: 'utf8' });

async function mount(page: Page, mode: 'normal' | 'retry' | 'slow' = 'normal', useRealShell = false) {
  const posts: any[] = [];
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let holdDetail = false;
  let detailHeld = false;
  let releaseDetail!: () => void;
  const detailGate = new Promise<void>(resolve => { releaseDetail = resolve; });
  const task: any = { id: 9, is_owner: true, is_active: true, status: 'running', runtime_status: 'waiting_input', status_label: '等待你的回答', title: '课堂活动方案', events: [], questions: [{
    id: 'request-1', status: 'pending', expires_at: Math.floor(Date.now() / 1000) + 3600,
    questions: [
      { id: 'format', question: '希望采用哪种课堂形式？', header: '课堂形式', detail: '请选择一种形式，也可以填写自己的想法。', multiSelect: false, options: [{ label: '小组讨论', description: '围绕案例开展合作探究。' }, { label: '个别练习', description: '每位学生独立完成。' }] },
      { id: 'outputs', question: '需要哪些产物？', multiSelect: true, options: [{ label: '活动说明', description: '可直接给学生阅读。' }, { label: '评价量表', description: '用于记录学生表现。' }] },
    ],
  }] };
  await page.route('http://agent-questions.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/answer')) {
      posts.push(route.request().postDataJSON());
      if (mode === 'slow') await gate;
      if (mode === 'retry' && posts.length === 1) return route.fulfill({ status: 503, json: { detail: '暂时无法提交，答案已保留，请重试。' } });
      task.questions[0].status = 'answered';
      task.questions[0].answers = posts.at(-1).answers;
      task.runtime_status = 'running';
      task.status_label = '运行中';
      return route.fulfill({ json: { question: task.questions[0] } });
    }
    if (url.pathname === '/api/agent-tasks/9') {
      const snapshot = JSON.stringify({ task });
      if (holdDetail) { holdDetail = false; detailHeld = true; await detailGate; }
      return route.fulfill({ contentType: 'application/json', body: snapshot });
    }
    if (url.pathname.endsWith('/events')) return route.fulfill({ json: { task_id: 9, events: [{ id: 2, event_type: 'question_closed', detail: { question_id: 'request-1' } }] } });
    if (useRealShell) return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${builtStyles}</style><body>${realShell}</body></html>` });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles}</style><style>body{margin:0;padding:16px;background:#f5f8fa}#ai-chat-modal{position:static!important;display:block;width:100%;max-width:620px;margin:auto}#ai-chat-messages-box{display:block;padding:0}.ai-chat-message{display:block;margin:0}.bubble{box-sizing:border-box}button,input,textarea{font:inherit}*{box-sizing:border-box}</style><div id="ai-chat-modal" style="display:block"><div id="ai-chat-messages-box"></div></div></html>` });
  });
  await page.goto('http://agent-questions.test/');
  if (useRealShell) await page.evaluate(() => {
    const modal = document.querySelector('#ai-chat-modal') as HTMLElement;
    modal.style.display = 'block'; modal.setAttribute('aria-hidden', 'false');
    document.querySelector('#ai-chat-messages-box')!.replaceChildren();
    (document.querySelector('#ai-chat-fab') as HTMLElement).style.display = 'none';
  });
  await page.addScriptTag({ content: `(() => { window.AI_WORKSPACE_WIDGET_CONFIG={taskCenterEnabled:true}; ${widget}
    currentChatSurface=()=>({messagesBox:document.querySelector('#ai-chat-messages-box'),scrollToBottom:()=>{}});
    refreshAgentComposerChrome=()=>{};renderAgentStarters=()=>{};
    bindAgentQuestionInteractions(document.querySelector('#ai-chat-messages-box'));
    window.EventSource=class {constructor(){window.fixtureStream=this;} close(){}};
    window.agentFixture={refresh:()=>loadTaskDetail(9), stream:()=>startTaskEventStream(9), poll:()=>{taskEventStreamDisabled=true;return pollTaskEventsOnce();}, render:renderTaskDetail,
      executeSecure:()=>{refreshTasks=async()=>{};notify=()=>{};return executeAgentAction(document.querySelector('[data-agent-action-confirm]'));}};
  })();` });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  return { posts, errors, task, release, holdNextDetail: () => { holdDetail = true; }, detailHeld: () => detailHeld, releaseDetail };
}

test('question form retains exact input nodes, focus, selection and drafts across task refresh and SSE', async ({ page }) => {
  const h = await mount(page);
  await expect(page.getByText('需要你的回答', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.locator('[data-question-feedback]')).toContainText('请回答每个问题');
  expect(h.posts).toHaveLength(0);
  await page.getByLabel('小组讨论').check();
  await page.getByLabel('活动说明').check();
  await page.getByLabel('评价量表').check();
  const text = page.locator('[data-question-item="outputs"] textarea');
  await text.fill('请保留学生可编辑版本');
  await page.screenshot({ path: '.codex-temp/agent-questions-desktop.png', fullPage: true });
  await text.evaluate((node: HTMLTextAreaElement) => { node.setSelectionRange(2, 5); (window as any).originalQuestionInput = node; });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  expect(await text.evaluate(node => node === (window as any).originalQuestionInput && document.activeElement === node)).toBe(true);
  expect(await text.evaluate((node: HTMLTextAreaElement) => [node.selectionStart, node.selectionEnd])).toEqual([2, 5]);
  await page.evaluate(() => { (window as any).agentFixture.stream(); (window as any).fixtureStream.onmessage({ data: JSON.stringify({events:[{id:1,event_type:'question_requested',detail:{question_id:'request-1'}}]}) }); });
  await expect(text).toHaveValue('请保留学生可编辑版本');
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.getByText('已回答 · Agent 将继续任务', { exact: true })).toBeVisible();
  await page.locator('[data-agent-question-request] summary').click();
  await expect(page.locator('.ai-agent-question-answer').last()).toContainText('活动说明；评价量表；请保留学生可编辑版本');
  expect(h.posts).toEqual([{ answers: [{ id: 'format', selected: ['小组讨论'], custom: '' }, { id: 'outputs', selected: ['活动说明', '评价量表'], custom: '请保留学生可编辑版本' }] }]);
  expect(h.errors).toEqual([]);
});

test('failed answer remains editable and retries without losing custom single choice or multi choices', async ({ page }) => {
  const h = await mount(page, 'retry');
  await page.getByLabel('小组讨论').check();
  await page.locator('[data-question-item="format"] textarea').fill('角色扮演');
  await expect(page.getByLabel('小组讨论')).not.toBeChecked();
  await page.getByLabel('活动说明').check();
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.locator('[data-question-feedback]')).toContainText('暂时无法提交');
  await expect(page.getByRole('button', { name: '提交回答' })).toBeEnabled();
  await expect(page.locator('[data-question-item="format"] textarea')).toHaveValue('角色扮演');
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.getByText('已回答 · Agent 将继续任务', { exact: true })).toBeVisible();
  expect(h.posts).toHaveLength(2);
  expect(h.posts[0]).toEqual(h.posts[1]);
  expect(h.errors).toEqual([]);
});

test('in-flight answer cannot be duplicated even when a pending task refresh arrives', async ({ page }) => {
  const h = await mount(page, 'slow');
  await page.getByLabel('小组讨论').check();
  await page.getByLabel('活动说明').check();
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect.poll(() => h.posts.length).toBe(1);
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.getByRole('button', { name: '提交回答' })).toBeDisabled();
  await page.locator('form').evaluate(form => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(h.posts).toHaveLength(1);
  h.release();
  await expect(page.getByText('已回答 · Agent 将继续任务', { exact: true })).toBeVisible();
  expect(h.errors).toEqual([]);
});

test('mobile layout, Escape focus, expiry and polling cancellation remain explicit', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const h = await mount(page);
  const text = page.locator('[data-question-item="format"] textarea');
  await text.fill('尚未提交的输入');
  await page.screenshot({ path: '.codex-temp/agent-questions-mobile.png', fullPage: true });
  await text.press('Escape');
  await expect(page.locator('[data-agent-question-request]')).not.toHaveAttribute('open');
  await expect(page.locator('[data-agent-question-request] summary')).toBeFocused();
  await page.locator('[data-agent-question-request] summary').press('Enter');
  await expect(text).toHaveValue('尚未提交的输入');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  h.task.questions[0].expires_at = Math.floor(Date.now() / 1000) - 1;
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.getByText('本次提问已过期', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交回答' })).toHaveCount(0);
  h.task.questions[0].status = 'canceled';
  await page.evaluate(() => (window as any).agentFixture.poll());
  await expect(page.getByText('本次提问已取消', { exact: true })).toBeVisible();
  expect(h.posts).toHaveLength(0);
  expect(h.errors).toEqual([]);
});

test('an older pending detail response cannot reopen a question after an accepted answer', async ({ page }) => {
  const h = await mount(page);
  await page.getByLabel('小组讨论').check();
  await page.getByLabel('活动说明').check();
  h.holdNextDetail();
  await page.evaluate(() => { (window as any).oldDetailRequest = (window as any).agentFixture.refresh(); });
  await expect.poll(h.detailHeld).toBe(true);
  await page.getByRole('button', { name: '提交回答' }).click();
  await expect(page.getByText('已回答 · Agent 将继续任务', { exact: true })).toBeVisible();
  h.releaseDetail();
  await page.evaluate(() => (window as any).oldDetailRequest);
  await expect(page.getByText('已回答 · Agent 将继续任务', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '提交回答' })).toHaveCount(0);
  expect(h.posts).toHaveLength(1);
  expect(h.errors).toEqual([]);
});

test('server text and option labels render as text and non-owner tasks expose no question input', async ({ page }) => {
  const h = await mount(page);
  h.task.questions[0].questions[0].question = '<img src=x onerror=alert(1)>';
  h.task.questions[0].questions[0].options[0].label = '\"><svg onload=alert(1)>';
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('.ai-agent-questions img,.ai-agent-questions svg')).toHaveCount(0);
  await expect(page.locator('legend').first()).toContainText('<img src=x onerror=alert(1)>');
  h.task.is_owner = false;
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('[data-agent-question-form]')).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

test('terminal Agent task separates a pending domain submission from verified material completion', async ({ page }) => {
  const h = await mount(page);
  Object.assign(h.task, { is_active: false, is_terminal: true, status: 'failed', status_label: '失败', runtime_status: '', questions: [], result_summary: '已提交的文档生成仍在运行，尚未获得成品及绑定回执。',
    result_detail: { completion_kind: 'partial', business_outcome_verified: false, completion_blockers: [{ code: 'domain_job_pending' }], platform_operations: [{ action: 'generate_session_document', status: 'completed', completion_status: 'running', result: { ref_id: 20, url: '/classroom/40', label: '已提交课时文档生成' }, domain_result: { generation_task_id: 20, status: 'running' } }] } });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('.ai-task-status')).toHaveText('业务结果待跟进');
  await expect(page.getByRole('heading', { name: '已提交，等待业务结果' })).toBeVisible();
  await expect(page.getByText('已提交 · 处理中', { exact: true })).toBeVisible();
  await expect(page.getByText('最终结论：失败', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '原样重试', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '跟进已有生成任务' })).toBeVisible();
  await expect(page.getByRole('link', { name: '前往课堂查看当前状态' })).toHaveAttribute('href', '/classroom/40');
  Object.assign(h.task, { status: 'completed', status_label: '完成', result_summary: '全部平台回执已核验。' });
  Object.assign(h.task.result_detail, { completion_kind: 'verified_business', business_outcome_verified: true, completion_blockers: [] });
  Object.assign(h.task.result_detail.platform_operations[0].domain_result, { status: 'completed', binding_verified: true, generated_material_id: 42, generated_material_path: 'lesson.md' });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('.ai-task-status')).toHaveText('业务结果已核验');
  await expect(page.getByText('成品及课时绑定已核验', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '跟进已有生成任务' })).toHaveCount(0);
  expect(h.errors).toEqual([]);
});

test('HTTP observations and self authority changes do not claim verified business completion', async ({ page }) => {
  const h = await mount(page);
  Object.assign(h.task, { is_active: false, is_terminal: true, status: 'completed', status_label: '完成', questions: [], runtime_status: '',
    result_detail: { completion_kind: 'observed_http_result', business_outcome_verified: false, platform_requests: [{ request_id: 'r-1', capability_key: 'http.polls.vote', status: 'observed_http_result', observation: { http_status: 200 } }] } });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('.ai-task-status')).toHaveText('已收到平台响应');
  await expect(page.getByText('已收到平台响应 · 业务结果未单独核验', { exact: true })).toBeVisible();
  await expect(page.getByText('业务结果已核验', { exact: true })).toHaveCount(0);
  Object.assign(h.task, { status: 'failed' });
  Object.assign(h.task.result_detail, { completion_kind: 'partial', completion_blockers: [{ code: 'platform_request_uncertain' }] });
  h.task.result_detail.platform_requests[0].status = 'uncertain';
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.getByRole('button', { name: '原样重试', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '核对已有平台请求' })).toBeVisible();
  Object.assign(h.task, { status: 'completed' });
  Object.assign(h.task.result_detail, { completion_kind: 'authority_changed', completion_blockers: [], platform_requests: [] });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(page.locator('.ai-task-status')).toHaveText('权限变更已提交');
  await expect(page.getByRole('heading', { name: '已按新权限停止当前任务' })).toBeVisible();
  expect(h.errors).toEqual([]);
});

test('request reconciliation retains notes, keeps HTTP facts separate, and never unlocks unknown host execution', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const h = await mount(page, 'normal', true);
  const view: any = { id: 'r-1', task_id: 9, summary: '提交课堂投票', revision: 'revision-1', host_execution_finished_at: null,
    request: { parameters: { body: { option_ids: [2] } } }, observation: { status: 'uncertain', http_status: 503, verified_business: false },
    reconciliation: {}, can_reconcile_occurred: false, can_reconcile_not_occurred: false, block_reason: '执行是否结束尚未获得证明，仍需执行恢复核查。' };
  const confirmations: any[] = [];
  await page.route('**/api/agent-tasks/9/platform-requests/r-1**', async route => {
    if (route.request().method() === 'POST') {
      confirmations.push(route.request().postDataJSON());
      if (confirmations.length === 1) return route.fulfill({ status: 503, json: { detail: '保存暂不可用，说明已保留。' } });
      view.reconciliation = { resolution: 'not_occurred', note: confirmations.at(-1).note, at: '2026-09-10T20:00:00', verified_business: false };
      view.can_reconcile_occurred = false; view.can_reconcile_not_occurred = false;
    }
    return route.fulfill({ json: { status: 'success', request: view } });
  });
  Object.assign(h.task, { is_terminal: true, is_active: false, status: 'failed', questions: [], runtime_status: '', result_detail: { completion_kind: 'partial',
    platform_requests: [{ request_id: 'r-1', capability_key: 'http.polls.vote', status: 'uncertain', observation: { http_status: 503 } }] } });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  const opener = page.getByRole('button', { name: '查看请求与核对' });
  await opener.click();
  const dialog = page.getByRole('dialog', { name: '核对平台请求' });
  await expect(dialog.getByText('执行是否结束尚未获得证明，仍需执行恢复核查。')).toBeVisible();
  await expect(dialog.getByRole('radio', { name: '已确认该操作未生效' })).toBeDisabled();
  await expect(dialog.getByRole('button', { name: '保存核对声明' })).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(opener).toBeFocused();
  Object.assign(view, { host_execution_finished_at: '2026-09-10T19:00:00', can_reconcile_occurred: true, can_reconcile_not_occurred: true, block_reason: '' });
  await opener.click();
  await expect(dialog.getByRole('radio', { name: '已确认该操作未生效' })).toBeEnabled();
  await expect(dialog.getByRole('radio', { name: '已确认该操作未生效' })).not.toBeChecked();
  await dialog.getByRole('radio', { name: '已确认该操作未生效' }).check();
  const note = dialog.getByRole('textbox', { name: '核对说明' });
  await note.fill('已查看正常投票详情，当前没有该选项记录。');
  await note.focus();
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await expect(note).toBeFocused();
  await expect(note).toHaveValue('已查看正常投票详情，当前没有该选项记录。');
  await page.screenshot({ path: '.codex-temp/agent-request-reconcile-mobile.png' });
  await dialog.getByRole('button', { name: '保存核对声明' }).click();
  await expect(dialog.getByText('保存暂不可用，说明已保留。')).toBeVisible();
  await expect(note).toHaveValue('已查看正常投票详情，当前没有该选项记录。');
  await dialog.getByRole('button', { name: '保存核对声明' }).click();
  await expect(dialog.getByText('已保存的人工核对声明')).toBeVisible();
  await expect(dialog.getByText('结果不确定 · HTTP 503。业务结果未单独核验。')).toBeVisible();
  await expect(dialog.getByRole('button', { name: '保存核对声明' })).toBeHidden();
  expect(confirmations).toHaveLength(2);
  expect(confirmations[1]).toEqual(confirmations[0]);
  expect(confirmations[0]).toEqual({ resolution: 'not_occurred', note: '已查看正常投票详情，当前没有该选项记录。', expected_revision: 'revision-1' });
  const box = await dialog.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(h.errors).toEqual([]);
});

test('request history loads more by server offset, deduplicates IDs and fetches full detail before confirmation', async ({ page }) => {
  const h = await mount(page);
  const offsets: string[] = [];
  const record = (id: string) => ({ id, summary: `历史请求 ${id}`, details_loaded: false, request: { parameters: {} }, observation: { status: 'uncertain' } });
  await page.route('**/api/agent-tasks/9/platform-requests**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/r-3')) return route.fulfill({ json: { request: { ...record('r-3'), details_loaded: true, revision: 'r3-revision', host_execution_finished_at: null,
      request: { parameters: { original: 'full request fetched' } }, can_reconcile_occurred: false, can_reconcile_not_occurred: false, block_reason: '仍需执行恢复核查。' } } });
    offsets.push(url.searchParams.get('offset')!);
    const next = url.searchParams.get('offset') === '20';
    return route.fulfill({ json: { requests: next ? [record('r-2'), record('r-3')] : [record('r-1'), record('r-2')], has_more: !next, next_offset: next ? null : 20 } });
  });
  Object.assign(h.task, { is_terminal: true, is_active: false, status: 'failed', questions: [], result_detail: { platform_requests: [{ request_id: 'r-1', status: 'uncertain' }] } });
  await page.evaluate(() => (window as any).agentFixture.refresh());
  await page.getByRole('button', { name: '全部平台请求' }).click();
  const list = page.getByRole('dialog', { name: '平台请求记录' });
  await expect(list.locator('[data-request-list-item]')).toHaveCount(2);
  await list.getByRole('button', { name: '加载更多' }).click();
  await expect(list.locator('[data-request-list-item]')).toHaveCount(3);
  await expect(list.getByRole('button', { name: '加载更多' })).toBeHidden();
  expect(offsets).toEqual(['0', '20']);
  await list.locator('[data-request-list-item="r-3"]').getByRole('button').click();
  const detail = page.getByRole('dialog', { name: '核对平台请求' });
  await expect(detail.getByText('仍需执行恢复核查。')).toBeVisible();
  await detail.getByText('查看原请求参数').click();
  await expect(detail.locator('pre')).toContainText('full request fetched');
  await expect(detail.getByRole('button', { name: '保存核对声明' })).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(list.locator('[data-request-list-item="r-3"]').getByRole('button')).toBeFocused();
  expect(h.errors).toEqual([]);
});

test('secure proposal collects password only at execute, preserves failed input across refresh, and clears on success', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  const h=await mount(page,'normal',true);
  const calls:any[]=[];
  Object.assign(h.task,{is_active:false,is_terminal:true,status:'completed',questions:[],result_detail:{proposed_actions:[{action:'reset_teacher_password_secure',label:'重置教师口令',execution_mode:'secure_input',params:{teacher_id:8,expected_revision:'current'}}]}});
  await page.evaluate(()=>(window as any).agentFixture.refresh());
  await page.route('**/api/agent-tasks/9/actions/0/*',async route=>{
    const body=route.request().postDataJSON();calls.push({path:new URL(route.request().url()).pathname,body});
    if(route.request().url().endsWith('/preview')) return route.fulfill({json:{action:'reset_teacher_password_secure',label:'重置教师口令',summary:'为教师 #8 设置新口令。',execution_mode:'secure_input',params:{teacher_id:8,expected_revision:'current'},confirmation_token:`preview-${calls.length}`,secure_fields:[{name:'password',label:'新口令',type:'password',required:true,min_length:8,max_length:128}]}});
    if(calls.filter(x=>x.path.endsWith('/execute')).length===1) return route.fulfill({status:503,json:{detail:'暂时无法提交，请重试。'}});
    h.task.result_detail.proposed_actions[0].executed={label:'教师口令已重置'};
    return route.fulfill({json:{task:h.task,result:{label:'教师口令已重置'}}});
  });
  await page.evaluate(()=>{void (window as any).agentFixture.executeSecure();});
  const dialog=page.locator('[data-agent-secure-dialog]');const password=dialog.getByLabel('新口令');
  await expect(dialog).toBeVisible();expect(calls).toHaveLength(1);
  await expect(dialog.locator('pre')).toContainText('教师编号：8');
  await expect(dialog.locator('pre')).not.toContainText('expected_revision');
  await password.fill('FixtureOnly-Password');
  await password.evaluate(node=>{(window as any).secureInputNode=node;});
  await page.evaluate(()=>(window as any).agentFixture.refresh());
  expect(await password.evaluate(node=>node===(window as any).secureInputNode)).toBe(true);
  await expect(password).toBeFocused();
  await dialog.getByRole('button',{name:'确认执行'}).click();
  await expect(dialog.locator('[data-secure-feedback]')).toContainText('暂时无法提交');
  await expect(password).toHaveValue('FixtureOnly-Password');
  expect(await page.evaluate(()=>[document.documentElement.outerHTML,JSON.stringify(localStorage),JSON.stringify(sessionStorage)].some(x=>x.includes('FixtureOnly-Password')))).toBe(false);
  await page.screenshot({path:'.codex-temp/agent-secure-input-mobile.png',fullPage:true});
  await dialog.getByRole('button',{name:'确认执行'}).click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).secureInputNode.value)).toBe('');
  const previews=calls.filter(x=>x.path.endsWith('/preview'));
  expect(previews).toHaveLength(2);expect(previews.every(x=>JSON.stringify(x.body)==='{"params":{}}')).toBe(true);
  const executes=calls.filter(x=>x.path.endsWith('/execute'));
  expect(executes).toHaveLength(2);expect(executes[0].body).toEqual({params:{},confirmation_token:'preview-1',secure_inputs:{password:'FixtureOnly-Password'}});
  expect(executes[1].body.confirmation_token).not.toBe(executes[0].body.confirmation_token);
  expect(h.errors).toEqual([]);
});

test('secure proposal Escape clears detached input and a lost execute response uses committed receipt without resubmission', async ({page})=>{
  const h=await mount(page);let executes=0;
  Object.assign(h.task,{is_active:false,is_terminal:true,status:'completed',questions:[],result_detail:{proposed_actions:[{action:'create_teacher_account_secure',label:'创建教师',execution_mode:'secure_input',params:{name:'新教师'}}]}});
  await page.evaluate(()=>(window as any).agentFixture.refresh());
  await page.route('**/api/agent-tasks/9/actions/0/*',async route=>{
    if(route.request().url().endsWith('/preview')) return route.fulfill({json:{action:'create_teacher_account_secure',label:'创建教师',execution_mode:'secure_input',params:{name:'新教师'},confirmation_token:'fresh',secure_fields:[{name:'password',label:'初始口令',type:'password',required:true,min_length:8,max_length:128}]}});
    executes++;h.task.result_detail.proposed_actions[0].executed={label:'教师已创建'};
    return route.fulfill({status:502,json:{detail:'响应中断'}});
  });
  await page.evaluate(()=>{void (window as any).agentFixture.executeSecure();});
  const dialog=page.locator('[data-agent-secure-dialog]');
  await dialog.getByLabel('初始口令').fill('Canceled-Password');
  await dialog.getByLabel('初始口令').evaluate(node=>{(window as any).canceledSecureInput=node;});
  await dialog.getByLabel('初始口令').press('Escape');
  await expect(dialog).toHaveCount(0);expect(executes).toBe(0);
  expect(await page.evaluate(()=>(window as any).canceledSecureInput.value)).toBe('');
  await page.evaluate(()=>{void (window as any).agentFixture.executeSecure();});
  await dialog.getByLabel('初始口令').fill('Submitted-Password');
  await dialog.getByRole('button',{name:'确认执行'}).click();
  await expect(dialog).toHaveCount(0);await expect(page.getByText('✓ 教师已创建',{exact:true})).toBeVisible();
  expect(executes).toBe(1);expect(h.errors).toEqual([]);
});

for (const viewport of [{ width: 1440, height: 980 }, { width: 390, height: 844 }]) {
  test(`built CSS and real Jinja widget shell fit ${viewport.width}px without clipped controls`, async ({ page }) => {
    await page.setViewportSize(viewport);
    const h = await mount(page, 'normal', true);
    await expect(page.locator('.ai-chat-container')).toBeVisible();
    const metrics = await page.locator('.ai-chat-container').evaluate(node => ({
      width: node.getBoundingClientRect().width, right: node.getBoundingClientRect().right,
      font: getComputedStyle(node).fontFamily,
      overflow: Array.from(node.querySelectorAll('.ai-workspace-panel,.ai-chat-messages,.ai-chat-input-area')).some(item => item.scrollWidth > item.clientWidth + 1),
    }));
    expect(metrics.right).toBeLessThanOrEqual(viewport.width);
    expect(metrics.overflow).toBe(false);
    expect(metrics.font).toMatch(/sans-serif|Inter|PingFang|Microsoft/i);
    const card = page.locator('.ai-agent-task-card');
    expect(await card.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    await page.screenshot({ path: `.codex-temp/agent-questions-real-${viewport.width}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: '提交回答' }).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button', { name: '提交回答' })).toBeInViewport();
    await expect(page.locator('#ai-chat-textarea')).toBeInViewport();
    expect(h.errors).toEqual([]);
  });
}
