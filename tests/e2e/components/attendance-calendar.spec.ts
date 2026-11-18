import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const origin = 'https://term-attendance.test';
const assets = process.env.ATTENDANCE_PRODUCTION_ASSETS === '1' ? JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).entries : {};
const asset = (name: string) => '/static/' + (assets[name] || name);
const students = [1, 2, 3].map(id => ({ student_id: id, name: '同学' + id, student_number: 'S00' + id }));
const lesson = { session_id: 10, title: '分组交换', date: '2026-11-16', section_text: '第1–2节', schedule_status: 'scheduled',
    round_id: 90, recorded: true, status: 'ABSENT', source: 'local', can_edit: true, school_cached: false, counts: { PRESENT: 2, ABSENT: 1 } };
const sessions = [lesson, { ...lesson, session_id: 11, title: '课堂实验', section_text: '第5–6节', counts: { PRESENT: 3 },
    round_id: null, source: 'smart_synced', school_cached: true, status: 'PRESENT', schedule_status_label: '调自 2026-11-19' },
    { ...lesson, session_id: 12, title: '拓扑结构', date: '2026-11-25', counts: {}, round_id: null, recorded: false, can_edit: false }];
async function fixture(page: Page, { studentId = null, conflict = false, align = false, loseResponse = false, createAvailable = false, classSize = 3 }: {studentId?: number|null; conflict?: boolean; align?: boolean; loseResponse?: boolean; createAvailable?: boolean; classSize?: number} = {}) {
    const roster = classSize === 3 ? students : Array.from({ length: classSize }, (_, i) => ({ student_id: i+1, name: '同学' + (i+1), student_number: 'S' + String(i+1).padStart(3,'0') }));
    const saves: any[] = [], previews: any[] = [], sends: any[] = [], errors: string[] = [], aligns: string[] = [], readbacks: string[] = [], creations: any[] = [];
    page.on('pageerror', error => errors.push(error.message));
    let revision = 1, sent = false;
    await page.route(origin + '/**', async route => {
        const url = new URL(route.request().url()), target = url.pathname;
        const api = (value: any, status = 200) => route.fulfill({ json: value, status });
        if (target.startsWith('/api/')) {
            if (target.endsWith('/calendar')) return api({ students: roster, sessions, offering: { course_name: '计算机网络', semester: '2026 秋季' },
                calendar: { today: '2026-11-18', start: '2026-09-01', end: '2027-01-20', days: [{ date: '2026-11-15', label: '调休上课', makeup_for_date: '2026-11-20' }] } });
            if (target.includes('/calendar/sessions/')) {
                const id = Number(target.split('/').pop());
                if (route.request().method() === 'PATCH') {
                    saves.push(route.request().postDataJSON()); if (conflict) { conflict = false; revision++; return api({ detail: '版本已变化' }, 409); }
                    return api({ round_id: 90, saved: 1 });
                }
                const scoped = Number(url.searchParams.get('student_id'));
                const session = sessions.find(row => row.session_id === id)!;
                return api({ session: { ...session, slot_revision: (revision === 1 ? 'a' : 'b').repeat(64), ...(revision > 1 ? { date: '2026-11-17', section_text: '第3–4节' } : {}) },
                    records: roster.filter(s => !scoped || s.student_id === scoped)
                    .map(row => ({ ...row, revision: id === 11 ? 0 : revision, status: 'PENDING', effective_status: id === 11 ? 'PRESENT' : 'ABSENT', source: id === 11 ? 'smart_synced' : 'local', editable: true, expected: true })) });
            }
            if (target.endsWith('/calendar/align')) { aligns.push('calendar'); return api({ session_count: 3, message: '已核对课表' }); }
            if (target.endsWith('/match')) { aligns.push('lesson'); align = false; return api({ binding: { state: 'matched' } }); }
            if (target.endsWith('/calendar-preview')) {
                const id = Number(target.split('/').at(-3)); previews.push({ id, ...route.request().postDataJSON() });
                if (createAvailable) return !route.request().postDataJSON().student_ids ? api({ state: 'create_available', preview_token: 'missing-' + id, message: '可授权新建学校点名' })
                    : api({ state: 'school_missing', message: '所选学生保留本地记录' });
                if (id === 11) return api({ state: 'school_only', message: '学校记录已回读' });
                if (align) return api({ state: 'alignment_required', message: '请核对课次' });
                return api({ state: 'ready', preview_token: 'pinned-' + id, message: '可以推送', counts: { needs_teacher_action: 1 }, rows: [{
                    student_id: 2, local_status: 'PRESENT', remote_status: 'UNCHECKED', target_status: 'CHECKED', state: 'needs_teacher_action' }] });
            }
            if (target.endsWith('/calendar-sync')) {
                sends.push(route.request().postDataJSON()); sent = true;
                if (loseResponse) return api({ detail: '响应中断' }, 503);
                return api({ state: 'queued', message: '已交由后台处理' });
            }
            if (target.endsWith('/calendar-create')) { creations.push({ id: Number(target.split('/').at(-3)), ...route.request().postDataJSON() });
                return api({ state: 'queued', message: '新建任务已排队' }); }
            if (sent && target.endsWith('/readback')) { readbacks.push(target); return api({ state: 'confirmed', message: '学校明确回读一致' }); }
            if (sent && target.includes('/calendar-sync/') && loseResponse) return api({ state: 'outcome_unknown', message: '学校结果待核实' });
            if (sent && target.includes('/calendar-sync/')) return api({ state: 'confirmed', message: '学校回读已确认' });
            return api({ detail: 'unexpected path ' + target }, 500);
        }
        if (target.startsWith('/static/')) {
            const file = path.resolve('.' + target);
            if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404 });
            return route.fulfill({ body: fs.readFileSync(file), contentType: target.endsWith('.js') ? 'text/javascript' : target.endsWith('.css') ? 'text/css' : 'application/octet-stream' });
        }
        const appearance = studentId ? 'dark' : 'light';
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-lq-scene-tone="${appearance}"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"><body><button id="open">查看学期出勤</button><script type="module">import {openAttendanceCalendar} from '${asset('js/attendance_calendar.js')}';window.APP_CONFIG={userInfo:{id:40}};document.querySelector('#open').onclick=e=>openAttendanceCalendar({classOfferingId:7,studentId:${studentId},trigger:e.currentTarget});window.ready=true;</script></body></html>` });
    });
    await page.goto(origin); await page.waitForFunction(() => (window as any).ready);
    await page.getByRole('button', { name: '查看学期出勤' }).click();
    return { saves, previews, sends, errors, aligns, readbacks, creations };
}

