/* Real browser contract regression; run node --test tests/frontend/attendance_reports_browser.test.cjs */
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
after(async () => { await browser?.close(); });

// The page carries a `_attendance_reports_lq_enabled` switch. Resolve it the way
// Jinja does before the generic strip below, otherwise both branches survive and
// the LQ filter form - whose lq_field() calls are {{ }} expressions that strip to
// nothing - wins every [data-att-filters] lookup. This fixture renders the legacy
// branch; the LQ branch needs real macro output and is covered by the S6 e2e.
const SWITCH = /{% if _attendance_reports_lq_enabled %}([\s\S]*?)(?:{% else %}([\s\S]*?))?{% endif %}/g;
function fixture(id = '', classroom = false) {
    const template = fs.readFileSync(path.join(workspace, 'templates/manage/attendance_reports.html'), 'utf8');
    let body = template.match(/{% block content %}([\s\S]*?){% endblock %}/)[1]
        .replace(SWITCH, (_, on, off) => off || '')
        .replace(/{{ report_id\|default\('', true\) }}/g, id).replace(/{{ class_offering_id\|default\('', true\) }}/g, '')
        .replace(/{{[\s\S]*?}}/g, '').replace(/{%[\s\S]*?%}/g, '');
    if (classroom) body = '<div data-attendance-classroom-panel data-class-offering-id="9"></div>';
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>*{box-sizing:border-box}body{margin:0;padding:24px;font-family:Arial,"Microsoft YaHei",sans-serif}button,input,select,textarea{font:inherit}h3{margin:0}a{color:#087f82}</style><link rel="stylesheet" href="/static/css/attendance_reports.css"></head><body>${body}<script type="module">${classroom ? "import {initClassroomAttendancePanel} from '/static/js/attendance_reports.js';window.attContainer=document.querySelector('[data-attendance-classroom-panel]');window.attControl=initClassroomAttendancePanel(window.attContainer);window.attInit=initClassroomAttendancePanel;" : "import '/static/js/attendance_reports.js';"}</script></body></html>`;
}
async function open({ detail = false, classroom = false, viewport = { width: 1440, height: 1100 }, search = '', runState = 'needs_review' } = {}) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' }), page = await context.newPage();
    const report = { id: 7, binding_id: 3, revision: 2, course_name: '动态Web程序设计', course_code: 'TEST-01', teaching_class_name: '合成教学班', academic_year: '2025-2026', academic_term: 2, source_state: 'cached', parse_state: runState, source_version_id: 11, confirmed_parse_run_id: runState === 'confirmed' ? 21 : null, student_count: 2, session_count: 2, updated_at: '2026-09-13T09:00:00', deleted_at: null };
    const run = { id: 21, source_version_id: 11, run_no: 1, state: runState, revision: 1, ai_used: true, validation: { can_confirm: runState === 'validated', blockers: runState === 'needs_review' ? [{ code: 'unknown', message: '有1条待核实记录' }] : [], warnings: [], student_count: 2, session_count: 2, cell_count: 4, unknown_count: runState === 'needs_review' ? 1 : 0, conflict_count: 0 }, coverage: { processed_pages: 2, total_pages: 2 } };
    const binding = { id: 3, revision: 4, course_name: report.course_name, course_code: report.course_code, teaching_class_name: report.teaching_class_name, academic_year: '2025-2026', academic_term: 2 };
    const students = [{ id: 101, row_index: 1, source_name: '合成学生甲', student_number: '00001', source_class_name: '合成一班', identity_state: 'matched', summary: { checked: 1, absent: 0, sick_leave: 0, personal_leave: 0, unknown: 1, applicable: 2, completeness_rate: 50, attendance_rate: null } }, { id: 102, row_index: 29, source_name: '合成学生乙', student_number: '00002', source_class_name: '合成一班', identity_state: 'matched', summary: { checked: 1, absent: 1, sick_leave: 0, personal_leave: 0, unknown: 0, applicable: 2, completeness_rate: 100, attendance_rate: 50 } }];
    const sessions = [{ id: 201, column_index: 1, source_header: '03-09 19:30', mapping_state: 'matched' }, { id: 202, column_index: 2, source_header: '03-12 09:30', mapping_state: 'matched' }];
    const cells = [{ id: 301, student_row_id: 101, session_column_id: 201, normalized_status: 'CHECKED', raw_text: '出勤', evidence_page: 1, quality_state: 'verified' }, { id: 302, student_row_id: 101, session_column_id: 202, normalized_status: 'UNKNOWN', raw_text: '', evidence_page: 2, quality_state: 'unknown' }, { id: 303, student_row_id: 102, session_column_id: 201, normalized_status: 'CHECKED', raw_text: '出勤', evidence_page: 2, quality_state: 'verified' }, { id: 304, student_row_id: 102, session_column_id: 202, normalized_status: 'UNCHECKED', raw_text: '缺课', evidence_page: 2, quality_state: 'verified' }];
    students[0].mapping_candidates = [{ id: 801, label: '00001 · 合成学生甲' }];
    sessions[0].mapping_candidates = [{ id: 901, label: '2026-03-09 第1次合成课' }];
    const env = { context, page, report, run, binding, students, sessions, cells, requests: [], writes: [], errors: [], failList: false, failReview: 0, reviewDelay: 0, emptyBindings: false, archiveEnabled: true, parseEnabled: true, listDelay: 0, listTotal: 67 };
    page.on('pageerror', error => env.errors.push(error.message));
    const json = (route, data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    await page.route('**/*', async route => {
        const req = route.request(), url = new URL(req.url());
        if (url.pathname.startsWith('/static/')) { const local = path.join(workspace, url.pathname.slice(1)); return route.fulfill({ contentType: local.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(local) }); }
        if (!url.pathname.startsWith('/api/')) return route.fulfill({ contentType: 'text/html', body: fixture(detail ? '7' : '', classroom) });
        env.requests.push({ path: url.pathname, params: Object.fromEntries(url.searchParams), method: req.method() });
        if (url.pathname.endsWith('source.pdf')) return route.fulfill({ contentType: 'text/html', body: '<p>受鉴权原件测试预览</p>' });
        const endpoint = url.pathname.replace('/api/attendance-reports', '');
        let body;
        if (!['GET', 'HEAD'].includes(req.method())) { body = req.postDataJSON(); env.writes.push({ path: endpoint, method: req.method(), body }); }
        if (endpoint === '' && req.method() === 'GET') { if (env.listDelay) await new Promise(resolve => setTimeout(resolve, env.listDelay)); if (env.failList) return json(route, { detail: '测试读取故障' }, 503); return json(route, { items: [{ ...report, course_name: url.searchParams.get('q') || report.course_name }], total: env.listTotal, page: Number(url.searchParams.get('page') || 1), page_size: Number(url.searchParams.get('page_size') || 25) }); }
        if (endpoint === '/options') return json(route, { years: ['2025-2026', '2024-2025'], terms: [1, 2], courses: [{ value: 'TEST-01', label: '动态Web程序设计' }], teaching_classes: [{ value: '3', label: '合成教学班' }] });
        if (endpoint === '/source-options') return json(route, { bindings: env.emptyBindings ? [] : [binding], credential_available: true, year: '2025-2026', term: 2, archive_enabled: env.archiveEnabled, parse_enabled: env.parseEnabled });
        if (endpoint === '/source-options/refresh') return json(route, { items: [{ ...binding, remote_schedule_id: 'opaque-task', source_token: 'signed-source-test' }] });
        if (endpoint === '/source-bindings') return json(route, { binding });
        if (endpoint === '/exports') return json(route, { report_id: 7, source_version_id: 11, job_id: 51, report_revision: report.revision }, 202);
        if (endpoint === '/7' && req.method() === 'GET') return json(route, { report, binding, versions: [{ id: 11, version_no: 1, source_state: 'cached', source_file_hash: 'a'.repeat(64), fetched_at: '2026-09-13T08:30:00', source_page_count: 2 }], runs: [run], jobs: [], active_run_id: 21 });
        if (endpoint.endsWith('/students')) { const q = url.searchParams.get('q') || ''; const filtered = students.filter(row => !q || row.source_name.includes(q) || row.student_number.includes(q)); return json(route, { items: filtered, total: filtered.length, page: 1, page_size: 25 }); }
        if (endpoint.endsWith('/sessions')) return json(route, { items: sessions, total: 2 });
        if (endpoint.endsWith('/cells')) { const ids = (url.searchParams.get('student_ids') || '').split(','); return json(route, { items: cells.filter(cell => ids.includes(String(cell.student_row_id))), total: cells.length }); }
        if (endpoint.endsWith('/reviews')) return json(route, { items: [], total: 0 });
        if (endpoint.endsWith('/review')) { if (env.reviewDelay) await new Promise(resolve => setTimeout(resolve, env.reviewDelay)); if (env.failReview) return json(route, { detail: '解析版本已更新' }, env.failReview); const rows = body.target_type === 'cell' ? cells : body.target_type === 'student' ? students : sessions; Object.assign(rows.find(row => row.id === body.target_id), body.changes); run.revision += 1; run.validation.can_confirm = true; run.validation.unknown_count = 0; run.validation.blockers = []; run.state = 'validated'; report.parse_state = 'validated'; return json(route, { run, validation: run.validation }); }
        if (endpoint.endsWith('/confirm')) { run.state = 'confirmed'; run.revision += 1; report.revision += 1; report.confirmed_parse_run_id = 21; report.parse_state = 'confirmed'; return json(route, { run }); }
        if (endpoint === '/7' && req.method() === 'DELETE') { report.deleted_at = '2026-09-13T11:00:00'; report.revision += 1; return json(route, { report }); }
        if (endpoint.endsWith('/restore')) { report.deleted_at = null; report.revision += 1; return json(route, { report }); }
        if (endpoint.endsWith('/parse-runs')) return json(route, { report_id: 7, source_version_id: 11, parse_run_id: 21, job_id: 52 }, 202);
        return json(route, { detail: 'Unexpected test API ' + endpoint }, 404);
    });
    await page.goto('http://attendance.test/manage/archive/attendance-reports' + (detail ? '/7' : '') + search);
    if (classroom) await page.locator('[data-source-form]').waitFor();
    else if (detail) await page.locator('[data-att-version]').waitFor();
    else await page.locator('.att-list-table').waitFor();
    return env;
}
async function capture(page, name) { if (!process.env.ATTENDANCE_FRONTEND_QA_DIR) return; fs.mkdirSync(process.env.ATTENDANCE_FRONTEND_QA_DIR, { recursive: true }); await page.screenshot({ path: path.join(process.env.ATTENDANCE_FRONTEND_QA_DIR, name + '.png'), fullPage: true }); }

test('list restores URL filters and exact pagination; errors keep filters and can retry', async () => {
    const env = await open({ search: '?year=2025-2026&term=2&page=2&q=课程甲' });
    try {
        assert.equal(await env.page.locator('[name=year]').first().inputValue(), '2025-2026');
        assert.match(await env.page.locator('[data-att-count]').textContent(), /67/);
        assert.match(await env.page.locator('[data-att-list-pagination]').textContent(), /第 2 \/ 3 页/);
        await env.page.locator('[data-scope=list][data-page="3"]').click();
        await env.page.waitForURL(/page=3/);
        env.failList = true; await env.page.locator('[name=status]').selectOption('failed');
        await env.page.getByText('档案暂时无法读取').waitFor();
        assert.equal(await env.page.locator('[name=q]').inputValue(), '课程甲');
        env.failList = false; await env.page.locator('[data-att-action=reload-list]').click(); await env.page.locator('.att-list-table').waitFor();
        assert.match(env.page.url(), /status=failed/); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
test('cached original remains downloadable when AI fails; detail reparse uses only cached source', async () => {
    const env = await open({ detail: true, runState: 'failed' });
    try {
        const href = await env.page.getByRole('link', { name: '下载原件', exact: true }).getAttribute('href'); assert.match(href, /versions\/11\/source\.pdf\?download=1/);
        await env.page.locator('[data-att-action=reparse]').click();
        await env.page.getByText('已用缓存原件开始新解析，旧确认结果继续可用。').waitFor();
        assert.equal(env.writes.filter(row => row.path === '/exports').length, 0); assert.equal(env.writes.filter(row => row.path.endsWith('/parse-runs')).length, 1);
    } finally { await env.context.close(); }
});
test('matrix search fetches cells by returned student IDs, keeping sparse source rows accurate', async () => {
    const env = await open({ detail: true });
    try {
        await env.page.locator('[data-att-tab=matrix]').click(); await env.page.locator('[data-att-action=evidence]').first().waitFor();
        await env.page.locator('[data-att-matrix-query]').fill('00002');
        await env.page.waitForFunction(() => document.querySelectorAll('.att-matrix tbody tr').length === 1 && document.querySelector('.att-matrix tbody').textContent.includes('合成学生乙'));
        assert.equal(env.requests.filter(row => row.path.endsWith('/cells')).at(-1).params.student_ids, '102');
        assert.match(await env.page.locator('.att-matrix tbody').textContent(), /缺课/);
        assert.equal(await env.page.locator('.att-matrix .att-status-UNKNOWN').count(), 0);
        await env.page.locator('[data-att-tab=students]').click(); await env.page.locator('[data-att-students-content] tbody').waitFor();
        const rows = await env.page.locator('[data-att-students-content] tbody tr').allTextContents(); assert.match(rows[0], /待核实/); assert.match(rows[1], /50\.0%/); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
test('review 409 preserves draft and source page; successful save validates and confirmation uses fresh revisions', async () => {
    const env = await open({ detail: true });
    try {
        await env.page.locator('[data-att-tab=matrix]').click(); await env.page.locator('[data-att-action=evidence][data-id="302"]').click();
        assert.match(await env.page.locator('[data-att-evidence-frame]').getAttribute('src'), /#page=2/);
        await env.page.locator('[name=normalized_status]').selectOption('UNCHECKED'); await env.page.locator('[name=reason]').fill('核对原件第2页为空白，暂按缺课候选核对');
        env.failReview = 409; await env.page.locator('[data-att-save-review]').click(); await env.page.getByText(/本次输入已保留/).waitFor();
        assert.equal(await env.page.locator('[name=normalized_status]').inputValue(), 'UNCHECKED'); assert.match(await env.page.locator('[name=reason]').inputValue(), /第2页/);
        env.failReview = 0; await env.page.locator('[data-att-save-review]').click(); await env.page.locator('[data-att-evidence-dialog]').waitFor({ state: 'hidden' });
        await env.page.locator('[data-att-action=confirm-run]').click(); await env.page.locator('[data-att-confirm-dialog] [value=confirm]').click();
        await env.page.getByText('当前解析版本已确认。').waitFor();
        const confirmation = env.writes.find(row => row.path.endsWith('/confirm')); assert.equal(confirmation.body.expected_run_revision, 2); assert.equal(confirmation.body.expected_report_revision, 2); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
test('confirmed version is read-only; delete/restore keeps original and changes actions', async () => {
    const env = await open({ detail: true, runState: 'confirmed' });
    try {
        await env.page.locator('[data-att-tab=matrix]').click(); await env.page.locator('[data-att-action=evidence]').first().click();
        assert.equal(await env.page.locator('[data-att-save-review]').isDisabled(), true); await env.page.locator('[data-att-action=close-evidence]').click();
        await env.page.locator('[data-att-action=delete-report]').click(); await env.page.locator('[data-att-confirm-dialog] [value=confirm]').click(); await env.page.locator('[data-att-action=restore-report]').waitFor();
        assert.equal(await env.page.getByRole('link', { name: '下载原件', exact: true }).count(), 1);
        await env.page.locator('[data-att-action=restore-report]').click(); await env.page.locator('[data-att-action=delete-report]').waitFor(); assert.equal(env.writes.filter(row => row.method === 'DELETE').length, 1);
    } finally { await env.context.close(); }
});
test('classroom panel initializes once, does not query remote until requested, then binds signed source and exports all', async () => {
    const env = await open({ classroom: true });
    try {
        await env.page.waitForFunction(() => document.querySelector('[name=year]').value === '2025-2026');
        assert.equal(env.writes.length, 0);
        assert.equal(await env.page.evaluate(() => window.attInit(window.attContainer) === window.attControl), true);
        await env.page.locator('[data-att-action=refresh-source]').click(); await env.page.locator('[name=source][value="c:0"]').check();
        await env.page.locator('[data-att-action=export-source]').click(); await env.page.getByRole('link', { name: '前往签到统计表查看进度' }).waitFor();
        assert.deepEqual(env.writes.find(row => row.path === '/source-bindings').body, { source_token: 'signed-source-test', class_offering_id: 9 });
        const exported = env.writes.find(row => row.path === '/exports').body; assert.equal(exported.binding_id, 3); assert.equal(exported.expected_binding_revision, 4); assert.equal(Object.hasOwn(exported, 'ids'), false);
        await env.page.evaluate(() => window.attControl.deactivate()); assert.equal(env.writes.some(row => row.path.includes('/cancel')), false); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
test('mobile list and detail remain contained; keyboard activates detail tabs', async () => {
    const list = await open({ viewport: { width: 390, height: 844 } });
    try { assert.equal(await list.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await capture(list.page, 'attendance-list-390'); } finally { await list.context.close(); }
    const env = await open({ detail: true, viewport: { width: 390, height: 844 } });
    try {
        await env.page.locator('[data-att-tab=overview]').focus(); await env.page.keyboard.press('ArrowRight'); await env.page.keyboard.press('Enter'); await env.page.locator('.att-matrix tbody').waitFor();
        assert.equal(await env.page.locator('[data-att-tab=matrix]').getAttribute('aria-selected'), 'true');
        assert.equal(await env.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); await capture(env.page, 'attendance-detail-390'); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('student and session mapping submits server candidates and never invents matched state', async () => {
    const env = await open({ detail: true });
    try {
        await env.page.locator('[data-att-tab=matrix]').click();
        await env.page.locator('[data-att-action=review-student][data-id="101"]').click();
        await env.page.locator('[name=local_student_id]').selectOption('801');
        await env.page.locator('[name=reason]').fill('核对原件学号与本课堂学生一致');
        await env.page.locator('[data-att-save-review]').click();
        await env.page.locator('[data-att-evidence-dialog]').waitFor({ state: 'hidden' });
        const studentWrite = env.writes.find(row => row.body.target_type === 'student').body;
        assert.equal(studentWrite.changes.local_student_id, 801);
        assert.equal(Object.hasOwn(studentWrite.changes, 'identity_state'), false);
        await env.page.locator('[data-att-action=review-session][data-id="201"]').click();
        await env.page.locator('[name=local_session_id]').selectOption('901');
        await env.page.locator('[name=reason]').fill('原件日期对应本课堂课次');
        await env.page.locator('[data-att-save-review]').click();
        await env.page.locator('[data-att-evidence-dialog]').waitFor({ state: 'hidden' });
        const sessionWrite = env.writes.find(row => row.body.target_type === 'session').body;
        assert.equal(sessionWrite.changes.local_session_id, 901);
        assert.equal(Object.hasOwn(sessionWrite.changes, 'mapping_state'), false);
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('not-applicable requires evidence; API discrepancy requires an explicit resolution', async () => {
    const env = await open({ detail: true });
    try {
        env.cells[1].api_status = 'CHECKED'; env.cells[1].quality_state = 'conflict';
        await env.page.locator('[data-att-tab=matrix]').click();
        await env.page.locator('[data-att-action=evidence][data-id="302"]').click();
        await env.page.locator('[name=normalized_status]').selectOption('NOT_APPLICABLE');
        assert.equal(await env.page.locator('[name=applicability_evidence]').getAttribute('required'), '');
        await env.page.locator('[name=reason]').fill('按名册核对该生当时尚未转入');
        await env.page.locator('[data-att-save-review]').click();
        assert.equal(env.writes.length, 0);
        await env.page.locator('[name=applicability_evidence]').fill('合成测试名册：该生在第二次点名后才转入');
        await env.page.locator('[name=quality_state]').selectOption('resolved_historical_difference');
        await env.page.locator('[data-att-save-review]').click();
        await env.page.locator('[data-att-evidence-dialog]').waitFor({ state: 'hidden' });
        const write = env.writes.find(row => row.path.endsWith('/review')).body;
        assert.equal(write.changes.quality_state, 'resolved_historical_difference');
        assert.match(write.changes.applicability_evidence, /转入/); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('rollout switches keep historical access while pausing classroom exports', async () => {
    const env = await open({ classroom: true });
    try {
        env.archiveEnabled = false;
        await env.page.evaluate(() => { window.attControl.deactivate(); window.attControl.activate(); });
        await env.page.getByText('当前暂停新导出，已有档案和原件仍可查看。').waitFor();
        assert.equal(await env.page.locator('[data-att-action=export-source]').isVisible(), false);
        assert.equal(await env.page.getByRole('link', { name: '下载原件', exact: true }).isVisible(), true);
        env.archiveEnabled = true; env.parseEnabled = false;
        await env.page.evaluate(() => { window.attControl.deactivate(); window.attControl.activate(); });
        await env.page.getByRole('button', { name: '导出并缓存原件', exact: true }).waitFor();
        assert.equal(env.writes.length, 0); assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
