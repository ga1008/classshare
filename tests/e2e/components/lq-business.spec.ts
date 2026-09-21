import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const fixture = JSON.parse(execFileSync(process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python', ['tests/e2e/scripts/render_lq_business.py'], { encoding: 'utf8' }));
const clockProps = { assignment_id: 7, server_now: '2026-09-20 12:00:00', countdown_at: '2026-09-20 13:00:01', deadline_phase: 'regular', accepting: true };
const questions = { label: '答题卡', groups: [{ id: 'p1', label: '第一部分', items: [
    { id: 'q1', index: 1, answered: true, current: true, flagged: true, error: true, pendingUpload: true },
    { id: 'q2', index: 2 }, { id: 'q3', index: 3, disabled: true },
] }] };
async function mount(page: Page) {
    const requests: string[] = [];
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'https://lq-business.test') return route.abort();
        if (url.pathname.startsWith('/static/')) {
            const file = path.resolve(`.${url.pathname}`);
            if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
        }
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LQ business</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}main{padding:16px;max-width:100%;box-sizing:border-box}#fixture{display:grid;gap:16px}h1{margin-bottom:16px}</style></head><body><main><h1>截止、任务与答题</h1><div id="fixture"></div><label for="draft">业务草稿</label><textarea id="draft">保留输入</textarea><button id="after">继续编辑</button></main><script type="module">import * as business from '/static/js/lq/business.js';import * as clock from '/static/js/assignment_time.js';window.business=business;window.clockApi=clock;document.body.dataset.ready='true';</script></body></html>` });
        requests.push(`${route.request().method()} ${url.pathname}`);
        return route.fulfill({ status: 404, body: 'No business network fixture' });
    });
    await page.goto('https://lq-business.test/');
    await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
    return requests;
}

test('LQ business Jinja, HTML and Element entries agree and reject invalid props before rendering', async ({ page }) => {
    expect(fixture.isolated).toBe(true); expect(fixture.cases.filter((item: any) => item.error)).toEqual([]);
    expect(fixture.invalid.filter((item: any) => !item.error)).toEqual([]);
    await mount(page);
    const actual = await page.evaluate(serialized => {
        const fixture = JSON.parse(serialized);
        const api = (window as any).business;
        function semantic(node: Node): any {
            if (node.nodeType === Node.TEXT_NODE) return node.textContent?.trim() ? { text: node.textContent } : null;
            const el = node as Element;
            return { tag: el.tagName.toLowerCase(), attrs: Object.fromEntries([...el.attributes].map(attr => [attr.name, attr.value]).sort()), children: [...el.childNodes].map(semantic).filter(Boolean) };
        }
        const parse = (html: string) => { const t = document.createElement('template'); t.innerHTML = html; return [...t.content.childNodes].map(semantic).filter(Boolean); };
        return { cases: fixture.cases.map((item: any) => ({ normalized: api.businessProps(item.kind, item.props), jinja: parse(item.html), html: parse(api.businessMarkup(item.kind, item.props)), element: [semantic(api.createBusiness(item.kind, item.props))] })),
            invalid: fixture.invalid.map((item: any) => ['businessProps', 'businessMarkup', 'createBusiness'].every(method => { try { api[method](item.kind, item.props); return false; } catch { return true; } })) };
    }, JSON.stringify(fixture));
    actual.cases.forEach((item: any, index: number) => {
        expect(item.normalized).toEqual(fixture.cases[index].normalized); expect(item.html).toEqual(item.jinja); expect(item.element).toEqual(item.jinja);
    });
    expect(actual.invalid.every(Boolean)).toBe(true);
});

