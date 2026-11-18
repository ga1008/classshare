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
async function fixture(page: Page, { studentId = null, conflict = false, align = false, loseResponse = false, createAvailable = false, classSize = 3, receiptMissing = false, activities = false, initialUnknown = false }: {studentId?: number|null; conflict?: boolean; align?: boolean; loseResponse?: boolean; createAvailable?: boolean; classSize?: number; receiptMissing?: boolean; activities?: boolean; initialUnknown?: boolean} = {}) {
    const roster = classSize === 3 ? students : Array.from({ length: classSize }, (_, i) => ({ student_id: i+1, name: '同学' + (i+1), student_number: 'S' + String(i+1).padStart(3,'0') }));
    const saves: any[] = [], sends: any[] = [], errors: string[] = [], aligns: string[] = [], readbacks: string[] = [], creations: any[] = [];
    page.on('pageerror', error => errors.push(error.message));
    let revision = 1;
    const receipts = new Set<string>(), localReceipts = new Map<string, object>(), imported = new Set<number>();
    const calendarSessions = () => sessions.map(row => row.session_id !== 11 ? row : { ...row,
        ...(activities ? { status: 'STUDY_TOUR', counts: { STUDY_TOUR: 1, PRACTICE: 1, VOLUNTEER: 1 } } : {}),
        ...(imported.size ? { round_id: 91, source: 'local' } : {}) });
    await page.route(origin + '/**', async route => {
        const url = new URL(route.request().url()), target = url.pathname;
        const api = (value: any, status = 200) => route.fulfill({ json: value, status });
        if (target.startsWith('/api/')) {
            if (target.endsWith('/calendar')) return api({ students: roster, sessions: calendarSessions(), offering: { course_name: '计算机网络', semester: '2026 秋季' },
                calendar: { today: '2026-11-18', start: '2026-09-01', end: '2027-01-20', days: [{ date: '2026-11-15', label: '调休上课', makeup_for_date: '2026-11-20' }] } });
            if (target.includes('/calendar/sessions/')) {
                const id = Number(target.split('/').pop());
                if (route.request().method() === 'PATCH') {
                    saves.push(route.request().postDataJSON()); if (conflict) { conflict = false; revision++; return api({ detail: '版本已变化' }, 409); }
                    return api({ round_id: 90, saved: 1 });
                }
                const scoped = Number(url.searchParams.get('student_id'));
                const session = calendarSessions().find(row => row.session_id === id)!;
                return api({ session: { ...session, slot_revision: (revision === 1 ? 'a' : 'b').repeat(64), ...(revision > 1 ? { date: '2026-11-17', section_text: '第3–4节' } : {}) },
                    records: roster.filter(s => !scoped || s.student_id === scoped)
                    .map(row => ({ ...row, revision: id === 11 && !imported.has(row.student_id) ? 0 : revision, status: id === 11 && imported.has(row.student_id) ? 'PRESENT' : 'PENDING', effective_status: id === 11 ? 'PRESENT' : 'ABSENT', source: id === 11 && !imported.has(row.student_id) ? 'smart_synced' : 'local', editable: true, expected: true })) });
            }
            if (target.endsWith('/match')) { aligns.push('lesson'); align = false; return api({ binding: { state: 'matched' } }); }
            if (target.endsWith('/calendar-reconcile')) {
                const id = Number(target.split('/').at(-3)), data = route.request().postDataJSON();
                sends.push({ id, ...data });
                if (id === 12) return api({ state: 'empty', message: '双方均未开始点名。', receipt: false });
                if (createAvailable) {
                    if (data.student_ids) return api({ state: 'school_missing', requires_full_roster: true, message: '新建学校点名需要本课全班。' });
                    if (!data.location) return api({ state: 'location_required', message: '新建学校点名需要定位。' });
                    creations.push({ id, ...data }); receipts.add(data.idempotency_key);
                    return api({ state: 'queued', message: '新建任务已排队', receipt: true });
                }
                if (id === 11) {
                    const selected = data.student_ids || roster.map(person => person.student_id);
                    const rows = selected.map((student_id: number) => {
                        if (student_id === roster.length) return { student_id, local_status: 'PENDING', remote_status: null, state: 'not_enrolled', reason: '学校名单无对应学生。' };
                        imported.add(student_id);
                        return { student_id, local_status: 'PRESENT', remote_status: 'CHECKED', state: 'imported', reason: '已写入本地点名。' };
                    });
                    const result = { state: rows.some((r: any) => r.state === 'not_enrolled') ? 'partial' : 'imported', message: '学校点名已登记到本地。', local_changed: true, receipt: false, has_receipt: true, rows,
                        counts: { imported: rows.filter((r: any) => r.state === 'imported').length, not_enrolled: rows.filter((r: any) => r.state === 'not_enrolled').length } };
                    localReceipts.set(data.idempotency_key, result); return api(result);
                }
                if (align) return api({ state: 'alignment_required', message: '课次有多个候选。' });
                if (receiptMissing) { receiptMissing = false; return api({ detail: '网络中断' }, 503); }
                receipts.add(data.idempotency_key);
                if (loseResponse) return api({ detail: '响应中断' }, 503);
                if (initialUnknown) return api({ state: 'outcome_unknown', receipt: true, has_receipt: true, message: '学校结果待核实' });
                return api({ state: 'queued', message: '已交由后台处理', receipt: true });
            }
            if (target.includes('/calendar-sync/')) {
                const key = target.split('/calendar-sync/')[1].split('/')[0];
                if (localReceipts.has(key)) return api(localReceipts.get(key));
                if (!receipts.has(key)) return api({ detail: '没有回执' }, 404);
                if (target.endsWith('/readback')) {
                    readbacks.push(target);
                    return initialUnknown && readbacks.length === 1 ? api({ state: 'outcome_unknown', message: '学校回读尚未证实' }) : api({ state: 'confirmed', message: '学校明确回读一致' });
                }
                return loseResponse || initialUnknown ? api({ state: 'outcome_unknown', message: '学校结果待核实' }) : api({ state: 'confirmed', message: '学校回读已确认' });
            }
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
    return { saves, sends, errors, aligns, readbacks, creations };
}

test('semester calendar shows same-day school and local lessons and keeps personal correction scope on mobile', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 }); await page.emulateMedia({ colorScheme: 'dark' });
    const { saves, sends, errors } = await fixture(page, { studentId: 2, conflict: true });
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
    await detail.getByRole('button', { name: '保存并同步' }).click();
    await expect(detail.getByRole('alert')).toContainText('已变化');
    await expect(detail.getByRole('textbox', { name: /更正理由/ })).toHaveValue('核对签到表');
    await detail.getByRole('button', { name: '读取最新名单' }).click();
    await expect(detail.getByText('拟改 · 出勤')).toBeVisible();
    await expect(detail.getByRole('heading', { name: '2026-11-17 出勤记录' })).toBeVisible();
    await expect(detail.getByText(/课次已调整为 2026-11-17 · 第3–4节/)).toBeVisible();
    await detail.getByRole('button', { name: '保存并同步' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await expect(sync.getByText('已交由后台处理')).toBeVisible();
    expect(saves.map(item => item.records)).toEqual([[{ student_id: 2, status: 'PRESENT', revision: 1 }], [{ student_id: 2, status: 'PRESENT', revision: 2 }]]);
    expect(saves.map(item => item.expected_slot_revision)).toEqual(['a'.repeat(64), 'b'.repeat(64)]);
    expect(sends).toHaveLength(1); expect(sends[0]).toMatchObject({ id: 10, student_ids: [2] });
    await sync.getByRole('button', { name: '查询此次回执' }).click(); await expect(sync.getByText('学校回读已确认')).toBeVisible();
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
    await page.keyboard.press('Escape'); await expect(page.getByRole('button', { name: '查看学期出勤' })).toBeFocused();
    expect(errors).toEqual([]);
});

test('one sync reconciles named students across lessons and resumes the ambiguous lesson after matching', async ({ page }, testInfo) => {
    const { sends, aligns, errors } = await fixture(page, { align: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    const day = calendar.getByRole('button', { name: /2026-11-16，/ });
    await expect(day.locator('[data-tone="warning"]')).toHaveText('到2 未到1');
    await expect(day.locator('[data-tone="success"]')).toHaveText('到3 未到0');
    await page.screenshot({ path: testInfo.outputPath('class-desktop.png') });
    await calendar.getByRole('button', { name: '同步智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.locator('select').nth(1).selectOption('selected', { force: true });
    await sync.locator('select').nth(2).selectOption(['1', '3'], { force: true });
    await sync.getByRole('button', { name: '同步所选课次' }).click();
    await expect(sync.getByText('学校点名已登记到本地。')).toBeVisible();
    await sync.getByText('查看 2 人的记录').click();
    await expect(sync.getByText(/同学1.*本地 出勤 \/ 学校 出勤/)).toBeVisible();
    await expect(sync.getByText(/同学3.*本地 待确认 \/ 学校 暂无记录/)).toBeVisible();
    await sync.getByRole('button', { name: '核对此课对齐' }).click();
    await expect(sync.getByText('已交由后台处理')).toBeVisible();
    expect(aligns).toEqual(['lesson']); expect(sends.map(row => [row.id, row.student_ids])).toEqual([[10,[1,3]],[11,[1,3]],[10,[1,3]]]);
    expect(sends[0].idempotency_key).toBe(sends[2].idempotency_key); expect(errors).toEqual([]);
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await day.click(); const detail = page.getByRole('dialog', { name: '2026-11-16 出勤记录' });
    await detail.locator('select').selectOption('11', { force: true });
    await expect(detail.getByText(/课堂实验.*本地已点名/)).toBeVisible();
    await expect(detail.getByText('本地 · 出勤', { exact: true })).toBeVisible();
});

test('class teacher changes the whole roster then one student in the other same-day lesson with no school write', async ({ page }) => {
    const { sends, saves, errors } = await fixture(page);
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
    expect(sends).toHaveLength(0); expect(errors).toEqual([]);
});

test('uncertain submission automatically checks its original receipt and reads back without another dispatch', async ({ page }) => {
    const { sends, readbacks, errors } = await fixture(page, { loseResponse: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: '同步智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.locator('select').first().selectOption(['10'], { force: true });
    await sync.getByRole('button', { name: '同步所选课次' }).click();
    await expect(sync.getByText('学校明确回读一致')).toBeVisible();
    expect(sends).toHaveLength(1); expect(readbacks).toHaveLength(1);
    expect(readbacks[0]).toContain('/10/external/calendar-sync/' + sends[0].idempotency_key + '/readback');
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await calendar.getByRole('button', { name: '同步智慧课堂' }).click();
    await sync.getByRole('button', { name: '近期同步回执' }).click();
    await expect(sync.getByText('学校明确回读一致')).toBeVisible();
    expect(sends).toHaveLength(1); expect(errors).toEqual([]);
});
test('no receipt after interrupted delivery retries exactly the same key and people', async ({ page }) => {
    const { sends } = await fixture(page, { studentId: 2, receiptMissing: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: /2026-11-16，/ }).click();
    await page.getByRole('button', { name: '同步本课个人记录' }).click();
    await page.getByRole('button', { name: '重试此次同步' }).click();
    await expect(page.getByText('已交由后台处理')).toBeVisible();
    expect(sends).toHaveLength(2); expect(sends[0]).toEqual(sends[1]); expect(sends[0].student_ids).toEqual([2]);
});

test('acknowledged unknown outcome checks the school once automatically and keeps the explicit readback action', async ({ page }) => {
    const { sends, readbacks } = await fixture(page, { studentId: 2, initialUnknown: true });
    await page.getByRole('button', { name: /2026-11-16，/ }).click();
    await page.getByRole('button', { name: '同步本课个人记录' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await expect(sync.getByText('学校回读尚未证实')).toBeVisible();
    await sync.getByRole('button', { name: '查询此次回执' }).click();
    await expect(sync.getByText('学校结果待核实')).toBeVisible(); expect(readbacks).toHaveLength(1);
    await sync.getByRole('button', { name: '回读学校' }).click();
    await expect(sync.getByText('学校明确回读一致')).toBeVisible();
    expect(readbacks).toHaveLength(2); expect(sends).toHaveLength(1);
});

test('a whole-class sync requests fresh geolocation only to create missing roll calls and reuses it within this batch', async ({ page, context }) => {
    const { creations, sends, errors } = await fixture(page, { createAvailable: true });
    await context.setGeolocation({ latitude: 24.012, longitude: 108.034 });
    await context.grantPermissions(['geolocation'], { origin });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    await calendar.getByRole('button', { name: '同步智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.locator('select').first().selectOption(['10','11','12'], { force: true });
    await sync.getByRole('button', { name: '同步所选课次' }).click();
    await expect(sync.getByText('新建任务已排队')).toHaveCount(2); await expect(sync.getByText('未开始点名', { exact: true })).toBeVisible();
    expect(creations.map(row => row.id)).toEqual([10,11]); expect(creations[0].location).toMatchObject({ latitude: 24.012, longitude: 108.034 });
    expect(creations[1].location).toEqual(creations[0].location); expect(Date.now() - creations[0].location.captured_at_ms).toBeLessThan(300000);
    expect(sends[0].idempotency_key).toBe(creations[0].idempotency_key);
    expect(sends[0].location).toBeUndefined(); expect(sends.at(-1).location).toBeUndefined();
    expect(sends.every(r => r.student_ids === null)).toBeTruthy(); expect(errors).toEqual([]);
});

test('personal sync offers a single explicit whole-class action when the school only creates a whole roll call', async ({ page, context }) => {
    const { creations, sends } = await fixture(page, { createAvailable: true, studentId: 2 });
    await context.setGeolocation({ latitude: 24.012, longitude: 108.034 }); await context.grantPermissions(['geolocation'], { origin });
    await page.getByRole('button', { name: /2026-11-16，/ }).click();
    await page.getByRole('button', { name: '同步本课个人记录' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await expect(sync.getByText('新建学校点名需要本课全班。')).toBeVisible();
    expect(creations).toHaveLength(0); expect(sends).toHaveLength(1); expect(sends[0].student_ids).toEqual([2]);
    await sync.getByRole('button', { name: '同步本课全班' }).click();
    await expect(sync.getByText('新建任务已排队')).toBeVisible(); expect(creations).toHaveLength(1);
    expect(creations[0].idempotency_key).not.toBe(sends[0].idempotency_key);
    expect(creations[0].student_ids).toBe(null); await expect(sync.getByText('本次范围 · 本课全班')).toBeVisible();
});

test('school activity grades are labelled separately from absences and full attendance', async ({ page }) => {
    await fixture(page, { activities: true });
    const calendar = page.getByRole('dialog', { name: '学期出勤日历' });
    const day = calendar.getByRole('button', { name: /2026-11-16，/ });
    await expect(day.locator('[data-tone="info"]')).toHaveText('活动3');
    await expect(calendar.getByText(/学校活动 3 人次/)).toBeVisible();
    await calendar.locator('select').selectOption('2', { force: true });
    await expect(day.locator('[data-tone="info"]')).toHaveText('研学');
});

test('school-only roll call displays 41 saved local records and separately identifies the unmatched pupil', async ({ page }, testInfo) => {
    const { sends } = await fixture(page, { classSize: 42 });
    await page.getByRole('button', { name: /2026-11-16，/ }).click();
    const detail = page.getByRole('dialog', { name: '2026-11-16 出勤记录' });
    await detail.locator('select').selectOption('11', { force: true });
    await detail.getByRole('button', { name: '同步本课全班' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await expect(sync.getByText('已更新本地 41 · 学校无此人 1')).toBeVisible();
    await sync.getByText('查看 42 人的记录').click();
    await expect(sync.getByText(/本地 出勤 \/ 学校 出勤/)).toHaveCount(41);
    await expect(sync.getByText(/同学42.*学校名单无对应学生/)).toHaveCount(1);
    const background = page.locator('.lac-editor');
    await expect(background.locator('.lac-summary').first()).toContainText('本地已点名');
    await expect(background.locator('.lac-record').first()).toContainText('本地 · 出勤');
    await expect(sync).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath('school-only-41-of-42.png') });
    expect(sends).toHaveLength(1);
    await page.keyboard.press('Escape'); await expect(sync).toHaveCount(0);
    await expect(detail.getByText(/课堂实验.*本地已点名/)).toBeVisible();
    await page.keyboard.press('Escape'); await expect(detail).toHaveCount(0);
    await page.getByRole('button', { name: '同步智慧课堂' }).click();
    await sync.getByRole('button', { name: '近期同步回执' }).click();
    await expect(sync.getByText('已更新本地 41 · 学校无此人 1')).toBeVisible();
    expect(sends).toHaveLength(1);
});

test('denied location preserves each lesson and requests permission once per batch', async ({ page }) => {
    await page.addInitScript(() => {
        (window as any).geoCalls = 0;
        Object.defineProperty(navigator, 'geolocation', { value: { getCurrentPosition: (_ok: unknown, fail: () => void) => {
            (window as any).geoCalls++; fail();
        } } });
    });
    const { sends, creations } = await fixture(page, { createAvailable: true });
    await page.getByRole('button', { name: '同步智慧课堂' }).click();
    const sync = page.getByRole('dialog', { name: '智慧课堂同步' });
    await sync.getByRole('button', { name: '同步所选课次' }).click();
    await expect(sync.getByText(/未取得浏览器定位/)).toHaveCount(2);
    expect(await page.evaluate(() => (window as any).geoCalls)).toBe(1);
    await sync.getByRole('button', { name: '重新定位并同步本课' }).first().click();
    await expect.poll(() => page.evaluate(() => (window as any).geoCalls)).toBe(2);
    expect(sends[0].idempotency_key).toBe(sends[2].idempotency_key); expect(creations).toHaveLength(0);
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
