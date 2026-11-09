import { test, expect, Page, Route } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const origin = 'https://attendance-dashboard.test';
const manifest = process.env.ATTENDANCE_PRODUCTION_ASSETS === '1' ? JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')) : null;
const asset = (name: string) => '/static/' + (manifest ? manifest.entries[name] : name);
const summary = { counts: { PRESENT: 1, ABSENT: 1, PENDING: 1 }, expected: 3, confirmed: 2, pending: 1, attendance_rate: 50, session_count: 18, recorded_session_count: 3 };
const school = { session_id: 12, title: '第 3 次课 · 网络分层与实践', date: '2026-11-09', source: 'smart_synced', state: 'synced', status: 'ABSENT', source_key: 'api:71', source_fingerprint: 'fp-source-1', can_appeal: true };
const local = { session_id: 13, title: '第 4 次课 · 本地点名', date: '2026-11-10', source: 'local', state: 'running', local_round_id: 9, status: 'PENDING' };
const dashboard = (personal = false): any => ({ visible: true, offering: { id: 2, course_name: '合成计算机网络原理', semester: '2026–2027 学年第一学期' }, summary, rate_note: '未开展点名的课次不会记为缺课；待核对不计入分母。', sessions: [school, local], ...(personal ? { student: { student_id: 4, name: '合成同学四', student_number: 'S004' } } : { students: [{ student_id: 4, name: '合成同学四', student_number: 'S004', class_name: '合成班', summary }] }) });
const json = (route: Route, value: any, status = 200) => route.fulfill({ status, json: value });
type API = (route: Route, url: string, body: any) => Promise<any>;
async function fixture(page: Page, handler: API, role = 'teacher', query = '') {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await page.route(`${origin}/**`, async route => {
        const url = new URL(route.request().url()), pathname = url.pathname;
        if (pathname.startsWith('/api/')) return handler(route, decodeURIComponent(pathname), route.request().postData() ? route.request().postDataJSON() : null);
        if (pathname.startsWith('/static/')) {
            const target = path.resolve('.' + pathname);
            if (!target.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404 });
            return route.fulfill({ body: fs.readFileSync(target), contentType: pathname.endsWith('.css') ? 'text/css' : pathname.endsWith('.js') ? 'text/javascript' : 'application/octet-stream' });
        }
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"></head><body><button type="button" data-lq-component="button" class="lq-btn lq-btn--glass" id="cw-attendance-dashboard" hidden>${role === 'teacher' ? '签到统计' : '我的签到'}</button><script type="module">window.APP_CONFIG={classOfferingId:2,userInfo:{role:'${role}'}}; const m=await import('${asset('js/attendance_dashboard_entry.js')}'); m.initAttendanceDashboardEntry();window.entryState=m.attendanceEntryState;window.refreshEntry=()=>m.refreshAttendanceVisibility({force:true});window.ready=true;</script></body></html>` });
    });
    await page.goto(origin + query); await page.waitForFunction(() => (window as any).ready);
    return errors;
}

test('LQ dashboard unused attendance stays silent; later school sync reveals only its own session', async ({ page }) => {
    let enabled = false; const paths: string[] = [];
    const errors = await fixture(page, async (route, url) => {
        paths.push(url);
        if (url.endsWith('/visibility')) return json(route, { visible: enabled, session_ids: enabled ? [12] : [], local_session_ids: [] });
        return json(route, { detail: 'unexpected request' }, 500);
    }, 'student');
    await expect.poll(() => paths.length).toBe(1);
    await expect(page.getByRole('button', { name: '我的签到', exact: true })).toBeHidden();
    expect(await page.evaluate(() => (window as any).entryState(12))).toEqual({ visible: false, local: false });
    await expect(page.getByText('尚未开启')).toHaveCount(0); await expect(page.getByRole('alert')).toHaveCount(0);
    enabled = true; await page.evaluate(() => (window as any).refreshEntry());
    await expect(page.getByRole('button', { name: '我的签到', exact: true })).toBeVisible();
    expect(await page.evaluate(() => (window as any).entryState(12))).toEqual({ visible: true, local: false });
    expect(await page.evaluate(() => (window as any).entryState(13))).toEqual({ visible: false, local: false });
    expect(paths.every(url => url.endsWith('/visibility'))).toBeTruthy(); expect(errors).toEqual([]);
});

