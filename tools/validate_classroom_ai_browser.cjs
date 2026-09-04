/* Deterministic Chrome acceptance of the real AI template, JS and shared UI.
 * node tools/validate_classroom_ai_browser.cjs
 * Uses in-memory HTTP fixtures, including deferred/out-of-order responses.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { spawnSync } = require('node:child_process');
const { chromium, expect } = require('@playwright/test');
const root = path.resolve(__dirname, '..');

const render = spawnSync(path.join(root, 'venv', 'Scripts', 'python.exe'), ['-c', `
import sys
from jinja2 import Environment, FileSystemLoader, DictLoader, ChoiceLoader, select_autoescape
layout = '''<!doctype html><html lang="zh-CN" data-theme="lanshare"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css">{% block extra_head %}{% endblock %}</head><body><main style="max-width:1440px;margin:auto;padding:24px">{% block content %}{% endblock %}</main>{% block scripts %}{% endblock %}</body></html>'''
env=Environment(loader=ChoiceLoader([DictLoader({'manage/layout.html':layout}),FileSystemLoader('templates')]),autoescape=select_autoescape())
env.globals['asset_url']=lambda name: '/static/'+name
html=env.get_template('manage/ai.html').render(my_offerings=[{'id':11,'course_name':'课程甲','class_name':'班级甲','textbook_id':1},{'id':12,'course_name':'课程乙','class_name':'班级乙','textbook_id':1}],my_textbooks=[{'id':1,'title':'旧教材','author_display':'作者甲'},{'id':2,'title':'新教材','author_display':'作者乙'}],user_info={'name':'测试教师'})
sys.stdout.buffer.write(html.encode('utf-8'))
`], { cwd: root, encoding: 'utf8' });
if (render.status !== 0) throw new Error(render.stderr);

const checks = [];
const pending = [];
let holdLoads = false;
let failLoad = false;
let saveStatus = 200;
let pendingSave;
let pendingGeneration;
let savedForm;
const records = {
  11: { has_config: true, textbook_id: 1, system_prompt: '课堂甲配置', syllabus: '课堂甲大纲', classroom_summary: '课堂甲\n当前绑定教材：旧教材' },
  12: { has_config: true, textbook_id: null, system_prompt: '课堂乙配置', syllabus: '', classroom_summary: '课堂乙' },
};
function json(res, payload, status = 200) {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(payload));
}
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (url.pathname === '/') { res.setHeader('content-type', 'text/html; charset=utf-8'); res.end(render.stdout); return; }
  const match = url.pathname.match(/^\/api\/manage\/ai\/config\/(\d+)$/);
  if (match) {
    if (holdLoads) pending.push({ id: Number(match[1]), res });
    else if (failLoad) json(res, { detail: '加载失败，请重试' }, 500);
    else json(res, records[match[1]]);
    return;
  }
  if (url.pathname === '/api/manage/ai/configure') {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const form = await new Response(Buffer.concat(chunks), { headers: { 'content-type': req.headers['content-type'] } }).formData();
    savedForm = Object.fromEntries(form);
    pendingSave = () => json(res, saveStatus === 200 ? { status: 'success', message: 'AI 配置已保存', textbook_id: savedForm.textbook_id ? Number(savedForm.textbook_id) : null } : { detail: '保存失败，请重试' }, saveStatus);
    return;
  }
  if (url.pathname === '/api/manage/ai/ai-generate') {
    pendingGeneration = () => json(res, { status: 'success', system_prompt: 'AI 生成的提示词', syllabus: 'AI 生成的大纲' });
    return;
  }
  if (url.pathname.startsWith('/api/prompt-pool')) { json(res, { status: 'success', prompts: [] }); return; }
  if (url.pathname.startsWith('/static/')) {
    const filename = path.resolve(root, '.' + url.pathname);
    if (!filename.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(filename)) { res.writeHead(404); res.end(); return; }
    res.setHeader('content-type', filename.endsWith('.js') ? 'text/javascript' : filename.endsWith('.css') ? 'text/css' : 'application/octet-stream');
    res.end(fs.readFileSync(filename)); return;
  }
  res.writeHead(404); res.end();
});

(async () => {
  let browser;
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    browser = await chromium.launch({ headless: true, channel: 'chrome' });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const offering = page.locator('#aiOfferingSelect');
    const textbook = page.locator('#aiTextbookSelect');
    const prompt = page.locator('#aiSystemPromptInput');
    const syllabus = page.locator('#aiSyllabusInput');
    const save = page.locator('#aiConfigSubmitBtn');
    const generate = page.locator('#aiGenerateBtn');
    await expect(prompt).toHaveValue('课堂甲配置');
    await expect(save).toBeEnabled();
    checks.push('existing configuration loads into the real template');

    holdLoads = true;
    await offering.selectOption('12');
    await expect(save).toBeDisabled();
    await expect(prompt).toHaveValue('');
    await expect.poll(() => pending.length).toBe(1);
    await offering.selectOption('11');
    await expect.poll(() => pending.length).toBe(2);
    json(pending[1].res, { ...records[11], system_prompt: '最新课堂甲配置' });
    await expect(prompt).toHaveValue('最新课堂甲配置');
    json(pending[0].res, records[12]);
    await expect(page.locator('#aiClassroomSummary')).toHaveText('课堂甲');
    await expect(prompt).toHaveValue('最新课堂甲配置');
    holdLoads = false;
    checks.push('out-of-order classroom responses cannot overwrite the active classroom');

    failLoad = true;
    await offering.selectOption('12');
    await expect(page.locator('#aiConfigRetryBtn')).toBeVisible();
    await expect(save).toBeDisabled();
    await expect(prompt).toBeDisabled();
    failLoad = false;
    await page.locator('#aiConfigRetryBtn').click();
    await expect(prompt).toHaveValue('课堂乙配置');
    await expect(syllabus).toHaveValue('');
    await expect(textbook).toHaveValue('');
    await expect(generate).toBeDisabled();
    checks.push('failed load blocks editing and retries; saved empty fields and null textbook remain empty');

    await textbook.selectOption('2');
    await generate.click();
    await expect.poll(() => Boolean(pendingGeneration)).toBe(true);
    await expect(offering).toBeDisabled();
    await expect(textbook).toBeDisabled();
    await expect(prompt).toBeDisabled();
    await expect(save).toBeDisabled();
    pendingGeneration();
    await expect(prompt).toHaveValue('AI 生成的提示词');
    await expect(offering).toBeEnabled();
    await textbook.selectOption('');
    await expect(prompt).toHaveValue('AI 生成的提示词');
    await expect(page.locator('#aiTextbookSummary')).toHaveText('当前课堂未绑定教材。');
    checks.push('generation locks conflicting edits; textbook changes preserve AI output and clear old context');

    await prompt.fill('保留我的修改');
    saveStatus = 500;
    await save.click();
    await expect.poll(() => Boolean(pendingSave)).toBe(true);
    assert.equal(savedForm.class_offering_id, '12');
    assert.equal(savedForm.system_prompt, '保留我的修改');
    assert.equal(savedForm.textbook_id, '');
    await expect(offering).toBeDisabled();
    pendingSave(); pendingSave = null;
    await expect(save).toBeEnabled();
    await expect(prompt).toHaveValue('保留我的修改');
    saveStatus = 200;
    await save.click();
    await expect.poll(() => Boolean(pendingSave)).toBe(true);
    pendingSave(); pendingSave = null;
    await expect(page.locator('#aiConfigStatus')).toContainText('AI 配置已保存');
    checks.push('save captures enabled form fields before locking; failed save retains edits and retry succeeds');

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(errors, []);
    checks.push('mobile viewport has no horizontal overflow; no uncaught browser errors');
    console.log(JSON.stringify({ status: 'passed', checks }, null, 2));
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