for (const entry of ['jinja', 'html', 'element']) test(`LQ ${entry} clock observes one existing owner, offsets and exact boundaries without inferring permissions`, async ({ page }) => {
    await page.clock.install({ time: new Date('2026-09-20T04:05:00Z') });
    const requests = await mount(page);
    await page.clock.pauseAt(new Date('2026-09-20T04:05:10Z'));
    await page.evaluate(({ entry, clockProps, fixture }) => {
        const w = window as any, host = document.getElementById('fixture')!;
        if (entry === 'jinja') host.innerHTML = fixture.cases.find((item: any) => item.kind === 'deadline_clock' && item.props.compact === true).html;
        else if (entry === 'html') host.innerHTML = w.business.html.deadline_clock({ ...clockProps, compact: true });
        else host.append(w.business.deadline_clock({ ...clockProps, compact: true }));
        const root = host.firstElementChild as HTMLElement;
        // The same server snapshot applies to all three source entries.
        root.dataset.countdownAt = clockProps.countdown_at;
        w.binder = w.business.deadlineClock(root); w.permissions = [];
        w.owner = w.clockApi.initAssignmentClocks({ onStateChange: (states: Map<string, any>) => w.permissions.push(states.get('7')) });
        w.snap = () => ({ value: root.querySelector('[data-assignment-clock-value]')!.textContent, compact: root.querySelector('[data-lq-clock-compact]')!.textContent,
            absolute: root.querySelector('time')!.textContent, datetime: root.querySelector('time')!.dateTime, tone: root.dataset.tone,
            permission: w.permissions.at(-1).is_accepting_submissions, live: root.querySelector('[role=timer]')!.getAttribute('aria-live') });
    }, { entry, clockProps, fixture });
    expect(await page.evaluate(() => (window as any).snap())).toMatchObject({ value: '1 小时 0 分钟', compact: '60:01', datetime: '2026-09-20T05:00:01.000Z', tone: 'deadline-regular', permission: true, live: 'off' });
    await page.clock.runFor(1000);
    expect(await page.evaluate(() => (window as any).snap())).toMatchObject({ compact: '60:00', tone: 'deadline-urgent' });
    await page.evaluate(() => {
        const root = document.querySelector('[data-assignment-clock]') as HTMLElement;
        Object.assign(root.dataset, { serverNow: '2026-09-20 13:00:00', countdownAt: '2026-09-20 13:00:01', lateUntil: '2026-09-20 13:00:03', latePolicyLabel: '补交扣分' });
        (window as any).owner.refresh();
    });
    await page.clock.runFor(1000);
    expect(await page.evaluate(() => (window as any).snap())).toMatchObject({ compact: '00:02', tone: 'deadline-late', permission: true, datetime: '2026-09-20T05:00:03.000Z' });
    await page.clock.runFor(2000);
    expect(await page.evaluate(() => (window as any).snap())).toMatchObject({ compact: '已截止', tone: 'deadline-closed', permission: false });
    await expect(page.locator('time')).toBeVisible();
    await page.evaluate(() => { (window as any).binder.destroy(); (window as any).owner.dispose(); });
    expect(requests).toEqual([]);
});