test('semester calendar shows same-day school and local lessons and keeps personal correction scope on mobile', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ colorScheme: 'dark' });
    const { saves, previews, sends, errors } = await fixture(page, { studentId: 2, conflict: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    const day = calendar.getByRole('button', { name: /2026-11-16，/ });
    await expect(day).toContainText('缺课'); await expect(day).toContainText('出勤');
    await expect(day).toContainText('调自'); await expect(calendar.getByText(/调休上课/)).toBeVisible();
    await calendar.getByRole('button', { name: /2026-11-15，调休上课/ }).click();
    const holiday = page.getByRole('dialog', { name: '2026-11-15 校历' });
    await expect(holiday.getByText(/补 2026-11-20 的课/)).toBeVisible();
    await page.keyboard.press('Escape'); await expect(holiday).toHaveCount(0);
    await expect(calendar.getByRole('button', { name: /2026-11-25，/ })).toContainText('未点名');
    expect(await calendar.evaluate(n => n.scrollWidth <= n.clientWidth + 1)).toBeTruthy();
    await page.screenshot({ path: testInfo.outputPath('personal-mobile.png') });
    await day.click(); const detail = page.getByRole('dialog', { name: /2026-11-(16|17) 出勤记录/ });
    await detail.getByRole('button', { name: '设为出勤' }).click();
    await detail.getByRole('textbox', { name: /更正理由/ }).fill('核对签到表');
    await detail.getByRole('button', { name: '保存并推送' }).click();
    await expect(detail.getByRole('alert')).toContainText('已变化');
    await expect(detail.getByRole('textbox', { name: /更正理由/ })).toHaveValue('核对签到表');
    await detail.getByRole('button', { name: '读取最新名单' }).click();
    await expect(detail.getByText('拟改 · 出勤')).toBeVisible();
    await expect(detail.getByRole('heading', { name: '2026-11-17 出勤记录' })).toBeVisible();
    await expect(detail.getByText(/课次已调整为 2026-11-17 · 第3–4节/)).toBeVisible();
    await detail.getByRole('button', { name: '保存并推送' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.getByRole('button', { name: '推送 1 个课次的差异' }).click();
    expect(saves.map(item => item.records)).toEqual([[{ student_id: 2, status: 'PRESENT', revision: 1 }], [{ student_id: 2, status: 'PRESENT', revision: 2 }]]);
    expect(saves.map(item => item.expected_slot_revision)).toEqual(['a'.repeat(64), 'b'.repeat(64)]);
    expect(previews).toEqual([{ id: 10, student_ids: [2] }]); expect(sends[0].preview_token).toBe('pinned-10');
    await sync.getByRole('button', { name: '查询此次回执' }).click(); await expect(sync.getByText('学校回读已确认')).toBeVisible();
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(page.getByRole('button', { name: '查看学期出勤' })).toBeFocused();
    expect(errors).toEqual([]);
});

test('semester calendar previews named students across selected lessons and never broadens school-only results', async ({ page }, testInfo) => {
    const { previews, sends, aligns, errors } = await fixture(page, { align: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    const day = calendar.getByRole('button', { name: /2026-11-16，/ });
    await expect(day.locator('[data-tone="warning"]')).toHaveText('到2 未到1');
    await expect(day.locator('[data-tone="success"]')).toHaveText('到3 未到0');
    await page.screenshot({ path: testInfo.outputPath('class-desktop.png') });
    await calendar.getByRole('button', { name: '同步到智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.getByRole('button', { name: '对齐课程与课次' }).click(); await expect(sync.getByText('已核对课表')).toBeVisible();
    await sync.locator('select').nth(1).selectOption('selected', { force: true });
    await sync.locator('select').nth(2).selectOption(['1', '3'], { force: true });
    await sync.getByRole('button', { name: '核对并预览' }).click();
    await expect(sync.getByText('学校记录已回读')).toBeVisible(); expect(sends).toHaveLength(0);
    await sync.getByRole('button', { name: '核对此课对齐' }).click();
    await sync.getByRole('button', { name: '推送 1 个课次的差异' }).click();
    await expect(sync.getByText('已交由后台处理')).toBeVisible();
    expect(aligns).toEqual(['calendar', 'lesson']); expect(previews).toEqual([{ id: 10, student_ids: [1, 3] }, { id: 11, student_ids: [1, 3] }, { id: 10, student_ids: [1, 3] }]);
    expect(sends).toHaveLength(1); expect(sends[0].preview_token).toBe('pinned-10'); expect(errors).toEqual([]);
});

test('class teacher changes the whole roster then one student in the other same-day lesson with no school write', async ({ page }) => {
    const { previews, sends, saves, errors } = await fixture(page);
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: '上月' }).click();
    await expect(calendar.getByRole('heading', { name: '2026年10月' })).toBeVisible();
    await calendar.getByRole('button', { name: '下月' }).click();
    await calendar.getByRole('button', { name: /2026-11-16，/ }).click();
    const detail = page.getByRole('dialog', { name: '2026-11-16 出勤记录' });
    await detail.locator('select').selectOption('11', { force: true });
    await detail.getByRole('button', { name: '选择全部应到学生' }).click();
    await detail.getByRole('button', { name: '设为出勤' }).click();
    await detail.getByRole('button', { name: '清除选择' }).click();
    await detail.getByRole('checkbox', { name: '同学2 · S002' }).check();
    await detail.getByRole('button', { name: '设为迟到' }).click();
    await detail.getByRole('textbox', { name: /更正理由/ }).fill('核对当日实验记录');
    await detail.getByRole('button', { name: '保存到本地' }).click();
    await expect(detail.getByText('已保存 3 人的本地记录。')).toBeVisible();
    expect(saves[0].round_id).toBe(null);
    expect(saves[0].records.map((r: any) => [r.student_id, r.status])).toEqual([[1, 'PRESENT'], [2, 'LATE'], [3, 'PRESENT']]);
    expect(previews).toHaveLength(0); expect(sends).toHaveLength(0); expect(errors).toEqual([]);
});

test('ambiguous send recovers by the original receipt and an explicit readback without resending', async ({ page }) => {
    const { sends, readbacks, errors } = await fixture(page, { loseResponse: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: '同步到智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.getByRole('button', { name: '核对并预览' }).click();
    await sync.getByRole('button', { name: '推送 1 个课次的差异' }).click();
    await expect(sync.getByText('待查回执')).toBeVisible();
    await sync.getByRole('button', { name: '查询此次回执' }).click();
    await expect(sync.getByText('学校结果待核实')).toBeVisible();
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await calendar.getByRole('button', { name: '同步到智慧课堂' }).click();
    await sync.getByRole('button', { name: '查看近期推送回执' }).click();
    await sync.getByRole('button', { name: '回读学校' }).click();
    await expect(sync.getByText('学校明确回读一致')).toBeVisible();
    expect(sends).toHaveLength(1); expect(readbacks).toHaveLength(1);
    expect(readbacks[0]).toContain('/10/external/calendar-sync/' + sends[0].idempotency_key + '/readback');
    expect(errors).toEqual([]);
});

test('missing remote roll calls require an individual lesson opt-in, full-roster scope, and a fresh geolocation', async ({ page, context }) => {
    const { creations, sends, errors } = await fixture(page, { createAvailable: true });
    await context.setGeolocation({ latitude: 24.012, longitude: 108.034 });
    await context.grantPermissions(['geolocation'], { origin });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: '同步到智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.getByRole('button', { name: '核对并预览' }).click();
    const boxes = sync.getByRole('checkbox', { name: '为本课全班新建学校点名' });
    await expect(boxes).toHaveCount(2); await expect(boxes.first()).not.toBeChecked();
    await expect(sync.getByRole('button', { name: '定位并新建学校点名' })).toBeDisabled();
    await boxes.last().check();
    const create = sync.getByRole('button', { name: '定位并新建 1 个学校点名' });
    await create.click(); const consent = page.getByRole('dialog', { name: '授权新建 1 个全班点名？' });
    await expect(consent).toContainText('课堂实验'); await expect(consent).not.toContainText('分组交换');
    await consent.getByRole('button', { name: '取消' }).click();
    expect(creations).toHaveLength(0); await create.click(); await consent.getByRole('button', { name: '定位并授权新建' }).click();
    await expect(sync.getByText('新建任务已排队')).toBeVisible();
    expect(creations).toHaveLength(1); expect(creations[0]).toMatchObject({ id: 11, preview_token: 'missing-11', authorize_create: true,
        location: { latitude: 24.012, longitude: 108.034 } }); expect(Date.now() - creations[0].location.captured_at_ms).toBeLessThan(300000);
    await sync.locator('select').nth(1).selectOption('selected', { force: true });
    await sync.locator('select').nth(2).selectOption(['1', '3'], { force: true });
    await sync.getByRole('button', { name: '核对并预览' }).click();
    await expect(sync.getByText('所选学生保留本地记录')).toHaveCount(2);
    await expect(boxes).toHaveCount(0); await expect(create).toHaveCount(0);
    expect(sends).toHaveLength(0); expect(errors).toEqual([]);
});

test('a 200-student register keeps selections across pagination and search with thirty visible records at most', async ({ page }) => {
    const { saves, sends, errors } = await fixture(page, { classSize: 200 });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: /2026-11-16，/ }).click();
    const detail = page.getByRole('dialog', { name: '2026-11-16 出勤记录' });
    await expect(detail.getByRole('checkbox')).toHaveCount(30);
    await detail.getByRole('checkbox', { name: '同学1 · S001' }).check();
    await detail.getByRole('button', { name: '下一页' }).click();
    await detail.getByRole('checkbox', { name: '同学33 · S033' }).check();
    await detail.getByRole('searchbox', { name: '查找学生' }).fill('S199');
    await expect(detail.getByRole('checkbox')).toHaveCount(1);
    await detail.getByRole('button', { name: '设为病假' }).click();
    await expect(detail.getByText('2 人待保存')).toBeVisible();
    await detail.getByRole('textbox', { name: /更正理由/ }).fill('已核对请假材料');
    await detail.getByRole('button', { name: '保存到本地' }).click();
    await expect(detail.getByText('已保存 2 人的本地记录。')).toBeVisible();
    expect(saves[0].records.map((r: any) => r.student_id)).toEqual([1,33]);
    expect(sends).toHaveLength(0); expect(errors).toEqual([]);
});
