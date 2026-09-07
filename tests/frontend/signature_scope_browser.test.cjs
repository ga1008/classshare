/* Run: node --test tests/frontend/signature_scope_browser.test.cjs
 * Production templates/modules in isolated Chromium; all signature APIs are intercepted. */
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const workspace = path.resolve(__dirname, '../..');
const read = file => fs.readFileSync(path.join(workspace, file), 'utf8');
const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1sAAAAASUVORK5CYII=', 'base64');
const scopes = ['platform', 'school', 'college', 'department', 'personal'];
const labels = ['平台可见', '学校可见', '学院可见', '系部可见', '个人可见'];
let browser;
before(async () => {
    const executablePath = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
    browser = await chromium.launch({ headless: true, ...(fs.existsSync(executablePath) ? { executablePath } : {}) });
});
after(async () => { await browser?.close(); });
function block(template, name) { return template.match(new RegExp('{% block ' + name + ' %}([\\s\\S]*?){% endblock %}'))[1]; }
function fixture(kind, admin) {
    let content, styles;
    if (kind === 'manage') {
        const template = read('templates/manage/signatures.html');
        content = block(template, 'header_actions') + block(template, 'content');
        content = content.replaceAll("{{ '1' if signature_actor.is_super_admin else '0' }}", admin ? '1' : '0')
            .replaceAll('{{ signature_actor.school_code }}', 'school-a').replaceAll('{{ signature_actor.school_name }}', '甲校');
        content = '<button id="signature-open-upload-btn">上传签名</button><button id="signature-refresh-btn">刷新</button>' + content;
        styles = template.match(/<style>([\s\S]*?)<\/style>/)[1];
    } else if (kind === 'profile') {
        content = '<section class="psig-shell" data-signature-app></section>';
        styles = read('templates/profile.html').match(/<style>([\s\S]*?)<\/style>/)[1];
    } else { content = '<div id="point"></div>'; styles = ''; }
    content = content.replace(/{{[\s\S]*?}}/g, '').replace(/{%[\s\S]*?%}/g, '');
    const script = kind === 'point'
        ? `<script type="module">import { SignaturePointControl } from '/static/js/signature_point_workflow.js';window.point = new SignaturePointControl({root:document.querySelector('#point'),pointKey:'test.review',pointLabel:'审核签名',materialType:'test',materialId:1});await window.point.load();</script>`
        : `<script type="module" src="/static/js/${kind === 'manage' ? 'manage' : 'profile'}_signatures.js"></script>`;
    return `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/signature_scope_fields.css"><link rel="stylesheet" href="/static/css/signature_point_workflow.css"><style>body{padding:20px;background:#f8fafc} ${styles}</style></head><body>${content}${script}</body></html>`;
}
async function open(kind = 'manage', admin = false, viewport = { width: 1360, height: 1080 }, actorOverrides = {}) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce' });
    const page = await context.newPage();
    const errors = [], queries = [], uploads = [], patches = [];
    page.on('pageerror', error => errors.push(error.message));
    const memberships = [
        { school_code: 'school-a', school_name: '甲校', college: '信息学院', department: '计算机系' },
        { school_code: 'school-b', school_name: '乙校', college: '工程学院', department: '网络系' },
    ];
    const actor = { id: 7, role: 'teacher', name: '签名教师', is_super_admin: admin, ...memberships[0], memberships, scope_options: scopes.map((value, index) => ({ value, label: labels[index] })), ...actorOverrides };
    const item = { id: 1, name: '签名教师', subject_name: '签名教师', subject_id: 7, subject_role: 'teacher', subject_role_label: '教师',
        owner_id: 7, owner_role: 'teacher', owner_name: '签名教师', is_owner: true, is_subject: true, can_edit: true, can_use: true, can_delete: true,
        scope_level: 'college', scope_label: '学院可见', ...memberships[0], image_url: '/signature.png', download_url: '/signature.png', identity_category: 'dean', identity_label: '院长' };
    const env = { context, page, errors, queries, uploads, patches, actor, item, rejectPatch: false };
    await page.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname.startsWith('/static/')) {
            const local = path.join(workspace, url.pathname.slice(1));
            return route.fulfill({ contentType: local.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(local) });
        }
        if (url.pathname === '/signature.png') return route.fulfill({ contentType: 'image/png', body: tinyPng });
        if (url.pathname.startsWith('/api/')) {
            let data = { items: [] };
            if (url.pathname === '/api/signatures') {
                queries.push(url.search);
                data = { items: [item], actor, school_options: memberships, selected_school: url.searchParams.get('school_code') ? memberships.find(org => org.school_code === url.searchParams.get('school_code')) : {}, stats: { visible_total: 1, mine: 1, college: 1 } };
            } else if (url.pathname === '/api/signatures/teachers') {
                data = { items: [{ id: 7, name: '签名教师', ...memberships[0] }] };
            } else if (url.pathname === '/api/signatures/schools') {
                data = { items: memberships };
            } else if (url.pathname === '/api/manage/system/organizations/tree') {
                data = { schools: memberships.map(org => ({ ...org, colleges: [{ college_name: org.college, departments: [{ department_name: org.department }] }] })) };
            } else if (url.pathname === '/api/signatures/upload') {
                const raw = request.postDataBuffer().toString('utf8');
                uploads.push(Object.fromEntries([...raw.matchAll(/name="([^"\r\n]+)"\r\n\r\n([^\r\n]*)/g)].map(match => [match[1], match[2]])));
                data = { signature: item };
            } else if (url.pathname === '/api/signatures/1' && request.method() === 'PATCH') {
                const payload = request.postDataJSON(); patches.push(payload);
                if (env.rejectPatch) return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: '不允许共享到该组织。' }) });
                Object.assign(item, payload, { scope_label: labels[scopes.indexOf(payload.scope_level)] });
                data = { signature: item };
            } else if (url.pathname.includes('/api/signatures/points/')) {
                const candidate = { id: 2, subject_name: '另一教师', scope_label: '学校可见', owner_name: '归属教师', identity_match: true, identity_label: '院长' };
                data = { material: { label: '当前材料' }, point: { required_identity_labels: ['院长'] }, signatures: [candidate], usable_signatures: [], requestable_signatures: [candidate], selected_signature_ids: [] };
            }
            return route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) });
        }
        return route.fulfill({ contentType: 'text/html', body: fixture(kind, admin) });
    });
    await page.goto('http://signature.test/' + kind);
    await page.locator(kind === 'manage' ? '[data-signature-card]' : kind === 'profile' ? '.psig-item' : '[data-spw-apply]').first().waitFor();
    return env;
}
async function capture(page, name) {
    if (!process.env.SIGNATURE_SCOPE_QA_DIR) return;
    const directory = path.resolve(process.env.SIGNATURE_SCOPE_QA_DIR); fs.mkdirSync(directory, { recursive: true });
    await page.screenshot({ path: path.join(directory, name + '.png') });
}

