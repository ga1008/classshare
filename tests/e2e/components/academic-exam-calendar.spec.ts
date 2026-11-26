import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const assets = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).entries;
const asset = (key: string) => '/static/' + assets[key];
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const panels = JSON.parse(execFileSync(python, ['-c', `
import json, sys
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from classroom_app.lq import lq_props
env = Environment(loader=FileSystemLoader('templates'), undefined=StrictUndefined, autoescape=True)
entries = json.loads(Path('static/assets/manifest.json').read_text(encoding='utf-8'))['entries']
env.globals.update(lq_props=lq_props, asset_url=lambda key: '/static/' + entries[key])
template = env.get_template('partials/dashboard_exam_calendar.html')
parts = {role: template.render(user_info={'role': role}) for role in ('teacher','student')}
parts['legacyDock'] = env.get_template('partials/app_bottomnav.html').render()
print(json.dumps(parts))
`], {encoding: 'utf8'}));

const base = 'https://exam-calendar.test';
const subject = '面向复杂网络的计算机网络原理与综合实验设计';
const room = '教学实验综合楼东区机房 406B';
const exam = { kind: 'exam', subject, location: room, date: '2030-01-15',
    starts_at: '2030-01-15T08:30:00', ends_at: '2030-01-15T10:00:00',
    detail_rows: [{label: '教学班', value: '网络工程合成一班'}, {label: '备注', value: '<校内通知>'}] };
const duty = { ...exam, kind: 'invigilation', subject: '大学英语', date: '', starts_at: '', ends_at: '', location: '' };
type Payload = { items: (typeof exam)[], sync: {status: string, message: string}, can_refresh: boolean };
function state(items: (typeof exam)[], status = 'idle', message = '', can_refresh = true): Payload {
    return { items, sync: {status, message}, can_refresh };
}

