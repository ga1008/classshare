/** Real built SSR + API acceptance, guarded to an owned synthetic database. */
const { chromium } = require('@playwright/test');
const fs = require('fs'), path = require('path'), assert = require('assert'), crypto = require('crypto');
assert(process.env.WANDERER_WORLD_RUNTIME, 'Set owned synthetic runtime');
const runtime = path.resolve(process.env.WANDERER_WORLD_RUNTIME);
assert(runtime.startsWith(path.resolve('.codex-temp') + path.sep));
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
assert(fixture.uiV3Synthetic && fixture.wandererWorld && path.resolve(fixture.databasePath) === path.join(runtime, 'db/classroom.db'));
const base = `http://127.0.0.1:${process.env.WANDERER_WORLD_PORT || 8398}`;
const api = `${base}/api/manage/system/wanderers/${fixture.wandererWorld.id}`;
const out = path.join(runtime, 'screenshots-v3'); fs.mkdirSync(out, { recursive: true });
(async () => {
    const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
    for (const name of ['js/wanderer_world_views.js', 'js/wanderer_workspace.js', 'js/wanderer_conversation_view.js', 'js/wanderer_memory_editor.js', 'js/manage_wanderers.js', 'css/lq/pages/wanderers.css', 'css/lq/pages/wanderer-world.css'])
        assert.equal(crypto.createHash('sha256').update(fs.readFileSync('static/' + name)).digest('hex'), graph.sourceHashes[name], 'Formal build source parity: ' + name);
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext(); const page = await context.newPage();
    const errors = [], geometry = [], reopenMs = []; page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
    const health = await (await page.request.get(base + '/api/internal/health')).json();
    assert.equal(path.resolve(health.database_path), fixture.databasePath);
    assert.equal((await page.request.post(base + '/teacher/login', { form: { email: fixture.superTeacher.email, password: fixture.password, next: '/dashboard' }, maxRedirects: 0 })).status(), 303);
    await page.request.post(base + '/api/manage/teacher-onboarding/dismiss', { data: { reason: 'later' } });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(base + '/manage/system/wanderers'); await page.waitForLoadState('networkidle');
    if (await page.locator('[data-life-tip-skip]').isVisible()) await page.locator('[data-life-tip-skip]').click();
    await page.keyboard.press('Escape');
    if (await page.locator('.cultivation-login-reveal').count()) { await page.keyboard.press('Escape'); await page.locator('.cultivation-login-reveal').waitFor({ state: 'detached' }); }
    await page.locator(`[data-wanderer-id="${fixture.wandererWorld.id}"]`).click();
    const owner = key => page.locator(`[data-wd-workspace="${key}"]`);
    async function tab(key) {
        const response = page.waitForResponse(r => r.url().startsWith(api + '/workspace?') && new URL(r.url()).searchParams.get('section') === key);
        await page.locator(`[data-lq-tab="${key}"]`).click(); await response;
        await page.waitForFunction(key => { const el = document.querySelector(`[data-wd-workspace="${key}"]`); return el.children.length > 0 && el.getAttribute('aria-busy') !== 'true'; }, key);
    }
    await owner('overview').getByText('今日自省', { exact: true }).waitFor();
    assert((await owner('overview').innerText()).includes('免费请求剩余'));
    await tab('relations');
    let data = await (await page.request.get(api + '/workspace?section=relations&kind=outgoing')).json();
    assert(data.items.length > 0); assert((await owner('relations').innerText()).includes(`${data.items[0].affinity} / 100`), await owner('relations').innerText());
    await owner('relations').locator('select').selectOption('incoming', { force: true });
    await owner('relations').locator(':scope[aria-busy="true"]').waitFor({ state: 'hidden' });
    data = await (await page.request.get(api + '/workspace?section=relations&kind=incoming')).json();
    assert(data.items.length > 0); assert((await owner('relations').innerText()).includes('真人没有虚构'));
    assert((await owner('relations').innerText()).includes(`${data.items[0].affinity} / 100`));
    await page.screenshot({ path: path.join(out, 'rankings-incoming.png') });
    await tab('identity');
    const identity = await (await page.request.get(api + '/workspace?section=identity')).json();
    assert.deepEqual(await owner('identity').locator('select option').evaluateAll(nodes => nodes.map(el => el.value)), ['', ...identity.summary.record_kinds]);
    assert(await owner('identity').locator('select option').evaluateAll(nodes => nodes.every(el => /[\u4e00-\u9fff]/.test(el.textContent))));
    for (const kind of identity.summary.record_kinds) {
        const response = page.waitForResponse(r => r.url().startsWith(api + '/workspace?') && new URL(r.url()).searchParams.get('kind') === kind);
        await owner('identity').locator('select').selectOption(kind, { force: true });
        const loaded = await response; assert(loaded.ok(), kind + ': ' + await loaded.text());
        await owner('identity').locator(':scope[aria-busy="true"]').waitFor({ state: 'hidden' });
        assert.equal(await owner('identity').locator('select').inputValue(), kind); assert(!(await owner('identity').innerText()).includes('读取失败'), kind);
    }
    await tab('conversations');
    const choice = page.locator(`[data-wd-conversation="${fixture.wandererWorld.groupKey}"]`);
    await choice.click(); await page.locator('.ww-chat-message').first().waitFor();
    assert.equal(await page.locator('.ww-chat-message').count(), 30);
    assert.equal(await page.locator('.ww-chat-message').getByText('同文不同ID的历史记录必须保留，不能在管理页藏起来。', { exact: true }).count(), 2, 'Equal original text under distinct IDs remains visible');
    assert(await page.locator('[data-ww-own="true"] .lq-bubble--outgoing').count() > 0);
    assert(await page.locator('[data-ww-own="false"] .lq-bubble--incoming').count() > 0);
    assert.equal(await choice.getAttribute('aria-current'), 'true');
    await page.locator('[data-ww-chat-stream]').evaluate(el => el.scrollTop = 0);
    const first = await page.locator('.ww-chat-message').first().getAttribute('data-ww-message-id');
    const before = await page.locator(`[data-ww-message-id="${first}"]`).boundingBox();
    await page.locator('[data-ww-chat-earlier]').click();
    await page.waitForFunction(() => document.querySelectorAll('.ww-chat-message').length === 36);
    const after = await page.locator(`[data-ww-message-id="${first}"]`).boundingBox();
    assert(Math.abs(before.y - after.y) <= 2, 'Older prepend preserves reading anchor');
    assert.equal(new Set(await page.locator('.ww-chat-message').evaluateAll(nodes => nodes.map(n => n.dataset.wwMessageId))).size, 36);
    let failMessages = true;
    await page.route('**/conversations/*/messages?*', route => failMessages ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '合成暂时不可用' }) }) : route.continue());
    await page.locator('[data-ww-chat-latest]').click(); await page.locator('.ww-transcript-foot').getByRole('button', { name: '重试', exact: true }).waitFor();
    assert.equal(await page.locator('.ww-chat-message').count(), 36, '503 retains loaded transcript');
    failMessages = false; await page.locator('.ww-transcript-foot').getByRole('button', { name: '重试', exact: true }).click();
    await page.waitForFunction(() => document.querySelectorAll('.ww-chat-message').length === 30);
    if (fixture.wandererWorld.dmKey) {
        let slowGroup = true;
        await page.route('**/conversations/*/messages?*', async route => {
            if (slowGroup && decodeURIComponent(route.request().url()).includes(fixture.wandererWorld.groupKey)) await new Promise(resolve => setTimeout(resolve, 250));
            await route.fallback();
        });
        await page.locator('[data-ww-chat-latest]').click();
        await page.locator(`[data-wd-conversation="${fixture.wandererWorld.dmKey}"]`).click();
        await page.waitForFunction(() => document.querySelectorAll('.ww-chat-message').length === 4);
        await page.waitForTimeout(350);
        assert((await page.locator('.ww-chat-stream').innerText()).includes('私聊合成往返'));
        assert(!(await page.locator('.ww-chat-stream').innerText()).includes('真实合成消息'), 'Late group response must not overwrite private conversation');
        slowGroup = false; await choice.click(); await page.waitForFunction(() => document.querySelectorAll('.ww-chat-message').length === 30);
    }
    for (const width of [320, 390, 768, 1440]) for (const dark of [false, true]) {
        await page.setViewportSize({ width, height: 900 });
        await page.emulateMedia({ colorScheme: dark ? 'dark' : 'light', reducedMotion: dark ? 'reduce' : 'no-preference' });
        await page.evaluate(d => { document.documentElement.dataset.appearance = d ? 'dark' : 'light'; document.documentElement.dataset.lqSceneTone = d ? 'dark' : 'light'; }, dark);
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        const shape = await page.locator('#wd-detail .wd-detail__surface').evaluate(el => {
            const b = el.getBoundingClientRect(), stream = el.querySelector('[data-ww-chat-stream]'), head = el.querySelector('.wd-detail__head');
            return { x: b.x, right: b.right, top: b.top, bottom: b.bottom, overflow: el.scrollWidth - el.clientWidth, streamHeight: stream.clientHeight, streamScrollable: stream.scrollHeight > stream.clientHeight, headTop: head.getBoundingClientRect().top };
        });
        geometry.push({ width, dark, ...shape });
        assert(shape.x >= -1 && shape.right <= width + 1 && shape.top >= -1 && shape.bottom <= 901 && shape.overflow <= 1 && shape.streamHeight > 180 && shape.streamScrollable, JSON.stringify(geometry.at(-1)));
        await page.locator('[data-ww-chat-stream]').evaluate(el => el.scrollTop = el.scrollHeight);
        assert.equal(await page.locator('.wd-detail__head').evaluate(el => el.getBoundingClientRect().top), shape.headTop);
        await page.screenshot({ path: path.join(out, `chat-${width}-${dark ? 'dark' : 'light'}.png`) });
        if (width <= 800) {
            await page.locator('[data-ww-chat-back]').click(); assert(await choice.isVisible());
            assert(await page.locator('[data-ww-transcript]').isHidden());
            await page.screenshot({ path: path.join(out, `list-${width}-${dark ? 'dark' : 'light'}.png`) });
            await choice.click(); await page.locator('.ww-chat-message').first().waitFor();
        }
    }
    await page.setViewportSize({ width: 1440, height: 900 });
    await owner('conversations').locator('input').fill('不存在的会话'); await owner('conversations').getByRole('button', { name: '查找', exact: true }).click();
    await page.getByText('没有匹配的会话。', { exact: true }).waitFor();
    assert.equal(await page.locator('[data-wd-conversation]').count(), 0);
    await tab('memory');
    const editor = owner('memory').locator('[data-ww-memory-editor]').first();
    await editor.locator(':scope > summary').click();
    const editorId = Number(await editor.getAttribute('data-ww-memory-editor'));
    const memories = await (await page.request.get(api + '/workspace?section=memory&kind=episodes')).json();
    const original = memories.items.find(item => Number(item.id) === editorId); assert(original.content_hash);
    const priorHistory = await (await page.request.get(api + `/memory-corrections?target_kind=episode&target_id=${editorId}`)).json();
    const draft = '已核对新世界规则：每日自省独立免费，普通思考额度照常。';
    await editor.locator('textarea[name="content"]').fill(draft);
    await editor.locator('textarea[name="reason"]').fill('合成验收：核对世界手册后修正升级前的认知。');
    await tab('relations'); await tab('memory');
    assert.equal(await editor.locator('textarea[name="content"]').inputValue(), draft, 'Switch tab preserves memory draft');
    await page.locator('[data-wd-detail-close]').click(); await page.locator('#wd-detail').waitFor({ state: 'hidden' });
    await page.locator(`[data-wanderer-id="${fixture.wandererWorld.id}"]`).click();
    await owner('overview').getByText('今日自省', { exact: true }).waitFor(); await tab('memory');
    assert.equal(await editor.locator('textarea[name="content"]').inputValue(), draft, 'Reopening same account preserves unsaved draft');
    const competing = await page.request.post(api + '/memory-corrections', { data: {
        target_kind: 'episode', target_id: original.id, expected_revision: original.correction_revision, expected_content_hash: original.content_hash,
        title: original.editor_title || original.title, content: '另一位管理员已先核对原文。', reason: '合成版本竞争', request_id: crypto.randomUUID(),
    } }); assert(competing.ok(), await competing.text());
    await editor.getByRole('button', { name: '保存修订', exact: true }).click();
    await editor.getByRole('alert').waitFor(); assert.equal(await editor.locator('textarea[name="content"]').inputValue(), draft);
    const reloaded = page.waitForResponse(r => r.url().startsWith(api + '/workspace?') && new URL(r.url()).searchParams.get('section') === 'memory');
    await editor.getByRole('button', { name: '重新读取当前版本', exact: true }).click(); await reloaded;
    await owner('memory').locator(':scope[aria-busy="true"]').waitFor({ state: 'hidden' });
    await editor.getByText('另一位管理员已先核对原文。', { exact: true }).first().waitFor();
    assert.equal(await editor.locator('textarea[name="content"]').inputValue(), draft);
    await page.setViewportSize({ width: 390, height: 900 });
    await editor.getByRole('button', { name: '保存修订', exact: true }).scrollIntoViewIfNeeded();
    assert(await editor.getByRole('button', { name: '保存修订', exact: true }).evaluate(el => { const b = el.getBoundingClientRect(), hit = document.elementFromPoint(b.x + b.width / 2, b.y + b.height / 2); return hit === el || el.contains(hit); }));
    await page.screenshot({ path: path.join(out, 'memory-conflict-draft-mobile.png') });
    await page.route('**/memory-corrections', async route => { if (route.request().method() === 'POST') await new Promise(resolve => setTimeout(resolve, 500)); await route.fallback(); });
    const response = page.waitForResponse(r => r.url() === api + '/memory-corrections' && r.request().method() === 'POST' && r.ok());
    const savedProjection = page.waitForResponse(r => r.url().startsWith(api + '/workspace?') && new URL(r.url()).searchParams.get('section') === 'memory');
    await editor.getByRole('button', { name: '保存修订', exact: true }).click();
    assert(await editor.locator('textarea[name="content"]').evaluate(el => el.readOnly));
    await page.locator('[data-wd-detail-close]').click(); assert(await page.locator('#wd-detail').isVisible(), 'Close veto while correction is saving');
    await response; await savedProjection;
    await owner('memory').locator(':scope[aria-busy="true"]').waitFor({ state: 'hidden' });
    const history = await (await page.request.get(api + `/memory-corrections?target_kind=episode&target_id=${editorId}`)).json();
    assert.equal(history.items.length, priorHistory.items.length + 2); assert(history.items.some(item => item.before.content === original.editor_content));
    assert(history.items.some(item => item.after.content === draft));
    await page.locator('[data-wd-detail-close]').click(); await page.locator('#wd-detail').waitFor({ state: 'hidden' });
    assert(await page.locator(`[data-wanderer-id="${fixture.wandererWorld.id}"]`).evaluate(el => el === document.activeElement));
    for (let index = 0; index < 20; index += 1) {
        const started = Date.now(); await page.locator(`[data-wanderer-id="${fixture.wandererWorld.id}"]`).click();
        await owner('overview').getByText('今日自省', { exact: true }).waitFor();
        await page.locator('[data-wd-detail-close]').click(); await page.locator('#wd-detail').waitFor({ state: 'hidden' });
        reopenMs.push(Date.now() - started); assert.equal(await page.locator('#wd-detail').count(), 1);
    }
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(runtime, 'browser-admin-v3-result.json'), JSON.stringify({ pass: true, graph: graph.revision, geometry, reopenMs, errors, admin_record_kind_count: identity.summary.record_kinds.length, checks: ['all retained ledgers selectable, readable and preserve selection', 'outgoing/incoming real rankings', 'free reflection display', 'own/peer bubble direction', 'older prepend anchor and ID overlap', 'same text different ID retained', 'private/group delayed-response isolation', '503 transcript preserve + correct latest retry', '8 chat responsive/theme scenes + mobile list', 'stable fixed header and independent scroll', 'server conversation search', 'memory draft tab/close persistence', 'real 409 and reload preservation', 'mobile Save hit target', 'saving fields readonly and close veto', 'audited correction history and original preserved', 'modal focus return', '20 reopen-close cycles with one modal root'] }, null, 2));
    console.log(JSON.stringify({ pass: true, geometry: geometry.length, output: out })); await browser.close();
})().catch(error => { console.error(error); process.exit(1); });
