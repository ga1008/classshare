import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { loginTeacher, loginStudent } from '../fixtures/p03';

const runtime = path.resolve(process.env.TEACHER_TIMETABLE_RUNTIME || '.codex-temp/teacher-timetable-20261020-runtime');
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!fixture.teacherTimetable?.synthetic || !runtime.startsWith(path.resolve('.codex-temp') + path.sep)) throw Error('Owned synthetic runtime required');
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const API = '/api/manage/academic/teachers';
function mode(mode: string, delay_ms = 0) { fs.writeFileSync(path.join(runtime, 'teacher-timetable-control.json'), JSON.stringify({ mode, delay_ms })); }
function calls() {
  const file = path.join(runtime, 'teacher-timetable-requests.jsonl');
  return fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split(/\r?\n/).filter(Boolean).map(value => JSON.parse(value)) : [];
}
async function isolate(page: Page) {
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await expect.poll(async () => (await (await page.request.get('/api/internal/health')).json()).database_path).toBe(fixture.databasePath);
}
async function open(page: Page, q = '同名教师') {
  await page.goto('/manage/academic/teachers/schedule');
  await page.locator('#teacherQueryTerm').selectOption('2026-2027|1');
  await page.locator('#teacherQueryName').fill(q);
}
async function sync(page: Page) {
  await page.locator('#teacherQuerySync').click();
  await expect(page.locator('#teacherQueryFeedback')).toContainText('已同步');
  await expect(page.locator('#teacherQuerySubmit')).toBeEnabled();
}

test.beforeEach(async ({ page }) => { mode('success'); await isolate(page); await loginTeacher(page, fixture); });
test.afterEach(async ({ page }, info) => {
  const graphs = await page.evaluate(() => [...new Set(performance.getEntriesByType('resource').map(entry => entry.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(graphs).toEqual([graph]);
  await info.attach('synthetic-jwxt-requests', { body: JSON.stringify(calls()), contentType: 'application/json' });
});

test('same-name cards, cache-first query, forced sync and two-layer full timetable', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await open(page, `首次查询-${info.project.name}-${Date.now()}`);
  const cold = calls().length;
  await page.locator('#teacherQuerySubmit').click();
  await expect(page.locator('#teacherQueryFeedback')).toContainText('已同步');
  expect(calls().length).toBeGreaterThan(cold);
  const cards = page.locator('[data-teacher-no]');
  await expect(cards).toHaveCount(2);
  await expect(page.locator('[data-teacher-no="000123"]')).toContainText('已关联平台账号');
  await expect(page.locator('[data-teacher-no="000124"]')).toContainText('尚未关联平台账号');
  expect(await page.locator('#teacherQuery-campus_id option').allTextContents()).toContain('合成校区');
  const before = calls().length;
  await page.locator('#teacherQuerySubmit').click();
  await expect(page.locator('#teacherQueryFeedback')).toContainText('本地');
  expect(calls().length).toBe(before);
  await page.screenshot({ path: info.outputPath('teacher-cards.png'), fullPage: true });
  await page.locator('[data-teacher-no="000123"]').click();
  const parent = page.getByRole('dialog', { name: '同名教师的课表', exact: true });
  await expect(parent).toBeVisible();
  const lesson = parent.locator('[data-csd-lesson-detail]').first();
  await expect(lesson).toContainText('合成数据库原理');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const materials = await parent.evaluate(element => [...element.querySelectorAll('*')].filter(node => {
    const style = getComputedStyle(node);
    return style.backdropFilter !== 'none' && node.getBoundingClientRect().width > 0;
  }).map(node => ({ tag: node.tagName, className: node.className, filter: getComputedStyle(node).backdropFilter })));
  await info.attach('timetable-live-material-hosts', { body: JSON.stringify(materials), contentType: 'application/json' });
  await expect(parent.locator('.cs-card.is-active')).toHaveCount(1);
  expect(await parent.locator('.cs-card:not(.is-active)').evaluateAll(cards => cards.every(card => !card.childNodes.length))).toBe(true);
  await page.screenshot({ path: info.outputPath('teacher-3d-timetable.png') });
  await lesson.click();
  const detail = page.getByRole('dialog', { name: '合成数据库原理', exact: true });
  await expect(detail).toBeVisible();
  await expect(detail).toContainText('合成软件2601班');
  await expect(detail).toContainText('理论:16,实践:16');
  await expect(detail).toContainText('第2、3节');
  await expect(page.locator('[role="dialog"]:visible')).toHaveCount(2);
  await expect(page.locator('.cs-expand')).toBeHidden();
  expect(page.url()).toContain('/manage/academic/teachers/schedule');
  await page.screenshot({ path: info.outputPath('lesson-detail.png') });
  await page.keyboard.press('Escape');
  await expect(detail).toHaveCount(0);
  await expect(lesson).toBeFocused();
  await parent.getByRole('button', { name: '合成安全教育', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '合成安全教育', exact: true })).toContainText('未排定具体上课时间');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '合成安全教育', exact: true })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(parent).toHaveCount(0);
  await expect(page.locator('[data-teacher-no="000123"]')).toBeFocused();
  await page.locator('[data-teacher-no="000124"]').click();
  await expect(page.locator('[data-csd-lesson-detail]').first()).toContainText('合成英语阅读');
  await page.keyboard.press('Escape');
  expect(errors).toEqual([]);
});