test('LQ dashboard teacher course overview and individual course history preserve source and denominator', async ({ page }) => {
    const errors = await fixture(page, async (route, url) => {
        if (url.endsWith('/students/4')) return json(route, dashboard(true));
        if (url.endsWith('/dashboard')) {
            if (new URL(route.request().url()).searchParams.get('session_id') === '12') return json(route, { ...dashboard(), sessions: [school], students: [{ ...dashboard().students![0], summary: { ...summary, attendance_rate: 0, recorded_session_count: 1 } }] });
            return json(route, dashboard());
        }
        return json(route, { detail: 'unexpected request' }, 500);
    });
    await page.getByRole('button', { name: '签到统计', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '课程签到统计', exact: true });
    await expect(dialog.getByText('智慧课堂同步', { exact: true })).toBeVisible();
    await expect(dialog.getByText('点名中', { exact: true })).toBeVisible();
    await expect(dialog.getByText('50.0%', { exact: true })).toBeVisible();
    await expect(dialog.getByText('未开展点名的课次不会记为缺课', { exact: false })).toBeVisible();
    await dialog.getByRole('button', { name: '查看该课学生', exact: true }).first().click();
    await expect(dialog.getByText('本课出勤率 0.0%', { exact: true })).toBeVisible();
    await dialog.getByRole('button', { name: '课程总览', exact: true }).click();
    await dialog.getByRole('button', { name: '学生汇总', exact: true }).click();
    await dialog.getByRole('button', { name: '查看个人课次', exact: true }).click();
    await expect(dialog.getByRole('heading', { name: '合成同学四 · 本课程签到' })).toBeVisible();
    await expect(dialog.getByRole('button', { name: '学生汇总', exact: true })).toBeHidden();
    await page.screenshot({ path: '.codex-temp/attendance-dashboard-desktop.png', fullPage: true });
    await dialog.getByRole('button', { name: '课程总览', exact: true }).click();
    await expect(dialog.getByRole('heading', { name: '合成计算机网络原理' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('button', { name: '签到统计', exact: true })).toBeFocused();
    expect(errors).toEqual([]);
});

test('LQ dashboard school-only student appeal keeps failed draft and closes nested layer safely on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const paths: string[] = [], posted: any[] = []; let submitted = false;
    const errors = await fixture(page, async (route, url, body) => {
        paths.push(url);
        if (url.endsWith('/visibility')) return json(route, { visible: true, session_ids: [12], local_session_ids: [] });
        if (url.endsWith('/dashboard')) return json(route, { ...dashboard(true), sessions: [{ ...school, can_appeal: !submitted, appeal: submitted ? { id: 72, status: 'pending' } : null }] });
        if (url.endsWith('/appeals')) { posted.push(body); if (posted.length === 1) return json(route, { detail: '合成网络暂时故障' }, 503); submitted = true; return json(route, { request: { id: 72, status: 'pending' } }); }
        if (url.endsWith('/records/api:71')) return json(route, { record: { ...school, source_fingerprint: 'fp-source-1' }, can_appeal: !submitted, current_source_available: true, appeal: submitted ? { id: 72, status: 'pending', reason: posted[1].reason } : null });
        return json(route, { detail: 'unexpected request' }, 500);
    }, 'student');
    await page.getByRole('button', { name: '我的签到', exact: true }).click();
    const dashboardDialog = page.getByRole('dialog', { name: '我的课程签到', exact: true });
    await expect(dashboardDialog.getByRole('button', { name: '学生汇总', exact: true })).toBeHidden();
    await dashboardDialog.getByRole('button', { name: '查看并申诉', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '签到记录与申诉', exact: true });
    await dialog.getByRole('textbox', { name: '申诉说明', exact: true }).fill('当时在教室参与课堂练习，请老师核对。');
    await dialog.getByRole('button', { name: '提交本人申诉', exact: true }).click();
    await expect(dialog.getByRole('alert')).toContainText('合成网络暂时故障');
    await expect(dialog.getByRole('textbox', { name: '申诉说明', exact: true })).toHaveValue('当时在教室参与课堂练习，请老师核对。');
    await page.keyboard.press('Escape');
    await page.getByRole('dialog', { name: '放弃尚未提交的说明？' }).getByRole('button', { name: '取消', exact: true }).click();
    await expect(dialog.getByRole('textbox', { name: '申诉说明', exact: true })).toHaveValue('当时在教室参与课堂练习，请老师核对。');
    await dialog.getByRole('button', { name: '提交本人申诉', exact: true }).click();
    await expect(dialog.getByText('申诉：处理中', { exact: true })).toBeVisible();
    expect(posted[1]).toEqual({ reason: '当时在教室参与课堂练习，请老师核对。', source_fingerprint: 'fp-source-1' });
    expect(paths.some(url => url.includes('/students/'))).toBeFalsy();
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBeTruthy();
    await page.screenshot({ path: '.codex-temp/attendance-dashboard-mobile.png', fullPage: true });
    await page.keyboard.press('Escape'); await expect(dashboardDialog).toBeVisible(); expect(errors).toEqual([]);
});

test('LQ dashboard teacher lost-source appeal deep link remains reviewable without allowing stale approval', async ({ page }) => {
    const paths: string[] = [];
    const errors = await fixture(page, async (route, url) => {
        paths.push(url);
        if (url.endsWith('/students/4')) return json(route, { ...dashboard(true), sessions: [] });
        if (url.endsWith('/approvals/72')) return json(route, { request: { id: 72, status: 'pending', can_decide: true, request_type: 'smart_attendance_appeal', reason: '请核对旧记录', detail: { attendance: { record: school, source_fingerprint: null, current_source_available: false, source_changed: true } } } });
        return json(route, { detail: 'unexpected request' }, 500);
    }, 'teacher', '?attendance_dashboard=1&student_id=4&session_id=12&smart_appeal_id=72');
    await expect(page.getByRole('dialog', { name: '签到记录与申诉', exact: true })).toBeVisible();
    await expect(page.getByText('批准仅更正平台统计，不会修改学校端记录。', { exact: true })).toBeVisible();
    expect(paths.some(url => url.endsWith('/students/4'))).toBeTruthy();
    await expect(page.getByRole('button', { name: '确认本地更正', exact: true })).toBeDisabled();
    await expect(page.getByRole('button', { name: '维持结果并说明', exact: true })).toBeEnabled();
    expect(paths.some(url => url.endsWith('/approvals/72'))).toBeTruthy();
    expect(errors).toEqual([]);
});