test('LQ clock keeps legacy personal values, long compact minutes, absolute times and passive twenty-cycle cleanup', async ({ page }) => {
    await page.clock.install({ time: new Date('2026-09-20T04:00:00Z') }); const requests = await mount(page);
    await page.clock.pauseAt(new Date('2026-09-20T04:00:10Z'));
    const result = await page.evaluate(async clockProps => {
        const w = window as any, host = document.getElementById('fixture')!;
        let intervals = 0, timeouts = 0, fetches = 0;
        const originalInterval = window.setInterval, originalTimeout = window.setTimeout, originalFetch = window.fetch;
        window.setInterval = ((...args: any[]) => { intervals++; return (originalInterval as any)(...args); }) as any;
        window.setTimeout = ((...args: any[]) => { timeouts++; return (originalTimeout as any)(...args); }) as any;
        window.fetch = ((...args: any[]) => { fetches++; return (originalFetch as any)(...args); }) as any;
        const root = w.business.deadline_clock({ ...clockProps, compact: true, countdown_at: '2030-09-20 12:00:00' }); host.append(root);
        let binder = w.business.deadlineClock(root);
        const passive = { intervals, timeouts, fetches };
        w.owner = w.clockApi.initAssignmentClocks();
        const long = root.querySelector('[data-lq-clock-compact]').textContent;
        const width = root.querySelector('[data-lq-clock-compact]').getBoundingClientRect().width;
        const url = '/static/js/lq/business.js?duplicate'; const alias = await import(url);
        let cycles = true;
        for (let i = 0; i < 20; i++) { const old = binder; cycles &&= alias.deadlineClock(root) === binder; old.destroy(); binder = alias.deadlineClock(root); old.destroy(); cycles &&= alias.deadlineClock(root) === binder; }
        const onlyOwnerTimers = { intervals, timeouts, fetches };
        Object.assign(root.dataset, { personalResubmission: '1', canResubmit: '1', resubmissionDueAt: '2026-09-20 12:00:02' });
        w.owner.refresh();
        const personal = { label: root.querySelector('[data-assignment-clock-label]').textContent, value: root.querySelector('[data-assignment-clock-value]').textContent,
            compact: root.querySelector('[data-lq-clock-compact]').textContent, datetime: root.querySelector('time').dateTime };
        const stableWidth = root.querySelector('[data-lq-clock-compact]').getBoundingClientRect().width >= width;
        w.binder = binder; window.setInterval = originalInterval; window.setTimeout = originalTimeout; window.fetch = originalFetch;
        return { passive, onlyOwnerTimers, cycles, long, personal, stableWidth };
    }, clockProps);
    expect(result.passive).toEqual({ intervals: 0, timeouts: 0, fetches: 0 });
    expect(result.onlyOwnerTimers).toEqual({ intervals: 1, timeouts: 1, fetches: 0 });
    expect(result.cycles).toBe(true); expect(result.long).toBe('2103840:00'); expect(result.stableWidth).toBe(true);
    expect(result.personal).toMatchObject({ label: '重交截止', compact: '00:02', datetime: '2026-09-20T04:00:02.000Z' });
    expect(result.personal.value).not.toBe('00:02'); await page.clock.runFor(2000);
    await expect(page.locator('.lq-clock')).toHaveAttribute('data-tone', 'deadline-closed');
    expect(await page.evaluate(() => (window as any).owner.getStates().get('7').localAccepting)).toBe(true);
    await page.evaluate(() => { (window as any).binder.destroy(); (window as any).owner.dispose(); });
    await page.clock.runFor(310000); expect(requests).toEqual([]);
});

test('LQ clock pulse occurs only on urgent entry and reduced motion removes it', async ({ page }) => {
    await mount(page);
    await page.evaluate(clockProps => {
        const w = window as any, root = w.business.deadline_clock({ ...clockProps, countdown_at: '2026-09-20 12:30:00' });
        document.getElementById('fixture')!.append(root); w.pulses = 0;
        root.addEventListener('animationstart', (event: AnimationEvent) => { if (event.animationName === 'lq-clock-enter-urgent') w.pulses++; });
        w.owner = w.clockApi.initAssignmentClocks(); w.binder = w.business.deadlineClock(root);
    }, clockProps);
    await expect.poll(() => page.evaluate(() => (window as any).pulses)).toBe(1);
    await page.evaluate(() => { for (let i = 0; i < 20; i++) (window as any).binder.refresh(); });
    expect(await page.evaluate(() => (window as any).pulses)).toBe(1);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    expect(await page.locator('.lq-clock__dot').evaluate(el => getComputedStyle(el).animationName)).toBe('none');
    await page.evaluate(() => { (window as any).binder.destroy(); (window as any).owner.dispose(); });
});