async function mount(page: Page, role: 'teacher' | 'student', width: number,
    respond: (method: string) => Payload | number, ticking = false) {
    await page.setViewportSize({ width, height: 760 });
    await page.emulateMedia({reducedMotion: width < 500 ? 'reduce' : 'no-preference'});
    const now = new Date('2030-01-14T23:58:01+08:00');
    if (ticking) await page.clock.install({time: now});
    else await page.clock.setFixedTime(now);
    await page.route(base + '/**', async route => {
        const url = new URL(route.request().url());
        if (url.pathname.startsWith('/api/dashboard/academic-exams')) {
            const result = respond(route.request().method());
            return typeof result === 'number' ? route.fulfill({status: result, json: {detail: '连接中断'}}) : route.fulfill({json: result});
        }
        if (url.pathname.startsWith('/static/assets/')) {
            const file = path.resolve('.' + url.pathname);
            if (file.startsWith(path.resolve('static/assets') + path.sep) && fs.existsSync(file))
                return route.fulfill({contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file)});
        }
        if (url.pathname !== '/') return route.fulfill({status: 404, body: ''});
        return route.fulfill({contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${width < 500 ? 'dark' : 'light'}" data-lq-tier="A" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/dashboard_exam_calendar.css')}"></head><body class="ls-page role-${role}"><main class="ls-shell"><div class="${role === 'teacher' ? 'ls-exam-overview' : 'ls-home-grid'}"><section class="${role === 'teacher' ? 'ls-focus' : 'ls-schedule'}">课程日程</section>${panels[role]}</div></main>${role === 'student' ? panels.legacyDock : ''}</body></html>`});
    });
    await page.goto(base);
    return page.locator('#dashboard-exam-calendar');
}

for (const [role, width] of [['teacher', 1280], ['student', 320]] as const) {
    test.describe(`${role} at ${width}px`, () => {
        test.use({hasTouch: width < 500, timezoneId: 'America/Los_Angeles'});
        test('shows local school days, accessible details, and a coalesced full refresh', async ({page}, info) => {
            const errors: string[] = [];
            page.on('pageerror', err => errors.push(err.message));
            let posts = 0, polls = 0;
            const rows = role === 'teacher' ? [duty, exam] : [{...exam, room_time_varies: true}];
            const card = await mount(page, role, width, method => {
                if (method === 'POST') { posts++; return state(rows, 'pending', '已排队'); }
                if (posts && ++polls === 1) return state(rows, 'running', '同步中');
                return posts ? state([{...exam, location: '新考场 203'}], 'success', '已完成') : state(rows);
            });
            await expect(card.getByRole('heading')).toHaveText(role === 'teacher' ? '考试与监考倒计时' : '考试倒计时');
            const row = card.getByRole('button', {name: new RegExp(subject)});
            await expect(row).toContainText('明天');
            if (role === 'teacher') await expect(card.getByRole('button', {name: /大学英语/})).toContainText('待定');
            const refresh = card.getByRole('button', {name: '同步本学期教务安排'});
            await expect(refresh).toHaveText('');
            const corner = await refresh.evaluate(button => {
                const title = button.closest('article')!.querySelector('h3')!.getBoundingClientRect();
                const action = button.getBoundingClientRect();
                return action.left >= title.right && action.top < title.bottom && action.bottom > title.top;
            });
            expect(corner).toBe(true);
            const geometry = await row.evaluate(node => {
                const copy = node.querySelector('strong')!.getBoundingClientRect();
                const count = node.querySelector('.ls-exam-count')!.getBoundingClientRect();
                return { gap: count.left - copy.right, clipped: node.scrollWidth - node.clientWidth, width: document.documentElement.scrollWidth };
            });
            expect(geometry.gap).toBeGreaterThan(0);
            expect(geometry.clipped).toBeLessThanOrEqual(1);
            expect(geometry.width).toBe(width);
            await card.screenshot({path: info.outputPath('calendar.png')});
            if (width < 500) await row.tap(); else { await row.focus(); await page.keyboard.press('Enter'); }
            const dialog = page.getByRole('dialog', {name: '考试详情'});
            await expect(dialog).toContainText('08:30 – 10:00');
            await expect(dialog.getByText(role === 'teacher' ? '时间' : '场次时间范围', {exact: true})).toBeVisible();
            await expect(dialog).toContainText('网络工程合成一班');
            await expect(dialog).toContainText('<校内通知>');
            if (width < 500) {
                expect(await dialog.evaluate(node => Number(getComputedStyle(node.parentElement!).zIndex)
                    > Number(getComputedStyle(document.querySelector('.app-bottomnav')!).zIndex))).toBe(true);
                const lastFact = dialog.locator('dd').last();
                expect(await lastFact.evaluate(node => {
                    const rect = node.getBoundingClientRect();
                    const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
                    return hit === node || node.contains(hit);
                })).toBe(true);
            }
            await dialog.screenshot({path: info.outputPath('detail.png')});
            await page.keyboard.press('Escape');
            await expect(dialog).toHaveCount(0);
            await expect(row).toBeFocused();
            await refresh.click();
            await expect(refresh).toBeDisabled();
            await expect(refresh).toHaveAttribute('aria-busy', 'true');
            expect(await refresh.locator('.lq-btn__icon').evaluate(n => getComputedStyle(n).animationName))
                .toBe(width < 500 ? 'none' : 'lq-spinner-turn');
            await expect(card.getByRole('status')).toHaveText('已完成', {timeout: 12000});
            await expect(card.getByRole('listitem')).toHaveCount(1);
            await expect(row).toContainText('新考场 203');
            await expect(refresh).toBeEnabled();
            expect(posts).toBe(1);
            expect(polls).toBe(2);
            expect(errors).toEqual([]);
        });
    });
}

test('lost refresh response preserves details and rereads before any new submission', async ({page}) => {
    let reads = 0, posts = 0;
    const card = await mount(page, 'teacher', 760, method => {
        if (method === 'POST') { posts++; return 503; }
        return ++reads === 1 ? state([exam]) : state([], 'unavailable', '请绑定教务账号', false);
    });
    await card.getByRole('button', {name: '同步本学期教务安排'}).click();
    await expect(card.getByRole('status')).toContainText('连接中断');
    await expect(card.getByRole('listitem')).toContainText(subject);
    await card.getByRole('button', {name: '重新读取教务安排'}).click();
    await expect(card.getByRole('status')).toHaveText('请绑定教务账号');
    await expect(card.getByRole('button', {name: '同步本学期教务安排'})).toBeDisabled();
    await expect(card.locator('[data-exam-empty]')).toBeVisible();
    expect(posts).toBe(1);
    expect(reads).toBe(2);
});

test('an open dashboard changes from tomorrow to today, in progress, and finished at school time', async ({page}) => {
    let reads = 0;
    const card = await mount(page, 'student', 390, () => {
        reads++;
        return state([{...exam, starts_at: '2030-01-15T00:01:00+08:00', ends_at: '2030-01-15T00:02:00+08:00'}]);
    }, true);
    const row = card.getByRole('listitem');
    await expect(row).toContainText('明天');
    await page.clock.fastForward(120000);
    await expect(row).toContainText('今天');
    await page.clock.fastForward(60000);
    await expect(row).toContainText('进行中');
    await page.clock.fastForward(60000);
    await expect(row).toHaveCount(0);
    await expect(card.locator('[data-exam-empty]')).toBeVisible();
    expect(reads).toBe(1);
});