test('ordinary owner sees five upload scopes and submits an active secondary organization', async () => {
    const env = await open();
    try {
        assert.equal(new URLSearchParams(env.queries[0]).has('school_code'), false);
        await env.page.locator('#signature-open-upload-btn').click();
        const root = env.page.locator('#signature-upload-scope-fields');
        assert.deepEqual(await root.locator('[data-scope-level] option').evaluateAll(options => options.map(option => option.value)), scopes);
        await root.locator('[data-scope-level]').selectOption('department');
        await root.locator('[data-scope-membership]').selectOption('1');
        assert.equal(await root.locator('[data-scope-admin-org]').isVisible(), false);
        await env.page.locator('#signature-file-input').setInputFiles({ name: 'signature.png', mimeType: 'image/png', buffer: tinyPng });
        await env.page.locator('#signature-upload-submit-btn').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-upload-modal').style.display === 'none');
        assert.equal(env.uploads[0].scope_level, 'department');
        assert.equal(env.uploads[0].school_code, 'school-b');
        assert.equal(env.uploads[0].college, '工程学院');
        assert.equal(env.uploads[0].department, '网络系');
        assert.equal(Object.hasOwn(env.uploads[0], 'subject_id'), false);
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('editing preserves college scope and organization when the job identity changes; all scopes filter distinctly', async () => {
    const env = await open();
    try {
        await env.page.locator('#signature-edit-btn').click();
        const root = env.page.locator('#signature-edit-scope-fields');
        assert.equal(await root.locator('[data-scope-level]').inputValue(), 'college');
        await env.page.locator('#signature-edit-identity-input').selectOption('principal');
        await env.page.locator('#signature-edit-submit-btn').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-edit-modal').style.display === 'none');
        assert.equal(env.patches[0].scope_level, 'college');
        assert.equal(env.patches[0].department, '计算机系');
        for (const scope of scopes) {
            await env.page.locator('#signature-scope-filter').selectOption(scope);
            await env.page.waitForTimeout(300);
            assert.equal(new URLSearchParams(env.queries.at(-1)).get('scope'), scope);
        }
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('super admin starts globally, can clear school filtering and set an explicit organization', async () => {
    const env = await open('manage', true);
    try {
        assert.equal(new URLSearchParams(env.queries[0]).has('school_code'), false);
        await env.page.locator('#signature-school-search-input').fill('school-b');
        await env.page.locator('#signature-school-search-input').press('Enter');
        await env.page.waitForTimeout(350);
        assert.equal(new URLSearchParams(env.queries.at(-1)).get('school_code'), 'school-b');
        await env.page.locator('#signature-clear-filter-btn').click();
        await env.page.waitForTimeout(100);
        assert.equal(new URLSearchParams(env.queries.at(-1)).has('school_code'), false);
        await env.page.locator('#signature-edit-btn').click();
        const root = env.page.locator('#signature-edit-scope-fields');
        await root.locator('[data-scope-level]').selectOption('department');
        await root.locator('[data-scope-school]').fill('school-b');
        await root.locator('[data-scope-college-options] option[value="工程学院"]').waitFor({ state: 'attached' });
        assert.deepEqual(await root.locator('[data-scope-college-options] option').evaluateAll(options => options.map(option => option.value)), ['工程学院']);
        await root.locator('[data-scope-college]').fill('工程学院');
        assert.deepEqual(await root.locator('[data-scope-department-options] option').evaluateAll(options => options.map(option => option.value)), ['网络系']);
        await root.locator('[data-scope-department]').fill('网络系');
        await capture(env.page, 'signature-scope-admin-desktop');
        await env.page.locator('#signature-edit-submit-btn').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-edit-modal').style.display === 'none');
        assert.equal(env.patches[0].scope_level, 'department');
        assert.equal(env.patches[0].school_code, 'school-b');
        assert.equal(env.patches[0].department, '网络系');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('profile lists only own or subject signatures, edits scope and uploads with the selected scope on mobile', async () => {
    const env = await open('profile', false, { width: 390, height: 844 });
    try {
        assert.equal(new URLSearchParams(env.queries[0]).get('scope'), 'mine');
        assert.match(await env.page.locator('.psig-item').textContent(), /学院可见/);
        await env.page.locator('[data-psig-scope]').click();
        const edit = env.page.locator('[data-psig-edit-scope]');
        await edit.locator('[data-scope-level]').selectOption('school');
        await edit.locator('[data-scope-membership]').selectOption('1');
        await env.page.locator('[data-psig-scope-form] button[type="submit"]').click();
        await env.page.locator('[data-psig-scope-form]').waitFor({ state: 'detached' });
        assert.equal(env.patches[0].scope_level, 'school');
        assert.equal(env.patches[0].school_code, 'school-b');
        await env.page.locator('[data-psig-upload-scope] [data-scope-level]').selectOption('platform');
        await capture(env.page, 'signature-scope-profile-mobile');
        assert.equal(await env.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
        await env.page.locator('[data-psig-file]').setInputFiles({ name: 'signature.png', mimeType: 'image/png', buffer: tinyPng });
        await env.page.waitForTimeout(150);
        assert.equal(env.uploads[0].scope_level, 'platform');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('visible signature candidates retain request authorization and show scope alongside ownership', async () => {
    const env = await open('point');
    try {
        assert.equal(await env.page.locator('[data-spw-available]').isDisabled(), true);
        await env.page.locator('[data-spw-apply]').click();
        assert.match(await env.page.locator('[data-spw-candidates]').textContent(), /学校可见.*归属：归属教师/);
        assert.match(await env.page.locator('.spw-flow-note').textContent(), /需要申请授权/);
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('explicitly empty memberships never revive inactive organization anchors', async () => {
    const env = await open('manage', false, { width: 1360, height: 1080 }, { memberships: [] });
    try {
        await env.page.locator('#signature-open-upload-btn').click();
        const root = env.page.locator('#signature-upload-scope-fields');
        await root.locator('[data-scope-level]').selectOption('department');
        assert.deepEqual(await root.locator('[data-scope-membership] option').evaluateAll(options => options.map(option => option.value)), ['']);
        await env.page.locator('#signature-file-input').setInputFiles({ name: 'signature.png', mimeType: 'image/png', buffer: tinyPng });
        await env.page.locator('#signature-upload-submit-btn').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-upload-status').textContent.includes('有效组织'));
        assert.equal(env.uploads.length, 0);
        await root.locator('[data-scope-level]').selectOption('personal');
        await env.page.locator('#signature-upload-submit-btn').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-upload-modal').style.display === 'none');
        assert.equal(env.uploads[0].scope_level, 'personal');
        assert.equal(env.uploads[0].school_code, '');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});

test('handwriting submission uses the same chosen visibility scope as file uploads', async () => {
    const env = await open();
    try {
        await env.page.locator('#signature-open-upload-btn').click();
        await env.page.locator('#signature-upload-scope-fields [data-scope-level]').selectOption('platform');
        await env.page.locator('#signature-open-pad-btn').click();
        const bounds = await env.page.locator('[data-pad-canvas]').boundingBox();
        await env.page.mouse.move(bounds.x + 55, bounds.y + 65);
        await env.page.mouse.down();
        await env.page.mouse.move(bounds.x + 115, bounds.y + 95, { steps: 8 });
        await env.page.mouse.move(bounds.x + 145, bounds.y + 45, { steps: 8 });
        await env.page.mouse.up();
        await env.page.locator('[data-pad-confirm]').click();
        await env.page.waitForFunction(() => document.querySelector('#signature-upload-modal').style.display === 'none');
        assert.equal(env.uploads[0].scope_level, 'platform');
        assert.equal(env.uploads[0].name, '手写签名');
        assert.deepEqual(env.errors, []);
    } finally { await env.context.close(); }
});