test('LQ JobStatus validates atomically, rejects stale identity generations and only explicit actions call the caller', async ({ page }) => {
    const requests = await mount(page);
    const result = await page.evaluate(async () => {
        const w = window as any, api = w.business, host = document.getElementById('fixture')!;
        const snapshot = { identity: 'task:9', generation: 2, family: 'agent', state: 'waiting_input', label: '等待你的回答', elapsed: '12 秒', progress: { label: '执行进度' }, actions: [{ key: 'view', label: '查看结果' }] };
        const root = api.job_status(snapshot); host.append(root); w.calls = [];
        let controller = api.jobStatus(root, { onAction: (action: any) => w.calls.push(action) });
        const action = root.querySelector('button'); let direct = 0; action.addEventListener('click', () => direct++); action.focus();
        const original = root.outerHTML; let invalid = 0;
        for (const bad of [{ ...snapshot, generation: -1 }, { ...snapshot, actions: [{ key: 'view', label: 'x', href: 'javascript:alert(1)' }] }]) { try { controller.set(bad); } catch { invalid++; } }
        const unchanged = root.outerHTML === original && document.activeElement === action;
        const stale = controller.set({ ...snapshot, generation: 1 }); const collision = controller.set({ ...snapshot, identity: 'task:10' });
        const advanced = controller.set({ ...snapshot, identity: 'task:10', generation: 3, state: 'running', label: '运行中' });
        const old = controller.set({ ...snapshot, state: 'completed' });
        const preserved = root.querySelector('button') === action && document.activeElement === action;
        const aliasUrl = '/static/js/lq/business.js?second'; const alias = await import(aliasUrl); let cycles = true;
        for (let i = 0; i < 20; i++) { cycles &&= alias.jobStatus(root) === controller; const previous = controller; previous.destroy(); controller = api.jobStatus(root, { onAction: (a: any) => w.calls.push(a) }); previous.destroy(); cycles &&= api.jobStatus(root) === controller && previous.set(snapshot) === false; }
        w.jobController = controller;
        return { invalid, unchanged, stale, collision, advanced, old, preserved, cycles, calls: w.calls.length, direct, progressValue: root.querySelector('progress').getAttribute('value'), elapsed: root.querySelector('[data-lq-job-elapsed]').textContent };
    });
    expect(result).toEqual({ invalid: 2, unchanged: true, stale: false, collision: false, advanced: true, old: false, preserved: true, cycles: true, calls: 0, direct: 0, progressValue: null, elapsed: '12 秒' });
    await page.getByRole('button', { name: '查看结果' }).click();
    expect(await page.evaluate(() => (window as any).calls)).toEqual([{ key: 'view', identity: 'task:10', generation: 3 }]);
    const disabledFocus = await page.evaluate(() => {
        const w = window as any, snapshot = { identity: 'task:10', generation: 3, state: 'result_ready', label: '可查看' };
        w.jobController.set({ ...snapshot, actions: [{ key: 'view', label: '查看结果', href: '/result/10' }] });
        const root = document.querySelector('.lq-job')!, link = root.querySelector('a') as HTMLElement; link.focus();
        w.jobController.set({ ...snapshot, actions: [{ key: 'view', label: '查看结果', href: '/result/10', disabled: true }] });
        return document.activeElement === root;
    });
    expect(disabledFocus).toBe(true);
    await page.evaluate(() => (window as any).jobController.set({ identity: 'task:10', generation: 3, state: 'superseded', label: '已被替代' }));
    await expect(page.locator('.lq-job__superseded')).toBeVisible();
    expect(await page.locator('.lq-job').evaluate(node => document.activeElement === node)).toBe(true);
    await expect(page.locator('#draft')).toHaveValue('保留输入'); expect(requests).toEqual([]);
});

