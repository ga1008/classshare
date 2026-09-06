/* Run: node --test tests/frontend/career_resume_browser.test.cjs
 * Real isolated browser, intercepted APIs; never uses a student account or AI. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const workspace = path.resolve(__dirname, '../..');
let browser;
before(async () => {
  const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
  browser = await chromium.launch({ headless: true, ...(fs.existsSync(chrome) ? { executablePath: chrome } : {}) });
});
after(async () => { if (browser) await browser.close(); });
function source(file) { return fs.readFileSync(path.join(workspace, file), 'utf8'); }
async function capture(page, name) {
  if (!process.env.CAREER_FRONTEND_QA_DIR) return;
  const directory = path.resolve(process.env.CAREER_FRONTEND_QA_DIR);
  fs.mkdirSync(directory, { recursive: true });
  await page.screenshot({ path: path.join(directory, name + '.png'), fullPage: true });
}
function block(template, name) { return template.match(new RegExp('{% block ' + name + ' %}([\\s\\S]*?){% endblock %}'))[1]; }
function fixture(kind) {
  const career = kind === 'career';
  const template = source(career ? 'templates/career_path.html' : `templates/resume/${kind.startsWith('section_') ? 'section' : kind}.html`).replace('{{ section_key }}', kind.replace('section_', ''));
  const body = block(template, career ? 'body' : 'content');
  const script = career ? 'career_path_app' : 'resume_' + (kind.startsWith('section_') ? 'section' : kind);
  return '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    `<link rel="stylesheet" href="/static/css/${career ? 'career_path' : 'resume_console'}.css"></head>` +
    `<body class="${career ? 'career-page-body' : 'rz-body'}">${body}<div id="toast-container"></div>` +
    '<script src="/static/js/career_tools_client.js"></script>' +
    `<script src="/static/js/${career ? 'career_path_network' : 'resume_common'}.js"></script>` +
    `<script src="/static/js/${script}.js"></script></body></html>`;
}
async function open(kind, api, viewport = { width: 1440, height: 1000 }) {
  const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname.startsWith('/static/')) {
      const local = path.join(workspace, url.pathname.slice(1));
      return route.fulfill({ status: 200, contentType: local.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(local) });
    }
    if (url.pathname.startsWith('/api/')) {
      let body = null; try { body = request.postDataJSON(); } catch (_) {}
      const result = await api(url.pathname, body, request.method(), url, request);
      return route.fulfill({ status: result && result.httpStatus || 200, headers: result && result.headers || {}, contentType: result && result.contentType || 'application/json', body: result && result.rawBody || JSON.stringify(result && result.payload || result || {}) });
    }
    return route.fulfill({ status: 200, contentType: 'text/html', body: fixture(kind) });
  });
  await page.goto('http://career.test/' + (kind === 'career' ? 'career-path' : 'resume/' + kind));
  return { context, page, errors };
}
function careerState(extra = {}) {
  return { ok: true, phase: 'intro', session_status: 'intro', revision: 0, draft_revision: 0,
    quiz_mode: 'quick', quiz_version: 'career-v1', draft: [], student: { name: '测试同学' },
    major: { name: '新专业' }, timeline: {}, network: { cats: [], nodes: [], links: [] }, tasks: {}, ...extra };
}
function questions(mode) {
  return { mode, quiz_version: 'career-v1', questions: [1, 2, 3].map(id => ({ id: 'q' + id, title: '问题 ' + id,
    kind: 'single', options: [{ value: 'one', label: '选项一' }, { value: 'two', label: '选项二' }] })) };
}
test('cold major is nonblocking; double click cannot skip; quiz saves in revision order', async () => {
  let current = careerState({ tasks: { network: { id: 1, status: 'queued', phase_label: '等待处理', can_cancel: false } } });
  const writes = [];
  const env = await open('career', async (url, body, method, query) => {
    if (url.endsWith('/initialize') || url.endsWith('/state')) return current;
    if (url.endsWith('/questions')) return questions(query.searchParams.get('mode'));
    if (url.endsWith('/progress')) { writes.push(body); return { revision: writes.length, draft_revision: writes.length }; }
    return {};
  });
  try {
    await env.page.getByRole('button', { name: /深度探索/ }).click();
    await env.page.getByRole('button', { name: '选项一', exact: true }).dblclick();
    await env.page.getByText('问题 2', { exact: true }).waitFor();
    await capture(env.page, 'career-quiz-pending');
    assert.equal(await env.page.getByText('问题 3', { exact: true }).count(), 0);
    assert.equal(await env.page.locator('#career-waiting').isVisible(), false);
    assert.equal(await env.page.getByRole('link', { name: '简历工作台', exact: true }).isVisible(), true);
    await env.page.getByRole('button', { name: '选项二', exact: true }).click();
    await env.page.getByText('问题 3', { exact: true }).waitFor();
    await env.page.waitForFunction(() => document.querySelector('.career-quiz__count').textContent.includes('3 /'));
    assert.equal(writes.length, 3);
    assert.deepEqual(writes.map(write => write.revision), [0, 1, 2]);
    assert.ok(writes.every(write => write.mode === 'full' && write.quiz_version === 'career-v1'));
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('full quiz draft resumes exact mode; failed question requests expose recovery', async () => {
  let fail = true;
  const requested = [];
  const env = await open('career', async (url, body, method, query) => {
    if (url.endsWith('/initialize')) return careerState({ quiz_mode: 'full', draft: [{ question_id: 'q1', value: 'one' }] });
    if (url.endsWith('/questions')) { requested.push(query.searchParams.get('mode')); return fail ? { httpStatus: 503, payload: { detail: '题目暂不可用' } } : questions('full'); }
    return {};
  });
  try {
    await env.page.getByText('题目加载失败，请刷新重试。', { exact: true }).waitFor();
    assert.equal(await env.page.locator('#career-typewriter').isVisible(), true);
    assert.deepEqual(requested, ['full']);
    fail = false; await env.page.reload();
    await env.page.getByText('问题 2', { exact: true }).waitFor();
    assert.match(await env.page.locator('.career-quiz__count').textContent(), /深度探索/);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('graph escapes AI attributes, supports hyphen tags and keyboard; mobile list is usable', async () => {
  const node = { tag: 'data-analyst', cat: 'analysis', name: '数据分析', rec: 4, know: ['分析'], reason: '与资料中的分析兴趣相关',
    tl: [['入门', '分析助理'], ['进阶', '数据分析师']] };
  const injected = { ...node, tag: 'other" data-audit="injected', name: '跨专业方向' };
  const current = careerState({ phase: 'ready', session_status: 'failed', network_level: 'base',
    network: { cats: [{ id: 'analysis', name: '分析方向', c1: '#63cbff' }], nodes: [node, injected], links: [] } });
  const env = await open('career', async url => url.endsWith('/initialize') ? current : {});
  try {
    await env.page.locator('.career-direction').first().waitFor();
    await capture(env.page, 'career-directions-desktop');
    assert.equal(await env.page.locator('[data-audit]').count(), 0);
    await env.page.getByRole('button', { name: '网络图', exact: true }).click();
    await env.page.locator('.cn-node[role=button]').first().focus(); await env.page.keyboard.press('Enter');
    assert.equal(await env.page.locator('.cn-nlabel.hot').first().textContent(), '数据分析');
    assert.equal(await env.page.locator('#career-detail').isVisible(), true);
    await env.page.keyboard.press('Escape');
    await env.page.getByRole('button', { name: '方向列表', exact: true }).click();
    await env.page.setViewportSize({ width: 390, height: 844 });
    const bounds = await env.page.locator('.career-direction').first().boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390);
    await capture(env.page, 'career-directions-mobile');
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('shared poller never overlaps a pending read and stops at a terminal result', async () => {
  const context = await browser.newContext(), page = await context.newPage();
  try {
    await page.goto('about:blank'); await page.addScriptTag({ content: source('static/js/career_tools_client.js') });
    await page.clock.install();
    await page.evaluate(() => {
      window.calls = 0;
      window.statusPoll = CareerTools.poll({ interval: 1000, load: () => { window.calls++; return new Promise(resolve => window.finishRead = resolve); }, done: result => result.done });
    });
    await page.clock.runFor(60000);
    assert.equal(await page.evaluate(() => window.calls), 1);
    await page.evaluate(() => window.finishRead({ done: true }));
    await page.clock.runFor(60000);
    assert.equal(await page.evaluate(() => window.calls), 1);
    assert.equal(await page.evaluate(() => window.statusPoll.active()), false);
  } finally { await context.close(); }
});
test('resume can save incomplete draft and keeps input after revision conflict', async () => {
  const writes = [];
  const env = await open('builder', async (url, body, method) => {
    if (url.endsWith('/palette')) return { personal: { name: '测试同学' }, personal_labels: { name: '姓名' },
      templates: [{ key: 'classic', label: '经典', description: '单栏' }], self_intro: [], education: [], experience: [], skill: [], certificate: [] };
    if (url === '/api/resume/resumes' && method === 'POST') { writes.push(body); return { id: 88, revision: 1, status: 'draft' }; }
    if (url === '/api/resume/resumes/88' && method === 'PUT') { writes.push(body); return { httpStatus: 409, payload: { detail: { code: 'revision_conflict', message: '资料已更新' } } }; }
    return {};
  });
  try {
    await env.page.locator('#rzResumeTitle').fill('未完成的简历');
    await env.page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await env.page.getByText('草稿已保存 · 版本 1', { exact: true }).waitFor();
    await capture(env.page, 'resume-draft');
    assert.equal(writes[0].draft, true); assert.equal(writes[0].target_position, '');
    await env.page.locator('#rzResumeTitle').fill('需要保留的当前修改');
    await env.page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await env.page.getByRole('dialog', { name: '这份资料有了新版本' }).waitFor();
    assert.equal(writes[1].revision, 1);
    assert.equal(await env.page.locator('#rzResumeTitle').inputValue(), '需要保留的当前修改');
    assert.match(await env.page.locator('.rz-modal textarea').inputValue(), /需要保留的当前修改/);
    const summary = await env.page.locator('.rz-modal textarea').inputValue();
    assert.match(summary, /标题：需要保留的当前修改/);
    assert.doesNotMatch(summary, /template_key|client_id|source_context|"layout"/);
    const filePromise = env.page.waitForEvent('download');
    await env.page.getByRole('button', { name: '下载草稿备份', exact: true }).click();
    const file = await filePromise;
    assert.deepEqual(JSON.parse(fs.readFileSync(await file.path(), 'utf8')), writes[1]);
    await env.page.getByRole('button', { name: '继续查看当前输入', exact: true }).click();
    assert.equal(await env.page.locator('#rzResumeTitle').inputValue(), '需要保留的当前修改');
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('failed major remains usable, reload does not retry, feedback filters agree with graph', async () => {
  const network = { cats: [{ id: 'general', name: '专业方向', c1: '#63cbff' }], nodes: [
    { tag: 'research-path', cat: 'general', name: '调研分析', rec: 4, know: [], tl: [['入门', '调研助理']] },
    { tag: 'service-path', cat: 'general', name: '专业服务', rec: 3, know: [], tl: [['入门', '服务助理']] }
  ], links: [] };
  let current = careerState({ network, result_version: 'v1', feedback_by_tag: {}, tasks: { network: {
    id: 18, status: 'failed', phase_label: '详细专业网络暂未完成', can_retry: true, can_cancel: false
  } } });
  const mutations = [];
  const env = await open('career', async (url, body, method) => {
    if (method === 'POST') mutations.push(url);
    if (url.endsWith('/feedback')) { current = { ...current, revision: current.revision + 1, result_version: 'v' + (current.revision + 2),
      feedback_by_tag: { ...current.feedback_by_tag, [body.career_tag]: { favorite: 'saved', hide: 'dismissed', restore: '' }[body.action] } }; return current; }
    if (url.endsWith('/initialize') || url.endsWith('/state')) return current;
    return {};
  });
  try {
    await env.page.getByRole('button', { name: '先浏览基础方向' }).click();
    assert.equal(await env.page.locator('.career-direction').count(), 2);
    await env.page.locator('.career-direction').first().getByRole('button', { name: '收藏', exact: true }).click();
    await env.page.getByRole('button', { name: '取消收藏', exact: true }).waitFor();
    await env.page.locator('#career-direction-filter').selectOption('saved');
    assert.equal(await env.page.locator('.career-direction').count(), 1);
    await env.page.getByRole('button', { name: '网络图', exact: true }).click();
    assert.equal(await env.page.locator('.cn-node[data-tag]').count(), 1);
    await env.page.getByRole('button', { name: '方向列表', exact: true }).click();
    await env.page.getByRole('button', { name: '暂不考虑', exact: true }).click();
    await env.page.waitForFunction(() => !document.querySelector('.career-direction'));
    await env.page.getByRole('button', { name: '网络图', exact: true }).click();
    assert.equal(await env.page.locator('.cn-node[data-tag]').count(), 0);
    await env.page.getByRole('button', { name: '方向列表', exact: true }).click();
    await env.page.locator('#career-direction-filter').selectOption('dismissed');
    assert.equal(await env.page.getByRole('button', { name: '重新考虑', exact: true }).count(), 1);
    await env.page.reload(); await env.page.getByRole('button', { name: '先浏览基础方向' }).waitFor();
    assert.equal(mutations.filter(url => url.endsWith('/retry')).length, 0);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('lightweight status preserves graph while task failure becomes visible', async () => {
  const node = { tag: 'career-one', cat: 'general', name: '现有方向', rec: 3, tl: [['入门', '助理']] };
  let statusQueries = [];
  const env = await open('career', async (url, body, method, query) => {
    if (url.endsWith('/initialize')) return careerState({ phase: 'ready', session_status: 'ready', result_version: 'result-12',
      network: { cats: [], nodes: [node], links: [] }, tasks: { personalization: { id: 12, status: 'running', can_cancel: true } } });
    if (url.endsWith('/state')) {
      statusQueries.push(query.searchParams.get('known_result_version'));
      return { ok: true, phase: 'ready', session_status: 'ready', result_version: 'result-12', network_unchanged: true,
        tasks: { personalization: { id: 12, status: 'failed', can_retry: true, phase_label: '服务暂不可用' } } };
    }
    return {};
  });
  try {
    await env.page.locator('.career-direction').waitFor();
    await env.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await env.page.getByText('个人推荐 · 服务暂不可用', { exact: true }).waitFor();
    assert.equal(await env.page.locator('.career-direction').count(), 1);
    assert.equal(statusQueries[0], 'result-12');
    assert.equal(await env.page.getByRole('button', { name: '重试', exact: true }).count(), 1);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('resume candidates require review, history restores with revision, downloads pin rendered revision', async () => {
  const item = { id: 11, title: '测试简历', target_position: '运营助理', template_key: 'classic', status: 'review_ready', revision: 3,
    render_revision: 2, optimized_summary_md: '我参与过校园活动', updated_at: '2026-09-06T12:00:00' };
  const writes = [], reads = [];
  const env = await open('list', async (url, body, method, query) => {
    reads.push(url + query.search);
    if (url === '/api/resume/resumes') return { items: [item] };
    if (url.endsWith('/candidates')) return { items: [{ id: 31, kind: 'optimization', status: 'pending', base_revision: 3,
      payload: { source: 'baseline', summary_md: '参与校园活动，负责记录与协调。', tech_stack: ['沟通协调'], notes: ['请核对具体职责'] } }] };
    if (url.endsWith('/versions')) return { items: [{ revision: 3, status: 'draft' }, { revision: 2, status: 'ready' }] };
    if (method === 'POST') { writes.push({ url, body }); return { id: 11, revision: 4 }; }
    return {};
  });
  try {
    await env.page.getByRole('button', { name: '核对待确认内容' }).click();
    await env.page.getByRole('dialog', { name: '测试简历 · 待确认内容' }).waitFor();
    await env.page.getByRole('heading', { name: '基础整理建议', exact: true }).waitFor();
    await env.page.getByText('参与校园活动，负责记录与协调。', { exact: true }).waitFor();
    assert.equal(writes.length, 0);
    await capture(env.page, 'resume-candidate-review');
    await env.page.getByRole('button', { name: '核对无误，采用建议' }).click();
    await env.page.waitForFunction(() => !document.querySelector('.rz-modal'));
    assert.deepEqual(writes[0], { url: '/api/resume/resumes/11/candidates/31/accept', body: { revision: 3 } });
    assert.match(await env.page.getByRole('link', { name: 'PDF', exact: true }).getAttribute('href'), /revision=2/);
    await env.page.getByRole('button', { name: '预览文件' }).click();
    await env.page.locator('iframe').waitFor();
    assert.match(await env.page.locator('iframe').getAttribute('src'), /revision=2/);
    await env.page.keyboard.press('Escape'); await env.page.waitForFunction(() => !document.querySelector('.rz-modal'));
    await env.page.getByRole('button', { name: '历史版本' }).click();
    await env.page.getByRole('button', { name: '恢复为新版本' }).click();
    await env.page.waitForFunction(() => !document.querySelector('.rz-modal'));
    assert.deepEqual(writes[1], { url: '/api/resume/resumes/11/versions/2/restore', body: { revision: 3 } });
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
test('import review sends only checked sections, items and personal fields', async () => {
  const writes = [];
  const env = await open('list', async (url, body, method) => {
    if (url === '/api/resume/resumes') return { items: [{ id: 17, title: '导入简历', revision: 1, status: 'review_ready' }] };
    if (url.endsWith('/candidates')) return { items: [{ id: 40, kind: 'import', status: 'pending', base_revision: 1,
      payload: { parsed: { personal: { name: '测试同学', email: 'student@example.test' }, education: [{ school: '甲学校' }, { school: '乙学校' }], skill: [{ name: '语言技能' }] } } }] };
    if (method === 'POST') { writes.push(body); return { ok: true }; }
    return {};
  });
  try {
    await env.page.getByRole('button', { name: '核对待确认内容' }).click();
    await env.page.locator('[data-import-section=skill]').uncheck();
    await env.page.locator('[data-import-personal=email]').uncheck();
    await env.page.locator('[data-import-owner=education][data-import-item="1"]').uncheck();
    await env.page.getByRole('button', { name: '确认导入选中资料' }).click();
    await env.page.waitForFunction(() => !document.querySelector('.rz-modal'));
    assert.deepEqual(writes[0].selected_sections, ['personal', 'education']);
    assert.deepEqual(writes[0].selected_items.education, [0]);
    assert.deepEqual(writes[0].selected_personal_fields, ['name']);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('career layouts at 360, 768, 1440 and 200 percent reflow expose final actions and restore keyboard focus', async () => {
  const nodes = Array.from({ length: 12 }, (_, i) => ({ tag: 'path-' + i, cat: 'general', name: '测试方向 ' + i, rec: 3,
    desc: '基于专业基础和个人兴趣进行探索。', reason: '请通过真实课程、实习与作品进一步核对。', know: ['沟通', '调研分析'], tl: [['入门', '助理']] }));
  const current = careerState({ phase: 'ready', session_status: 'ready', recommendation_source: 'baseline',
    network: { cats: [{ id: 'general', name: '专业方向', c1: '#63cbff' }], nodes, links: [] },
    tasks: { network: { status: 'failed', can_retry: true, phase_label: '详细网络暂未完成' } } });
  const env = await open('career', async url => url.endsWith('/initialize') ? current : {});
  try {
    for (const [width, height, name] of [[360, 800, 'career-360'], [768, 1024, 'career-768'], [1440, 1000, 'career-1440'], [720, 450, 'career-reflow-200pct']]) {
      await env.page.setViewportSize({ width, height });
      const last = env.page.locator('.career-direction').last(); await last.waitFor();
      await last.locator('button').last().scrollIntoViewIfNeeded();
      const metrics = await env.page.evaluate(() => {
        const a = document.querySelector('.career-direction:last-child button:last-child').getBoundingClientRect();
        const status = document.getElementById('career-task-status').getBoundingClientRect();
        return { button: { left: a.left, right: a.right, bottom: a.bottom }, statusTop: status.top, width: innerWidth, scroll: document.documentElement.scrollWidth };
      });
      assert.ok(metrics.button.bottom <= metrics.statusTop + 1, JSON.stringify({ width, metrics }));
      assert.ok(metrics.button.left >= 0 && metrics.button.right <= width && metrics.scroll <= width, JSON.stringify(metrics));
      const openDetail = last.locator('button').first(); await openDetail.focus(); await env.page.keyboard.press('Enter');
      assert.equal(await env.page.locator('.career-detail__close').evaluate(el => el === document.activeElement), true);
      await env.page.keyboard.press('Escape'); assert.equal(await openDetail.evaluate(el => el === document.activeElement), true);
      await last.locator('button').last().scrollIntoViewIfNeeded();
      await capture(env.page, name);
    }
    assert.equal(await env.page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), true);
    const duration = await env.page.locator('.career-direction').first().evaluate(el => getComputedStyle(el).animationDuration);
    assert.ok(duration === '0s' || duration === '1e-05s' || duration === '0.00001s', duration);
    await env.page.getByRole('button', { name: '职业偏好', exact: true }).focus(); await env.page.keyboard.press('Enter');
    await env.page.getByRole('button', { name: '保存偏好', exact: true }).focus(); await env.page.keyboard.press('Tab');
    assert.equal(await env.page.locator('.career-modal__close').evaluate(el => el === document.activeElement), true);
    await env.page.keyboard.press('Escape');
    await env.page.waitForFunction(() => document.activeElement.id === 'career-preferences');
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('snapshot draft publishes without live-palette validation and preserves draft when publish needs information', async () => {
  const calls = [];
  const env = await open('builder', async (url, body, method) => {
    calls.push({ url, body, method });
    if (url.endsWith('/palette')) return { personal: { name: '测试同学' }, personal_labels: {}, templates: [], self_intro: [], education: [], experience: [], skill: [], certificate: [] };
    if (url === '/api/resume/resumes' && method === 'POST') return { id: 88, revision: 1, status: 'draft' };
    if (url.endsWith('/publish')) return { httpStatus: 400, payload: { detail: '请先完善：联系方式' } };
    return {};
  });
  try {
    await env.page.locator('#rzTargetPosition').fill('课程研究助理');
    await env.page.getByRole('button', { name: '生成文件', exact: true }).click();
    await env.page.getByRole('dialog', { name: '还差一点就能生成' }).waitFor();
    assert.equal(calls.some(call => call.url.endsWith('/builder/validate')), false);
    assert.equal(calls.find(call => call.url.endsWith('/publish')).body.revision, 1);
    assert.match(await env.page.locator('#rzDraftStatus').textContent(), /草稿已保存/);
    assert.equal(await env.page.locator('#rzTargetPosition').inputValue(), '课程研究助理');
    assert.equal(await env.page.getByText('expected_position', { exact: false }).count(), 0);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('real job listings use verified source empty state and create personal targets without confusing directions', async () => {
  let populated = false; const writes = [];
  const env = await open('career', async (url, body, method) => {
    if (url.endsWith('/initialize')) return careerState({ phase: 'ready', network: { cats: [], nodes: [], links: [] } });
    if (url.endsWith('/job-postings')) return populated ? { items: [{ id: 22, title: '真实测试岗位', company: '测试企业', city: '南宁', source: '企业官网', source_url: 'https://example.test/jobs/22', checked_at: '2026-09-06T10:00:00', match: { hard_requirements: [{ text: '本科及以上', state: 'unknown' }] } }], total: 1 } : { items: [], empty_reason: 'no_verified_source' };
    if (url.endsWith('/target')) { writes.push(url); return { item: { id: 77 } }; }
    return {};
  });
  try {
    await env.page.getByRole('button', { name: '真实在招职位', exact: true }).click();
    await env.page.getByText('暂未接入已核验的职位来源', { exact: true }).waitFor();
    assert.equal(await env.page.getByRole('link', { name: '导入个人岗位描述并分析' }).isVisible(), true);
    populated = true; await env.page.getByRole('button', { name: '筛选职位' }).click();
    await env.page.getByText('真实测试岗位', { exact: true }).waitFor();
    assert.equal(await env.page.getByRole('link', { name: '查看来源 ↗' }).getAttribute('href'), 'https://example.test/jobs/22');
    await env.page.getByRole('button', { name: '保存为我的目标岗位' }).click();
    await env.page.getByRole('link', { name: '已保存 · 查看岗位条件与简历建议 →' }).waitFor();
    assert.deepEqual(writes, ['/api/career-path/job-postings/22/target']);
    assert.match(await env.page.getByRole('link', { name: '已保存 · 查看岗位条件与简历建议 →' }).getAttribute('href'), /job_id=77/);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('JD hard conditions retain uncertainty and insufficient extraction never displays a zero match score', async () => {
  const env = await open('job_targets', async (url, body, method) => {
    if (url.endsWith('/analyze')) return { item: { id: 9, target_position: '岗位条件测试', analysis: { coverage_score: 0, coverage_status: 'insufficient_extraction', hard_requirements: [
      { text: '本科及以上', state: 'unknown', importance: 'required', reason: '学历层次待确认' },
      { text: '有效资格证', state: 'failed', reason: '现有证书已过期' }, { text: '相关专业', state: 'met', reason: '用户填写的专业相关' }
    ] } } };
    return { items: [] };
  });
  try {
    await env.page.locator('#rzJobPosition').fill('岗位条件测试'); await env.page.locator('#rzJobDescription').fill('测试岗位条件原文，请保留学历和资格证的不确定性。');
    await env.page.getByRole('button', { name: '分析岗位要求' }).click();
    await env.page.getByText('识别不足，待核对', { exact: true }).waitFor();
    assert.equal(await env.page.locator('.rz-job-score').count(), 0);
    for (const text of ['待确认', '当前冲突', '材料有支持（自述待核验）']) assert.equal(await env.page.getByText(text, { exact: true }).count(), 1);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('education form takes degree registry from server and self-intro exposes cancellable durable task', async () => {
  const env = await open('section_education', async url => url.includes('/sections/') ? { items: [], meta: { education_degrees: ['高中', '本科', '硕士'] } } : {});
  try {
    await env.page.getByRole('button', { name: '+ 新建', exact: true }).click();
    const degree = env.page.locator('select[name=degree]'); await degree.waitFor();
    assert.deepEqual(await degree.locator('option').allTextContents(), ['待确认', '高中', '本科', '硕士']);
    assert.equal(await degree.inputValue(), ''); assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
  const writes = [];
  const intro = await open('section_self_intro', async (url, body, method) => {
    if (url.includes('/sections/')) return { items: [{ id: 33, title: '生成中的介绍', status: 'generating', revision: 2, active_job_id: 91 }] };
    if (url.endsWith('/job')) return { job: { status: 'running', can_cancel: true, phase_label: '正在整理资料' } };
    if (url.endsWith('/cancel')) { writes.push({ url, body }); return { ok: true }; }
    return {};
  });
  try {
    await intro.page.getByRole('button', { name: /AI 正在整理/ }).focus(); await intro.page.keyboard.press('Enter');
    await intro.page.getByRole('button', { name: '取消任务', exact: true }).click();
    await intro.page.waitForFunction(() => !document.querySelector('.rz-modal'));
    assert.deepEqual(writes, [{ url: '/api/resume/self-intro/33/job/cancel', body: { revision: 2 } }]);
    assert.deepEqual(intro.errors, []);
  } finally { await intro.context.close(); }
});

test('durable short suggestion resumes by job id and never replaces edited text before explicit review', async () => {
  let ready = false; const writes = [];
  const env = await open('section_self_intro', async (url, body, method) => {
    if (url.includes('/sections/')) return { items: [] };
    if (url.endsWith('/self-intro/optimize')) { writes.push(body); return { httpStatus: 202, payload: { ok: true, job: { id: 42, status: 'queued' }, kind: 'intro', profile_revision: 1 } }; }
    if (url.endsWith('/suggestions/jobs/42')) return { ok: true, job: { id: 42, status: ready ? 'succeeded' : 'running', cancellable: !ready }, profile_revision: 1, input_text: '原始介绍', ...(ready ? { result: { ok: true, content: '建议介绍，请核对。' } } : {}) };
    return {};
  });
  try {
    await env.page.getByRole('button', { name: '+ 新建' }).click();
    await env.page.locator('#rzIntroText').fill('原始介绍');
    await env.page.getByRole('button', { name: '✨ AI 优化', exact: true }).click();
    await env.page.getByRole('button', { name: '取消建议任务', exact: true }).waitFor();
    await env.page.keyboard.press('Escape'); await env.page.waitForFunction(() => document.querySelectorAll('.rz-modal').length === 1);
    await env.page.locator('#rzIntroText').fill('等待期间补充的新内容'); ready = true;
    await env.page.getByRole('button', { name: '查看上次建议', exact: true }).click();
    await env.page.getByRole('button', { name: '查看并核对建议', exact: true }).click();
    await env.page.getByRole('dialog', { name: '核对 AI 建议' }).waitFor();
    assert.equal(writes.length, 1); assert.equal(await env.page.locator('#rzIntroText').inputValue(), '等待期间补充的新内容');
    assert.equal(await env.page.getByText('等待期间补充的新内容', { exact: true }).count(), 1);
    await env.page.getByRole('button', { name: '采用建议', exact: true }).click();
    assert.equal(await env.page.locator('#rzIntroText').inputValue(), '建议介绍，请核对。');
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('export keeps version and page on 429, prevents duplicate request, and only downloads a successful file response', async () => {
  let calls = 0;
  const env = await open('list', async (url, body, method, query) => {
    if (url === '/api/resume/resumes') return { items: [{ id: 91, title: '下载测试', status: 'ready', revision: 5, render_revision: 4 }] };
    if (url.endsWith('/export')) {
      calls++; assert.equal(query.searchParams.get('revision'), '4');
      if (calls === 1) { await new Promise(resolve => setTimeout(resolve, 250)); return { httpStatus: 429, headers: { 'Retry-After': '10' }, payload: { detail: '转换处理中' } }; }
      return { contentType: 'application/pdf', headers: { 'Content-Disposition': "attachment; filename*=UTF-8''version-4.pdf" }, rawBody: '%PDF-1.4\n%%EOF' };
    }
    return {};
  });
  try {
    const downloads = []; env.page.on('download', download => downloads.push(download));
    await env.page.clock.install();
    await env.page.getByRole('link', { name: 'PDF', exact: true }).dblclick();
    await env.page.getByRole('dialog', { name: '转换处理中，稍后重试' }).waitFor();
    assert.equal(calls, 1); assert.equal(downloads.length, 0); assert.equal(env.context.pages().length, 1);
    assert.equal(await env.page.getByRole('button', { name: '10 秒后可重试', exact: true }).isDisabled(), true);
    await env.page.clock.fastForward(10000);
    const downloaded = env.page.waitForEvent('download');
    await env.page.getByRole('button', { name: '重试下载', exact: true }).click();
    assert.equal((await downloaded).suggestedFilename(), 'version-4.pdf');
    assert.equal(calls, 2); assert.equal(env.context.pages().length, 1); assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('accepted summary and grouped capabilities survive title save and remain directly editable', async () => {
  const writes = [];
  const resume = { id: 37, title: '原简历', revision: 1, target_position: '教师', template_key: 'classic', layout: { blocks: [{ type: 'tech_stack' }] },
    optimized_summary_md: '已采用的摘要', tech_stack: [{ group: '教学', items: ['英语教学'] }], content_snapshot: { personal: { name: '测试学生' } } };
  const env = await open('builder', async (url, body, method) => {
    if (url.endsWith('/palette')) return { personal: { name: '测试学生' }, templates: [], education: [], experience: [], self_intro: [], skill: [], certificate: [] };
    if (url.endsWith('/resumes/37') && method === 'GET') return { resume };
    if (url.endsWith('/resumes/37') && method === 'PUT') { writes.push(body); return { id: 37, revision: writes.length + 1 }; }
    return {};
  });
  try {
    await env.page.goto('http://career.test/resume/builder?edit=37');
    await env.page.locator('#rzResumeTitle').fill('仅修改标题'); await env.page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await env.page.getByText('草稿已保存 · 版本 2', { exact: true }).waitFor();
    assert.equal(writes[0].optimized_summary_md, '已采用的摘要'); assert.deepEqual(writes[0].tech_stack, resume.tech_stack);
    await env.page.getByRole('button', { name: '编辑本份文字' }).click();
    await env.page.locator('#rzSnapshotSummary').fill('我核对后的摘要'); await env.page.locator('#rzSnapshotCapabilities').fill('语言沟通：英语教学、课堂互动');
    await env.page.getByRole('button', { name: '应用到当前草稿' }).click();
    await env.page.getByRole('button', { name: '保存草稿', exact: true }).click(); await env.page.getByText('草稿已保存 · 版本 3', { exact: true }).waitFor();
    assert.equal(writes[1].optimized_summary_md, '我核对后的摘要'); assert.deepEqual(writes[1].tech_stack, [{ group: '语言沟通', items: ['英语教学', '课堂互动'] }]);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('compact resume polling preserves focused controls and fetches full content only on revision or status change', async () => {
  let ready = false, full = 0, compact = 0;
  const env = await open('list', async (url, body, method, query) => {
    if (url === '/api/resume/resumes') {
      const item = { id: 71, status: ready ? 'ready' : 'rendering', revision: 1, render_revision: ready ? 1 : 0, active_job_id: ready ? '' : '11' };
      if (query.searchParams.get('compact') === 'true') { compact++; return { items: [item] }; }
      full++; return { items: [{ ...item, title: '简历轮询测试', target_position: '教师', template_key: 'classic' }] };
    }
    return {};
  });
  try {
    const edit = env.page.getByRole('button', { name: '继续编辑', exact: true }); await edit.focus();
    await env.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(compact, 1); assert.equal(full, 1); assert.equal(await edit.evaluate(element => document.activeElement === element), true);
    ready = true; await env.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await env.page.getByRole('button', { name: '预览文件', exact: true }).waitFor();
    assert.equal(full, 2); assert.equal(compact, 2); assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('avatar upload carries revision, synchronizes it before saving, and preserves form text on conflict', async () => {
  const writes = [], uploads = [];
  const env = await open('personal', async (url, body, method, query, request) => {
    if (url === '/api/resume/personal' && method === 'GET') return { info: { name: '测试学生', email: 'student@example.com', expected_position: '教师', revision: 5 } };
    if (url === '/api/resume/personal' && method === 'POST') { writes.push(body); return { revision: 7 }; }
    if (url === '/api/resume/personal/avatar' && method === 'POST') {
      uploads.push(request.postData());
      return uploads.length === 1 ? { avatar_url: '/api/resume/personal/avatar?v=1', revision: 6 } : { httpStatus: 409, payload: { detail: { message: '资料已更新' } } };
    }
    return {};
  });
  try {
    await env.page.waitForFunction(() => document.querySelector('[name=name]').value === '测试学生');
    const file = { name: 'avatar.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4WkAAAAASUVORK5CYII=', 'base64') };
    await env.page.locator('#rzAvatarInput').setInputFiles(file);
    await env.page.getByText('头像已更新', { exact: true }).waitFor();
    assert.match(uploads[0], /name="revision"\r\n\r\n5\r\n/);
    await env.page.locator('[name=name]').fill('等待时补充姓名');
    await env.page.getByRole('button', { name: '保存个人信息', exact: true }).click();
    await env.page.getByText('已保存', { exact: true }).waitFor();
    assert.equal(writes[0].revision, 6);
    await env.page.locator('#rzAvatarInput').setInputFiles(file);
    await env.page.getByRole('dialog', { name: '这份资料有了新版本' }).waitFor();
    assert.match(uploads[1], /name="revision"\r\n\r\n7\r\n/);
    assert.equal(await env.page.locator('[name=name]').inputValue(), '等待时补充姓名');
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});

test('unavailable job target keeps a persistent recovery message and leaves own form usable', async () => {
  const env = await open('job_targets', async (url) => {
    if (url === '/api/resume/job-targets') return { items: [] };
    if (url === '/api/resume/job-targets/999') return { httpStatus: 404, payload: { detail: '岗位记录不存在或无权访问' } };
    return {};
  });
  try {
    await env.page.goto('http://career.test/resume/job_targets?job_id=999');
    await env.page.getByRole('heading', { name: '这条岗位分析暂不可用', exact: true }).waitFor();
    assert.equal(await env.page.getByRole('button', { name: '分析岗位要求', exact: true }).isEnabled(), true);
    assert.deepEqual(env.errors, []);
  } finally { await env.context.close(); }
});