test('sync failure retains cached cards, empty queries and late responses do not replace new filters', async ({ page }, info) => {
  await open(page); await sync(page);
  mode('error');
  await page.locator('#teacherQuerySync').click();
  await expect(page.locator('#teacherQueryFeedback')).toContainText('保留上次查询结果');
  await expect(page.locator('[data-teacher-no]')).toHaveCount(2);
  await page.locator('[data-teacher-no="000123"]').click();
  await expect(page.locator('[data-csd-lesson-detail]').first()).toBeVisible();
  await page.keyboard.press('Escape');
  mode('success', 650);
  await page.locator('#teacherQueryName').fill('迟到查询');
  const response = page.waitForResponse(result => result.url().endsWith('/teachers/sync'), { timeout: 5000 }).catch(() => null);
  await page.locator('#teacherQuerySync').click();
  await page.locator('#teacherQueryName').fill('无匹配');
  await response;
  await expect(page.locator('#teacherQueryFeedback')).toContainText('筛选条件已修改');
  mode('success');
  await page.locator('#teacherQuerySync').click();
  await expect(page.locator('#teacherQueryFeedback')).toContainText('已同步');
  await expect(page.locator('[data-teacher-no]')).toHaveCount(0);
  await expect(page.locator('#teacherQueryEmpty')).toBeVisible();
  await page.screenshot({ path: info.outputPath('empty-query.png'), fullPage: true });
});

test('pagination, term isolation, empty schedule and student permission', async ({ page, browser }, info) => {
  await open(page, '分页'); await sync(page);
  await expect(page.locator('[data-teacher-no]')).toHaveCount(12);
  const before = calls().length;
  await page.locator('#teacherQueryNext').click();
  await expect(page.locator('[data-teacher-no]')).toHaveCount(2);
  await expect(page.locator('#teacherQueryPage')).toContainText('2 / 2');
  expect(calls().length).toBe(before);
  await page.locator('#teacherQueryName').fill('000123');
  await page.locator('#teacherQueryTerm').selectOption('2026-2027|2');
  mode('empty_schedule'); await sync(page);
  await page.locator('[data-teacher-no="000123"]').click();
  await expect(page.locator('.cs-week-empty-mark').first()).toContainText('本周无排课');
  await expect(page.getByRole('button', { name: '合成安全教育', exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('empty-schedule.png') });
  await page.keyboard.press('Escape');
  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:8367' });
  const student = await context.newPage(); await isolate(student); await loginStudent(student, fixture);
  expect((await student.request.post(`${API}/query`, { data: { year: '2026-2027', term: '1', q: '同名教师' } })).status()).toBe(403);
  expect((await student.request.get(`${API}/000123/timetable?year=2026-2027&term=1`)).status()).toBe(403);
  await context.close();
});