test('LQ QuestionNavigator is controlled, exposes combinations, preserves focus and never takes the existing island marker', async ({ page }) => {
    await mount(page);
    await page.evaluate(questions => {
        const w = window as any, root = w.business.question_navigator(questions); document.getElementById('fixture')!.append(root);
        w.selections = []; w.questionController = w.business.questionNavigator(root, { onSelect: (item: any) => w.selections.push(item) });
    }, questions);
    const second = page.locator('[data-lq-question=q2]'); await second.focus(); await page.keyboard.press('Enter'); await page.keyboard.press('Space');
    expect(await page.evaluate(() => (window as any).selections)).toEqual([{ id: 'q2', index: 2 }, { id: 'q2', index: 2 }]);
    await expect(page.locator('[data-lq-question=q1]')).toHaveAttribute('aria-current', 'step'); await expect(page.locator('[data-lq-question=q3]')).toBeDisabled();
    await expect(page.locator('[data-lq-question=q1]')).toHaveAccessibleName('第1题，已作答，当前题，已标记，有错误，附件上传中');
    const result = await page.evaluate(async questions => {
        const w = window as any, root = document.querySelector('.lq-nav-grid')!, active = document.activeElement;
        const original = root.outerHTML; let rejected = false;
        try { w.questionController.set({ groups: [{ ...questions.groups[0], items: questions.groups[0].items.map(i => ({ ...i, current: true })) }] }); } catch { rejected = true; }
        const untouched = root.outerHTML === original && document.activeElement === active;
        w.questionController.set({ ...questions, groups: [{ ...questions.groups[0], items: questions.groups[0].items.map(i => ({ ...i, current: i.id === 'q2' })) }] });
        const focus = document.activeElement === active;
        const url = '/static/js/lq/business.js?nav-alias'; const alias = await import(url); let cycles = true;
        for (let i = 0; i < 20; i++) { const old = w.questionController; cycles &&= alias.questionNavigator(root) === old; old.destroy(); w.questionController = alias.questionNavigator(root); old.destroy(); cycles &&= alias.questionNavigator(root) === w.questionController; }
        w.questionController.set({ groups: [] }); const removedFocus = document.activeElement === root; w.questionController.destroy();
        return { rejected, untouched, focus, cycles, removedFocus, marker: root.hasAttribute('data-submission-jump-managed') };
    }, questions);
    expect(result).toEqual({ rejected: true, untouched: true, focus: true, cycles: true, removedFocus: true, marker: false });
    await expect(page.getByText('暂无题目')).toBeVisible(); await expect(page.locator('#draft')).toHaveValue('保留输入');
});

test('LQ JobStatus and QuestionNavigator remain passive through twenty mount cycles and elapsed never self-advances', async ({ page }) => {
    await page.clock.install({ time: new Date('2026-09-20T04:00:00Z') }); const requests = await mount(page);
    await page.clock.pauseAt(new Date('2026-09-20T04:00:10Z'));
    const timers = await page.evaluate(questions => {
        const w = window as any, api = w.business, host = document.getElementById('fixture')!;
        const originalInterval = window.setInterval, originalTimeout = window.setTimeout;
        let count = 0;
        window.setInterval = ((...args: any[]) => { count++; return (originalInterval as any)(...args); }) as any;
        window.setTimeout = ((...args: any[]) => { count++; return (originalTimeout as any)(...args); }) as any;
        for (let i = 0; i < 20; i++) {
            const job = api.job_status({ identity: 'task:9', generation: i, state: 'running', label: '运行中', elapsed: '12 秒' });
            const nav = api.question_navigator(questions); host.replaceChildren(job, nav);
            const jobs = api.jobStatus(job), navigation = api.questionNavigator(nav);
            jobs.destroy(); navigation.destroy();
        }
        window.setInterval = originalInterval; window.setTimeout = originalTimeout; return count;
    }, questions);
    expect(timers).toBe(0); await page.clock.runFor(310000);
    await expect(page.locator('[data-lq-job-elapsed]')).toHaveText('12 秒'); expect(requests).toEqual([]);
});

