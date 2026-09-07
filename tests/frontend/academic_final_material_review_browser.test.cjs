/* Run: node --test tests/frontend/academic_final_material_review_browser.test.cjs
 * Real browser and production modules, with isolated material/signature APIs. */
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

function fixture() {
    const template = fs.readFileSync(path.join(workspace, 'templates/manage/academic_final_materials.html'), 'utf8');
    const body = template.match(/{% block content %}([\s\S]*?){% endblock %}/)[1]
        .replaceAll('{{ document_type }}', 'academic_exam_analysis')
        .replaceAll('{{ document_type_label }}', '试卷分析表')
        .replace(/{{[\s\S]*?}}/g, '').replace(/{%[\s\S]*?%}/g, '');
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
        <style>*{box-sizing:border-box}body{margin:0;padding:24px;font-family:Arial,"Microsoft YaHei",sans-serif}button,input,select,textarea{font:inherit}</style>
        <link rel="stylesheet" href="/static/css/academic_final_materials.css"><link rel="stylesheet" href="/static/css/signature_point_workflow.css"></head><body>${body}
        <script>window.showMessage=(text,kind)=>{window.messages=window.messages||[];window.messages.push({text,kind})};</script>
        <script type="module" src="/static/js/academic_final_materials.js"></script></body></html>`;
}

async function open(extra = {}, viewport = { width: 1440, height: 1100 }) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = [], writes = [], regenerations = [];
    page.on('pageerror', error => errors.push(error.message));
    const record = {
        id: 17, updated_at: '2026-09-07T12:00:00.000001', preview_url: '/preview/17?v=1', structured: { analysis_text: '学生已掌握基础知识，后续加强综合应用。' },
        fields: { course_name: '计算机网络实验', class_name: '软工2303班', teacher_name: '测试教师', academic_year: '2025-2026', semester: '2',
            proposition_form: '教师组题', exam_form: '闭卷', separate_teaching_exam: '否', course_nature: '必修', marking_form: '本人阅卷', ...extra },
    };
    const signatures = [
        { id: 1, subject_name: '测试系主任', signature_kind: 'personal', scope_label: '系部可见', identity_match: true },
        { id: 2, subject_name: '测试院长', signature_kind: 'personal', scope_label: '学院可见', identity_match: true },
        { id: 3, subject_name: '已阅', signature_kind: 'stamp', scope_label: '平台可见', identity_match: true },
    ];
    const env = { context, page, errors, writes, regenerations, record, failNextPatch: false, delayNextPatch: 0 };
    let version = 1;
    const advanceVersion = () => {
        version += 1;
        record.updated_at = '2026-09-07T12:00:00.' + String(version).padStart(6, '0');
        record.preview_url = '/preview/17?v=' + version;
    };
    await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname.startsWith('/static/')) {
            const local = path.join(workspace, url.pathname.slice(1));
            return route.fulfill({ status: 200, contentType: local.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(local) });
        }
        if (url.pathname.startsWith('/api/')) {
            let data = {};
            if (url.pathname === '/api/academic-final-materials') {
                data = { items: [{ id: 'batch-1', record_id: 17, sync_status: 'completed', course_name: record.fields.course_name,
                    class_name: record.fields.class_name, teaching_class_name: record.fields.class_name, edit_state: {}, preview_url: record.preview_url }] };
            } else if (url.pathname === '/api/academic-final-materials/batch-1') {
                if (request.method() === 'PATCH') {
                    const payload = request.postDataJSON();
                    writes.push(payload);
                    if (payload.expected_updated_at !== record.updated_at) {
                        return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '材料已在其他窗口更新，请重新打开编辑窗口后再试。' }) });
                    }
                    if (env.delayNextPatch) {
                        await new Promise(resolve => setTimeout(resolve, env.delayNextPatch));
                        env.delayNextPatch = 0;
                    }
                    if (env.failNextPatch) {
                        env.failNextPatch = false;
                        return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '测试：文档更新失败' }) });
                    }
                    const { document_type, expected_updated_at, ...fields } = payload;
                    for (const key of ['department_review_opinion', 'dean_review_opinion']) {
                        if (Object.hasOwn(fields, key)) {
                            fields[key] = fields[key].replace(/\s+/g, ' ').trim();
                            fields[key + '_source'] = 'explicit';
                        }
                    }
                    Object.assign(record.fields, fields);
                    advanceVersion();
                    data = { status: 'success', record, message: '已更新文档' };
                } else data = { analysis: record };
            } else if (url.pathname.endsWith('/regenerate-analysis')) {
                const payload = request.postDataJSON();
                regenerations.push(payload);
                if (payload.expected_updated_at !== record.updated_at) {
                    return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '材料已更新，请重新打开。' }) });
                }
                record.structured.analysis_text = '重新生成的分析：加强综合应用，改进课堂练习。';
                advanceVersion();
                data = { status: 'success', analysis_text: record.structured.analysis_text, record, message: '分析已重新生成' };
            } else if (url.pathname.includes('/api/signatures/points/')) {
                const department = url.pathname.includes('department_review_signature');
                const ids = record.fields[department ? 'department_signature_ids' : 'dean_signature_ids'] || [];
                data = { point: { required_identity_labels: [department ? '系主任' : '院长'] }, material: { label: '试卷分析表 · 计算机网络实验 · 软工2303班' },
                    signatures, usable_signatures: signatures, selected_signature_ids: ids };
            }
            return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
        }
        if (url.pathname.startsWith('/preview/')) return route.fulfill({ status: 200, contentType: 'text/html', body: '<p>文档预览</p>' });
        return route.fulfill({ status: 200, contentType: 'text/html', body: fixture() });
    });
    await page.goto('http://afm.test/materials');
    await page.evaluate(() => {
        window.messages = [];
        window.showMessage = (text, kind) => window.messages.push({ text, kind });
    });
    await page.locator('[data-afm-edit]').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-spw-available]').length === 2 && !document.querySelector('[data-afm-save]').disabled);
    return env;
}

async function capture(page, name) {
    if (!process.env.AFM_FRONTEND_QA_DIR) return;
    const directory = path.resolve(process.env.AFM_FRONTEND_QA_DIR);
    fs.mkdirSync(directory, { recursive: true });
    await page.screenshot({ path: path.join(directory, name + '.png') });
}
const departmentInput = '[data-afm-review-opinion="department_review_opinion"]';
const deanInput = '[data-afm-review-opinion="dean_review_opinion"]';
const departmentPoint = '[data-afm-department-signature-point]';
const deanPoint = '[data-afm-dean-signature-point]';

test('unsigned suggestions are not persisted; custom opinion and explicit blank round-trip through full save', async () => {
    const env = await open();
    try {
        assert.equal(await env.page.locator(departmentInput).inputValue(), '已核');
        assert.equal(await env.page.locator(deanInput).inputValue(), '同意');
        await env.page.locator('[data-afm-save]').click();
        await env.page.locator('[data-afm-editor-dialog]').waitFor({ state: 'hidden' });
        assert.equal(Object.hasOwn(env.writes[0], 'department_review_opinion'), false);
        assert.equal(Object.hasOwn(env.writes[0], 'dean_review_opinion'), false);
        await env.page.locator('[data-afm-edit]').click();
        await env.page.locator(departmentInput).fill('已核，建议加强综合练习');
        await env.page.locator('[data-afm-clear-opinion="dean_review_opinion"]').click();
        assert.equal(await env.page.locator('[data-afm-preview-current]').isDisabled(), true);
        assert.match(await env.page.locator('[data-afm-editor-status]').textContent(), /审核意见待保存/);
        await env.page.locator('[data-afm-save]').click();
        await env.page.locator('[data-afm-editor-dialog]').waitFor({ state: 'hidden' });
        assert.equal(env.writes[1].department_review_opinion, '已核，建议加强综合练习');
        assert.equal(env.writes[1].dean_review_opinion, '');
        await env.page.locator('[data-afm-edit]').click();
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        assert.equal(await env.page.locator(departmentInput).inputValue(), '已核，建议加强综合练习');
        assert.equal(await env.page.locator(deanInput).inputValue(), '');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('signature confirmation atomically saves its opinion, locks writes, and uses the new preview version', async () => {
    const env = await open();
    try {
        await env.page.locator(departmentInput).fill('已核');
        await env.page.locator(`${departmentPoint} [data-spw-available]`).selectOption('1');
        assert.equal(await env.page.locator('[data-afm-save]').isDisabled(), true);
        env.delayNextPatch = 350;
        await env.page.locator(`${departmentPoint} [data-spw-confirm]`).click();
        assert.equal(await env.page.locator(deanInput).isDisabled(), true);
        assert.equal(await env.page.locator(deanPoint).evaluate(el => el.inert), true);
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        assert.deepEqual(env.writes[0], { document_type: 'academic_exam_analysis', expected_updated_at: '2026-09-07T12:00:00.000001', department_signature_ids: [1], department_review_opinion: '已核' });
        assert.match(await env.page.locator(`${departmentPoint} [data-spw-area]`).getAttribute('class'), /is-confirmed/);
        assert.equal(await env.page.locator('[data-afm-preview-current]').isDisabled(), false);
        await env.page.locator('[data-afm-preview-current]').click();
        assert.equal(await env.page.locator('[data-afm-preview-frame]').getAttribute('src'), '/preview/17?v=2');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('failed confirmation preserves draft and retry; untouched legacy stamp remains a stamp until explicitly cleared', async () => {
    const env = await open({ department_signature_ids: [3, 1], department_review_stamp_ids: [3], department_personal_signature_ids: [1] });
    try {
        assert.equal(await env.page.locator(departmentInput).inputValue(), '');
        assert.match(await env.page.locator('[data-afm-opinion-status="department_review_opinion"]').textContent(), /沿用批语章/);
        await env.page.locator('[data-afm-save]').click();
        await env.page.locator('[data-afm-editor-dialog]').waitFor({ state: 'hidden' });
        assert.equal(Object.hasOwn(env.writes[0], 'department_review_opinion'), false);
        assert.deepEqual(env.writes[0].department_signature_ids, [3, 1]);
        await env.page.locator('[data-afm-edit]').click();
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        await env.page.locator('[data-afm-clear-opinion="department_review_opinion"]').click();
        assert.equal(await env.page.locator('[data-afm-preview-current]').isDisabled(), true);
        await env.page.locator(`${departmentPoint} [data-spw-available]`).selectOption('2');
        env.failNextPatch = true;
        await env.page.locator(`${departmentPoint} [data-spw-confirm]`).click();
        await env.page.waitForFunction(() => window.messages?.some(item => /测试：文档更新失败/.test(item.text)));
        assert.equal(await env.page.locator(`${departmentPoint} [data-spw-confirm]`).isDisabled(), false);
        assert.equal(await env.page.locator('[data-afm-save]').isDisabled(), true);
        assert.equal(await env.page.locator(departmentInput).inputValue(), '');
        await env.page.locator(`${departmentPoint} [data-spw-confirm]`).click();
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        assert.equal(env.writes[2].department_review_opinion, '');
        assert.deepEqual(env.writes[2].department_signature_ids, [3, 1, 2]);
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('legacy inferred default yields to a chosen stamp and normalized opinions settle after confirmation', async () => {
    const env = await open({ department_signature_ids: [1], department_review_opinion: '已核', department_review_opinion_source: 'legacy_default' });
    try {
        assert.match(await env.page.locator('[data-afm-opinion-status="department_review_opinion"]').textContent(), /默认批语/);
        await env.page.locator(`${departmentPoint} [data-spw-available]`).selectOption('3');
        assert.equal(await env.page.locator(departmentInput).inputValue(), '');
        await env.page.locator(`${departmentPoint} [data-spw-confirm]`).click();
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        assert.equal(Object.hasOwn(env.writes[0], 'department_review_opinion'), false);
        assert.deepEqual(env.writes[0].department_signature_ids, [1, 3]);
        await env.page.locator(departmentInput).fill('  已核   请完善教学措施  ');
        await env.page.locator(`${departmentPoint} [data-spw-available]`).selectOption('2');
        await env.page.locator(`${departmentPoint} [data-spw-confirm]`).click();
        await env.page.waitForFunction(() => !document.querySelector('[data-afm-save]').disabled);
        assert.equal(env.writes[1].expected_updated_at, '2026-09-07T12:00:00.000002');
        assert.equal(await env.page.locator(departmentInput).inputValue(), '已核 请完善教学措施');
        assert.equal(await env.page.locator('[data-afm-preview-current]').isDisabled(), false);
        assert.equal(await env.page.locator('[data-afm-editor-status]').textContent(), '');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('regeneration advances the record version while preserving unsaved opinion drafts', async () => {
    const env = await open();
    try {
        await env.page.locator(departmentInput).fill('已核，请加强综合应用');
        await env.page.locator('[data-afm-regenerate]').click();
        await env.page.waitForFunction(() => document.querySelector('[data-afm-analysis-text]').value.startsWith('重新生成的分析') && !document.querySelector('[data-afm-save]').disabled);
        assert.equal(env.regenerations[0].expected_updated_at, '2026-09-07T12:00:00.000001');
        assert.equal(await env.page.locator(departmentInput).inputValue(), '已核，请加强综合应用');
        await env.page.locator('[data-afm-save]').click();
        await env.page.locator('[data-afm-editor-dialog]').waitFor({ state: 'hidden' });
        assert.equal(env.writes[0].expected_updated_at, '2026-09-07T12:00:00.000002');
        assert.equal(env.writes[0].department_review_opinion, '已核，请加强综合应用');
        assert.equal(env.writes[0].analysis_text, '重新生成的分析：加强综合应用，改进课堂练习。');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('stale editor conflicts retain the draft and cannot overwrite a newer material', async () => {
    const env = await open();
    try {
        env.record.updated_at = '2026-09-07T12:00:00.000099';
        await env.page.locator(departmentInput).fill('本窗口待保存意见');
        await env.page.locator('[data-afm-save]').click();
        await env.page.waitForFunction(() => window.messages.some(item => /材料已在其他窗口更新/.test(item.text)) && !document.querySelector('[data-afm-save]').disabled);
        assert.equal(await env.page.locator('[data-afm-editor-dialog]').isVisible(), true);
        assert.equal(await env.page.locator(departmentInput).inputValue(), '本窗口待保存意见');
        assert.equal(env.writes[0].expected_updated_at, '2026-09-07T12:00:00.000001');
        assert.equal(Object.hasOwn(env.record.fields, 'department_review_opinion'), false);
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('review cards retain aligned opinion and signature areas without horizontal overflow on desktop and mobile', async () => {
    const env = await open({ department_signature_ids: [1], dean_signature_ids: [2], department_review_opinion: '已核', dean_review_opinion: '同意' });
    try {
        for (const viewport of [{ width: 1440, height: 1100 }, { width: 760, height: 1024 }, { width: 390, height: 844 }]) {
            await env.page.setViewportSize(viewport);
            await env.page.locator('.afm-review-grid').scrollIntoViewIfNeeded();
            const bounds = await env.page.locator('.afm-review-card').evaluateAll(cards => cards.map(card => {
                const rect = card.getBoundingClientRect();
                const input = card.querySelector('input').getBoundingClientRect();
                const point = card.querySelector('.spw-point').getBoundingClientRect();
                return { left: rect.left, right: rect.right, top: rect.top, width: rect.width, scroll: card.scrollWidth, inputBottom: input.bottom, pointTop: point.top };
            }));
            for (const bound of bounds) {
                assert.ok(bound.left >= 0 && bound.right <= viewport.width, JSON.stringify({ viewport, bound }));
                assert.ok(bound.scroll <= bound.width + 1, JSON.stringify({ viewport, bound }));
                assert.ok(bound.inputBottom < bound.pointTop);
            }
            if (viewport.width > 800) assert.ok(Math.abs(bounds[0].top - bounds[1].top) < 1);
            else assert.ok(bounds[1].top > bounds[0].top);
            const editorOverflow = await env.page.locator('.afm-editor__body').evaluate(el => el.scrollWidth > el.clientWidth + 1);
            assert.equal(editorOverflow, false);
            if (viewport.width <= 640) {
                await env.page.locator('.afm-review-card').first().evaluate(el => el.scrollIntoView({ block: 'start' }));
            }
            await capture(env.page, `academic-review-${viewport.width}`);
            if (viewport.width <= 640) {
                await env.page.locator('.afm-review-card').last().evaluate(el => el.scrollIntoView({ block: 'start' }));
                await capture(env.page, `academic-review-${viewport.width}-dean`);
            }
        }
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
