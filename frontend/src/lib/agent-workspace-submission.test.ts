import { existsSync, readFileSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { chromium, type Browser, type Page } from '@playwright/test';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

// Actual ESM public API + native DOM events, replacing VM calls to deleted
// classic-widget functions. Real File/FormData and rendering are intentional.
let browser: Browser, page: Page, errors: string[];
beforeAll(async () => {
  browser = await chromium.launch({ headless: true, ...(existsSync('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe') ? { channel: 'chrome' } : {}) });
}, 15000);
afterAll(async () => { await browser?.close(); });
beforeEach(async () => {
  page = await browser.newPage(); errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://agent-submission.test') return route.abort();
    if (url.pathname.startsWith('/static/js/')) {
      const file = resolve(`.${url.pathname}`);
      return file.startsWith(resolve('static/js') + sep) && existsSync(file)
        ? route.fulfill({ contentType: 'text/javascript', body: readFileSync(file, 'utf8') }) : route.fulfill({ status: 404 });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><title>课堂工作</title><main id="workbench"></main><script>
      window.AI_WORKSPACE_WIDGET_CONFIG={userKey:'teacher:7',userRole:'teacher',classOfferingId:31};
    </script><script type="module">
      import {createAgentWorkbench} from '/static/js/agent_workbench.js';
      import * as render from '/static/js/agent_workbench_render.js';
      const h=window.h={requests:[],notifications:[],pending:[],failDetail:false,failList:false,render,
        task:{id:9,title:'课堂报告',private_instruction:'请生成课堂讨论报告',status:'queued',runtime_status:'queued',is_owner:true,is_terminal:false,events:[]}};
      h.apiJson=async(url,options={})=>{
        const payload=options.body instanceof FormData?JSON.parse(options.body.get('payload')):options.body?JSON.parse(options.body):null;
        const files=options.body instanceof FormData?options.body.getAll('files').map(file=>({name:file.name,size:file.size})):[];
        const request={url,method:options.method||'GET',payload,files};h.requests.push(request);
        if(request.method==='POST')return new Promise((resolve,reject)=>h.pending.push({resolve,reject}));
        if(url==='/api/agent-tasks/bootstrap')return {runtime_configured:true,tasks:[],workflow_catalog:[],queue_state:{}};
        if(url==='/api/agent-tasks?limit=40'){if(h.failList)throw new Error('refresh disconnected');return {tasks:[],queue_state:{}};}
        if(h.failDetail)throw new Error('detail disconnected');
        return {task:h.task};
      };
      h.workbench=createAgentWorkbench({root:document.getElementById('workbench'),config:window.AI_WORKSPACE_WIDGET_CONFIG,
        notify:(message,type)=>h.notifications.push({message,type}),apiJson:h.apiJson,capture:async()=>null});
      await h.workbench.prefill('  请生成课堂讨论报告  ');window.ready=true;
    </script>` });
  });
  await page.goto('http://agent-submission.test/classroom/31');
  await page.waitForFunction(() => (window as any).ready);
});
afterEach(async () => { expect(errors).toEqual([]); await page?.close(); });

const input = () => page.locator('[data-awb-input]');
const sent = () => page.evaluate(() => (window as any).h.requests.filter((request: any) => request.method === 'POST'));
const notices = () => page.evaluate(() => (window as any).h.notifications);
async function files(names = ['lesson.txt']) {
  await page.locator('[data-awb-file-input]').setInputFiles(names.map(name => ({ name, mimeType: 'text/plain', buffer: Buffer.from('课件内容') })));
}
async function submit() {
  await page.locator('[data-awb-composer]').evaluate(form => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
}
async function accept(index = 0, result = { task: { id: 9 } }) {
  await page.evaluate(({ index, result }) => (window as any).h.pending[index].resolve(result), { index, result });
  await expect.poll(() => page.locator('[data-awb-send]').isDisabled()).toBe(false);
}

describe('Agent public composer submission', () => {
  it('retains the exact draft/files on failure, locks duplicate pending submits, and retries through the same endpoint', async () => {
    await files(); const original = await input().inputValue(); await submit();
    await expect.poll(async () => (await sent()).length).toBe(1);
    expect(await input().inputValue()).toBe(original);
    expect(await page.locator('[data-awb-files]').textContent()).toContain('lesson.txt');
    expect(await page.locator('[data-awb-send]').isDisabled()).toBe(true);
    await submit(); expect(await sent()).toHaveLength(1);
    await page.evaluate(() => (window as any).h.pending[0].reject(new Error('503 unavailable')));
    await expect.poll(() => page.locator('[data-awb-send]').isDisabled()).toBe(false);
    expect(await input().inputValue()).toBe(original);
    expect(await page.locator('[data-awb-files]').textContent()).toContain('lesson.txt');
    await submit(); await accept(1);
    expect(await input().inputValue()).toBe('');
    expect(await page.locator('[data-awb-files]').textContent()).toBe('');
    const requests = await sent(); expect(requests).toHaveLength(2);
    expect(requests[1]).toMatchObject({ url: '/api/agent-tasks', payload: { instruction: original.trim(), page_context: { page: { path: '/classroom/31' } } }, files: [{ name: 'lesson.txt' }] });
  });
  it('preserves new text and added files while removing only the accepted attachment snapshot', async () => {
    await files(); await submit(); await input().fill('接下来补充另一份报告'); await files(['next.txt']); await accept();
    expect(await input().inputValue()).toBe('接下来补充另一份报告');
    expect(await page.locator('[data-awb-files]').textContent()).toContain('next.txt');
    expect(await page.locator('[data-awb-files]').textContent()).not.toContain('lesson.txt');
    expect(await page.evaluate(() => sessionStorage.getItem('lanshare.agentDraft.teacher:7'))).toBe('接下来补充另一份报告');
    await submit();
    expect((await sent())[1]).toMatchObject({ url: '/api/agent-tasks', payload: { instruction: '接下来补充另一份报告' }, files: [{ name: 'next.txt' }] });
    await accept(1);
  });
  it('reports an accepted task once even when opening its detail or refreshing its list fails', async () => {
    await page.evaluate(() => { (window as any).h.failDetail = true; (window as any).h.failList = true; });
    await submit(); await accept();
    expect(await notices()).toContainEqual({ message: '已加入全平台 Agent 队列。', type: 'success' });
    expect((await notices()).filter((notice: any) => notice.type === 'error')).toEqual([]);
    expect(await input().inputValue()).toBe(''); expect(await sent()).toHaveLength(1);
  });
  it('keeps attachment limits and a server 413 failure from consuming the draft', async () => {
    await files(['malware.exe']);
    expect((await notices()).some((notice: any) => notice.type === 'warning')).toBe(true);
    expect(await page.locator('[data-awb-files]').textContent()).toBe('');
    await files(); await submit();
    await page.evaluate(() => (window as any).h.pending[0].reject(new Error('附件总大小超过 20MB 上限。')));
    await expect.poll(() => page.locator('[data-awb-send]').isDisabled()).toBe(false);
    expect(await input().inputValue()).toContain('请生成课堂讨论报告');
    expect(await page.locator('[data-awb-files]').textContent()).toContain('lesson.txt');
  });
  it('keeps a newer supplement draft after acceptance even if the subsequent detail read fails', async () => {
    await page.evaluate(async () => { await (window as any).h.workbench.openTask(9); });
    await input().fill('请加入讨论参与情况'); await submit();
    expect((await sent())[0]).toMatchObject({ url: '/api/agent-tasks/9/follow-up', payload: { instruction: '请加入讨论参与情况' } });
    await input().fill('下一条补充尚未发送');
    await page.evaluate(() => { (window as any).h.failDetail = true; });
    await accept();
    expect(await input().inputValue()).toBe('下一条补充尚未发送');
    expect((await notices()).filter((notice: any) => notice.type === 'error')).toEqual([]);
    expect(await page.evaluate(() => sessionStorage.getItem('lanshare.agentDraft.teacher:7'))).toBe('下一条补充尚未发送');
  });
});

describe('Agent public artifact presentation', () => {
  it('renders successful and failed-task retained files from encoded local artifact paths', async () => {
    const html = await page.evaluate(() => ['completed', 'failed'].map(status => (window as any).h.render.renderResult({ id: 7, status, is_terminal: true,
      result_detail: { [status === 'failed' ? 'recovered_artifacts' : 'artifacts']: [{ name: '<report>.md', path: 'outputs/<report>.md' }] } }).html));
    for (const result of html) {
      expect(result).toContain('href="/api/agent-tasks/7/artifacts/outputs/%3Creport%3E.md"');
      expect(result).toContain('&lt;report&gt;.md'); expect(result).not.toContain('<report>');
    }
  });
  it.each(['//untrusted.test/file', '/\\untrusted.test/file', 'javascript:alert(1)', '/\n/untrusted.test'])('never uses model/server download_url as an href: %s', async downloadUrl => {
    const result = await page.evaluate(downloadUrl => (window as any).h.render.renderResult({ id: 7, status: 'completed', is_terminal: true,
      result_detail: { artifacts: [{ name: 'report.md', download_url: downloadUrl }] } }).html, downloadUrl);
    expect(result).not.toContain('<a '); expect(result).toContain('report.md');
  });
  it('retains distinct recovered artifacts once alongside any final-result files', async () => {
    const html = await page.evaluate(() => (window as any).h.render.renderResult({ id: 7, status: 'failed', is_terminal: true,
      result_detail: { artifacts: [{ name: '报告', path: 'outputs/report.md' }], recovered_artifacts: [
        { name: '重复报告', path: 'outputs/report.md' }, { name: '表格', path: 'outputs/table.csv' }, null,
      ] } }).html);
    expect(html.match(/href="\/api\/agent-tasks\/7\/artifacts\//g)).toHaveLength(2);
    expect(html).toContain('outputs/report.md'); expect(html).toContain('outputs/table.csv');
    expect(html).not.toContain('重复报告');
  });
  it('rejects traversal paths and escapes timeline artifact labels independently of supplied download URLs', async () => {
    const result = await page.evaluate(() => {
      const render = (window as any).h.render;
      return { bad: render.artifactUrl(7, 'outputs/../private.txt'), timeline: render.buildTimeline({ id: 7, events: [{ id: 1, event_type: 'artifact', detail: { name: '<report>', path: 'outputs/report.md', download_url: 'javascript:alert(1)' } }] })[0].html };
    });
    expect(result.bad).toBe(''); expect(result.timeline).toContain('href="/api/agent-tasks/7/artifacts/outputs/report.md"');
    expect(result.timeline).toContain('&lt;report&gt;'); expect(result.timeline).not.toContain('javascript:');
  });
});

describe('Agent recipient intent confirmation through the current question API', () => {
  async function question() {
    await page.evaluate(async () => {
      const h = (window as any).h;
      h.task = { ...h.task, runtime_status: 'waiting_input', pending_question: { id: 'confirm-recipients', title: '确认提醒范围',
        questions: [{ id: 'recipients', question: '提醒哪些学生？', options: [{ label: '<学生甲> 等 4 名未交学生', description: '仅当前授权课堂' }, { label: '暂不发送' }] }] } };
      await h.workbench.openTask(9);
    });
  }
  it('does not submit without explicit scope selection, then sends only the question answer and blocks duplicate pending answers', async () => {
    await question(); await page.locator('[data-awb-answer-submit]').click();
    expect(await sent()).toEqual([]);
    expect((await notices()).some((notice: any) => notice.message.includes('请先回答'))).toBe(true);
    const option = page.locator('[data-awb-option]').first();
    expect(await option.textContent()).toContain('<学生甲>'); expect(await option.locator('学生甲').count()).toBe(0);
    await option.click(); await page.locator('[data-awb-answer-submit]').click();
    await page.locator('[data-awb-question-form]').evaluate(form => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
    expect(await sent()).toEqual([{ url: '/api/agent-tasks/9/answer', method: 'POST', payload: { question_id: 'confirm-recipients', answers: [{ id: 'recipients', selected: ['<学生甲> 等 4 名未交学生'], custom: '' }] }, files: [] }]);
    await accept(); expect((await sent()).some((request: any) => /actions|execute|contacts/.test(request.url))).toBe(false);
  });
  it('keeps a failed answer selection retryable and old proposed actions nonexecutable', async () => {
    await question(); await page.locator('[data-awb-option]').last().click(); await page.locator('[data-awb-answer-submit]').click();
    await page.evaluate(() => (window as any).h.pending[0].reject(new Error('Network error')));
    await expect.poll(() => page.locator('[data-awb-send]').isDisabled()).toBe(false);
    expect(await page.locator('[data-awb-option]').last().getAttribute('aria-pressed')).toBe('true');
    await page.locator('[data-awb-answer-submit]').click(); await accept(1); expect(await sent()).toHaveLength(2);
    const html = await page.evaluate(() => (window as any).h.render.renderResult({ id: 11, status: 'completed', is_terminal: true, result_detail: { proposed_actions: [{ action: 'send_student_notification' }] } }).html);
    expect(html).toContain('旧版 Agent'); expect(html).not.toContain('data-agent-action-confirm');
  });
  it('retains text written while an explicitly selected recipient answer is being accepted', async () => {
    await question(); await input().fill('当前回答附注');
    await page.locator('[data-awb-option]').first().click(); await page.locator('[data-awb-answer-submit]').click();
    await input().fill('接下来要补充的内容');
    await page.evaluate(() => { (window as any).h.failDetail = true; });
    await accept();
    expect(await input().inputValue()).toBe('接下来要补充的内容');
    expect((await notices()).filter((notice: any) => notice.type === 'error')).toEqual([]);
  });
});