test('LQ JobStatus presents the actual Agent SSE and polling controller snapshots without owning requests or applying results', async ({ page }) => {
    const requests = await mount(page);
    const task: any = { id: 9, is_owner: true, is_active: true, status: 'running', runtime_status: 'waiting_input', status_label: '等待你的回答',
        elapsed_seconds: 12, title: '课堂活动方案', events: [], questions: [] };
    const calls: string[] = [];
    let hold = false, held = false, release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route('https://lq-business.test/api/agent-tasks/**', async route => {
        calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
        if (new URL(route.request().url()).pathname.endsWith('/events')) return route.fulfill({ json: { task_id: 9, events: [{ id: 2, event_type: 'question_closed', detail: { question_id: 'request-1' } }], last_event_id: 2 } });
        const snapshot = JSON.stringify({ task });
        if (hold) { hold = false; held = true; await gate; }
        return route.fulfill({ contentType: 'application/json', body: snapshot });
    });
    await page.evaluate(() => {
        const host = document.createElement('div'); host.id = 'ai-chat-modal'; host.style.display = 'block';
        host.style.setProperty('position', 'static', 'important');
        const messages = document.createElement('div'); messages.id = 'ai-chat-messages-box'; host.append(messages); document.body.append(host);
        const w = window as any;
        w.jobRoot = w.business.job_status({ identity: 'task:9', generation: 0, family: 'agent', state: 'queued', label: '排队中' });
        document.getElementById('fixture')!.append(w.jobRoot); w.applied = 0;
        w.jobController = w.business.jobStatus(w.jobRoot, { onAction: () => w.applied++ });
    });
    const source = fs.readFileSync('static/js/ai_workspace_widget.js', 'utf8');
    const widget = source.slice(0, source.lastIndexOf("if (document.readyState === 'loading')"));
    await page.addScriptTag({ content: `(() => { window.AI_WORKSPACE_WIDGET_CONFIG={taskCenterEnabled:true}; ${widget}
        currentChatSurface=()=>({messagesBox:document.querySelector('#ai-chat-messages-box'),scrollToBottom:()=>{}});
        refreshAgentComposerChrome=()=>{};renderAgentStarters=()=>{};
        window.EventSource=class {constructor(){window.testTaskStream=this;}close(){}};
        const originalRender=renderTaskDetail;
        renderTaskDetail=(task,options)=>{originalRender(task,options);window.jobController.set({identity:'task:'+task.id,
            generation:taskDetailRequestVersions.get(Number(task.id)),family:'agent',state:task.runtime_status||task.status,
            label:task.status_label,elapsed:task.elapsed_seconds+' 秒',actions:task.runtime_status==='completed'?[{key:'view',label:'查看结果'}]:[]});};
        window.agentFixture={refresh:()=>loadTaskDetail(9),stream:()=>startTaskEventStream(9),poll:()=>{taskEventStreamDisabled=true;return pollTaskEventsOnce();},dispose:()=>closeTaskEventStream(9)};
    })();` });
    await page.evaluate(() => (window as any).agentFixture.refresh());
    await expect(page.locator('.lq-job')).toHaveAttribute('data-lq-job-state', 'waiting_input');
    task.runtime_status = 'running'; task.status_label = '运行中';
    await page.evaluate(() => { (window as any).agentFixture.stream(); (window as any).testTaskStream.onmessage({ data: JSON.stringify({ task_id: 9, events: [{ id: 1, event_type: 'question_requested', detail: { question_id: 'request-1' } }], last_event_id: 1 }) }); });
    await expect(page.locator('.lq-job')).toHaveAttribute('data-lq-job-state', 'running');
    task.runtime_status = 'completed'; task.status = 'completed'; task.status_label = '已完成'; task.is_active = false;
    await page.evaluate(() => (window as any).agentFixture.poll());
    await expect(page.locator('.lq-job')).toHaveAttribute('data-lq-job-state', 'completed');
    expect(await page.evaluate(() => (window as any).applied)).toBe(0);
    hold = true; task.runtime_status = 'waiting_input'; task.status_label = '旧任务';
    await page.evaluate(() => { (window as any).pendingDetail = (window as any).agentFixture.refresh(); });
    await expect.poll(() => held).toBe(true);
    task.runtime_status = 'completed'; task.status_label = '最新完成';
    await page.evaluate(() => (window as any).agentFixture.refresh()); release();
    await page.evaluate(() => (window as any).pendingDetail);
    await expect(page.locator('.lq-job')).toContainText('最新完成');
    await page.locator('.lq-job').getByRole('button', { name: '查看结果' }).click();
    expect(await page.evaluate(() => (window as any).applied)).toBe(1);
    expect(calls.filter(call => call.endsWith('/events'))).toHaveLength(1); expect(calls.every(call => call.startsWith('GET '))).toBe(true);
    expect(requests).toEqual([]); await expect(page.locator('#draft')).toHaveValue('保留输入');
    await page.evaluate(() => { (window as any).agentFixture.dispose(); (window as any).jobController.destroy(); });
});

for (const appearance of ['light', 'dark']) for (const palette of ['indigo', 'teal', 'rose', 'sky', 'mint', 'violet']) {
    test(`LQ business ${appearance}/${palette} long text and state combinations fit 390 with axe`, async ({ page }) => {
        await page.setViewportSize({ width: 390, height: 844 }); await mount(page);
        await page.evaluate(({ appearance, palette, clockProps, questions }) => {
            Object.assign(document.documentElement.dataset, { appearance, uiPalette: palette });
            const w = window as any, api = w.business, host = document.getElementById('fixture')!;
            for (const phase of ['regular', 'late', 'closed', 'none']) host.append(api.deadline_clock({ ...clockProps, deadline_phase: phase, absolute: '2026年9月20日13:00:01', label: '首次截止与个人重交', value: '12:34:56' }));
            host.append(api.job_status({ identity: 'task:9', generation: 1, state: 'superseded', label: '已被新的任务替代', elapsed: '2 小时 5 分钟', progress: { label: '任务进度未知' },
                message: '业务结果只在明确查看和核对后使用。'.repeat(8), actions: [{ key: 'view', label: '查看最新结果并重新核对全部内容' }] }));
            host.append(api.question_navigator({ ...questions, groups: [{ ...questions.groups[0], label: '综合知识与应用能力'.repeat(6), items: [...questions.groups[0].items,
                { id: 'q4', index: 4, answered: true }, { id: 'q5', index: 5, flagged: true }, { id: 'q6', index: 6, pendingUpload: true }, { id: 'q7', index: 7, error: true }] }] }));
        }, { appearance, palette, clockProps, questions });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        const result = await new AxeBuilder({ page }).include('main').analyze(); expect(result.violations).toEqual([]);
        if (palette === 'rose') await page.screenshot({ path: `.codex-temp/lq-business-${appearance}-390.png`, fullPage: true });
    });
}

test('LQ business coarse question and result actions are 44px and forced colors retain visible current state', async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    const page = await context.newPage();
    try {
        await mount(page);
        await page.evaluate(questions => {
            const api = (window as any).business, host = document.getElementById('fixture')!;
            host.append(api.question_navigator(questions), api.job_status({ identity: 'job:7', generation: 1, state: 'result_ready', label: '结果可查看', actions: [{ key: 'view', label: '查看' }] }));
        }, questions);
        for (const node of await page.locator('.lq-nav-grid__item,.lq-job__actions .lq-btn').all()) {
            const box = (await node.boundingBox())!; expect(box.width).toBeGreaterThanOrEqual(44); expect(box.height).toBeGreaterThanOrEqual(44);
        }
        await page.emulateMedia({ forcedColors: 'active' });
        const colors = await page.locator('.is-current').evaluate(node => { const s = getComputedStyle(node); return [s.color, s.backgroundColor, s.borderTopStyle]; });
        expect(colors[0]).not.toBe(colors[1]); expect(colors[2]).toBe('solid');
    } finally { await context.close(); }
});
