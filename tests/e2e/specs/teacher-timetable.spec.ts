import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { loginTeacher, loginStudent } from '../fixtures/p03';

const runtime = path.resolve(process.env.TEACHER_TIMETABLE_RUNTIME || '.codex-temp/teacher-timetable-20261020-runtime');
const fixture = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!fixture.teacherTimetable?.synthetic || !runtime.startsWith(path.resolve('.codex-temp') + path.sep)) throw Error('Owned synthetic runtime required');
const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
const API = '/api/manage/academic/teachers';
function mode(mode: string, delay_ms = 0, revision = 1) { fs.writeFileSync(path.join(runtime, 'teacher-timetable-control.json'), JSON.stringify({ mode, delay_ms, revision })); }
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
  await expect(page.locator('#teacherQueryFeedback')).toContainText('本地');
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

test('empty keyword automatically browses cached teachers by latest sync with local pagination and term changes', async ({ page }, info) => {
  // Seed via the real service+adapter; everything after navigation must be local.
  expect((await page.request.post(`${API}/sync`, { data: { year: '2026-2027', term: '1', q: '分页' } })).ok()).toBe(true);
  expect((await page.request.post(`${API}/000136/sync`, { data: { year: '2026-2027', term: '1' } })).ok()).toBe(true);
  const before = calls().length;
  await page.goto('/manage/academic/teachers/schedule');
  await expect(page.locator('#teacherQueryFeedback')).toContainText('本地');
  await expect(page.locator('[data-teacher-no]')).toHaveCount(12);
  await expect(page.locator('[data-teacher-no]').first()).toHaveAttribute('data-teacher-no', '000136');
  await expect(page.locator('#teacherQuerySync')).toBeDisabled();
  await expect(page.locator('#teacherQuerySyncHint')).toBeVisible();
  expect(await page.locator('#teacherQueryName').getAttribute('placeholder')).not.toContain('兰瑞江');
  await expect(page.locator('#teacherQuery-campus_id')).toBeDisabled();
  await expect(page.locator('[data-teacher-metric="courses"]').locator('..')).toContainText('本页');
  await page.locator('#teacherQueryNext').click();
  await expect(page.locator('#teacherQueryPage')).toContainText('2 / 2');
  await expect(page.locator('[data-teacher-no]')).toHaveCount(2);
  await page.locator('#teacherQueryName').fill('待查询姓名');
  await expect(page.locator('#teacherQuery-campus_id')).toBeEnabled();
  await page.locator('summary').filter({ hasText: '更多筛选' }).click();
  await page.locator('#teacherQuery-campus_id').selectOption('1');
  await page.locator('#teacherQueryName').fill('');
  await expect(page.locator('#teacherQueryPage')).toContainText('1 / 2');
  await expect(page.locator('#teacherQuery-campus_id')).toHaveValue('1');
  await expect(page.locator('#teacherQuery-campus_id')).toBeDisabled();
  await expect(page.locator('#teacherQueryFilterHint')).toBeVisible();
  const termResponse = page.waitForResponse(response => response.url().endsWith('/teachers/query') && response.request().postDataJSON()?.term === '2');
  await page.locator('#teacherQueryTerm').selectOption('2026-2027|2');
  const termData = await (await termResponse).json();
  await expect(page.locator('[data-teacher-no]')).toHaveCount(termData.items.length);
  await expect(page.locator('#teacherQueryResultMeta')).toContainText(termData.selected_term.label);
  expect(termData.mode).toBe('local_browse');
  expect(calls().length).toBe(before);
  await page.screenshot({ path: info.outputPath('local-browse-term.png'), fullPage: true });
});

test('single-teacher dialog sync keeps its layer and trigger, refreshes current week, metadata and local cards', async ({ page }, info) => {
  await open(page); await sync(page);
  const data = await (await page.request.get(`${API}/000123/timetable?year=2026-2027&term=1`)).json();
  const weekIndex = data.overview.weeks.findIndex((week: any) => week.is_current);
  expect(weekIndex).toBeGreaterThanOrEqual(0);
  await page.locator('[data-teacher-no="000123"]').click();
  const parent = page.getByRole('dialog', { name: '同名教师的课表', exact: true, includeHidden: true });
  await expect(parent.locator('[data-csd-lesson-detail]').first()).toBeVisible();
  await parent.locator('[data-csd-next]').click();
  await expect(parent.locator('.cs-card.is-active')).not.toHaveAttribute('data-week-index', String(weekIndex));
  await page.evaluate(() => {
    (window as any).timetableNodes = {
      dialog: document.querySelector('.teacher-timetable-dialog'),
      deck: document.querySelector('.teacher-timetable-dialog .cs-deck'),
      trigger: document.querySelector('[data-teacher-no="000123"]'),
    };
  });
  const before = calls().length;
  const posts: any[] = [];
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes(API)) posts.push({ url: request.url(), body: request.postDataJSON() }); });
  mode('success', 2000, 2);
  const button = parent.locator('[data-teacher-timetable-sync]');
  await button.click();
  await expect(button).toBeDisabled();
  await expect(button).toHaveAttribute('aria-busy', 'true');
  await expect(button.locator('.lq-spinner')).toBeVisible();
  await button.evaluate(element => (element as HTMLButtonElement).click());
  await page.screenshot({ path: info.outputPath('timetable-sync-busy.png') });
  // The existing timetable remains usable while syncing. Its child layer owns
  // the old lesson trigger until close, so the new deck must wait.
  const previousWeek = await parent.locator('.cs-card.is-active').getAttribute('data-week-index');
  await parent.locator('[data-csd-lesson-detail]').filter({ hasText: '合成数据库原理' }).click();
  const previousDetail = page.getByRole('dialog', { name: '合成数据库原理', exact: true });
  await expect(previousDetail).toContainText('合成楼B412');
  await expect(button).toBeEnabled();
  await expect(parent).toContainText('关闭课程详情后显示更新的课表');
  await expect(previousDetail).toBeVisible();
  await expect(parent.locator('.cs-card.is-active')).toHaveAttribute('data-week-index', previousWeek!);
  await expect(previousDetail).not.toContainText('更新楼C201');
  await page.keyboard.press('Escape');
  await expect(previousDetail).toHaveCount(0);
  await expect(parent).toContainText('同步完成');
  await expect(parent).toContainText('副教授');
  await expect(parent.getByRole('button', { name: '更新安全教育', exact: true })).toBeVisible();
  await expect(parent.getByRole('button', { name: '合成安全教育', exact: true })).toHaveCount(0);
  await expect(parent.locator('.cs-card.is-active')).toHaveAttribute('data-week-index', String(weekIndex));
  const shanghaiDay = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', weekday: 'short' }).format(new Date()).replace('周', '');
  await expect(parent.locator('.cs-card.is-active .cs-grid__day--today')).toHaveText(`周${shanghaiDay}`);
  await expect(page.locator('[data-teacher-no="000123"]')).toContainText(`${data.teacher.summary.course_count + 1} 门课程`);
  expect(await page.evaluate(async () => {
    await new Promise(requestAnimationFrame);
    const nodes = (window as any).timetableNodes;
    return nodes.dialog === document.querySelector('.teacher-timetable-dialog') && nodes.deck === document.querySelector('.teacher-timetable-dialog .cs-deck')
      && nodes.trigger === document.querySelector('[data-teacher-no="000123"]') && nodes.trigger.isConnected;
  })).toBe(true);
  expect(posts.filter(post => post.url.endsWith('/000123/sync'))).toHaveLength(1);
  expect(posts.find(post => post.url.endsWith('/teachers/query'))?.body.local_only).toBe(true);
  expect(calls().slice(before).filter(call => call.form.jgh).every(call => call.form.jgh === '000123')).toBe(true);
  await parent.locator('[data-csd-next]').click();
  await parent.locator('[data-teacher-timetable-today]').click();
  await expect(parent.locator('.cs-card.is-active')).toHaveAttribute('data-week-index', String(weekIndex));
  await parent.locator('[data-csd-lesson-detail]').filter({ hasText: '合成数据库原理' }).click();
  await expect(page.getByRole('dialog', { name: '合成数据库原理', exact: true })).toContainText('更新楼C201');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '合成数据库原理', exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('timetable-sync-complete.png') });
  mode('error');
  await button.click();
  await expect(parent).toContainText('当前课表已保留');
  await expect(parent.locator('[data-csd-lesson-detail]').filter({ hasText: '合成新增课程' })).toBeVisible();
  await expect(button).toBeEnabled();
  await page.keyboard.press('Escape');
  await expect(parent).toHaveCount(0);
  await expect(page.locator('[data-teacher-no="000123"]')).toBeFocused();
});

test('closing a syncing teacher dialog aborts UI work and late server completion never reopens it', async ({ page }) => {
  await open(page); await sync(page);
  await page.locator('[data-teacher-no="000123"]').click();
  const parent = page.getByRole('dialog', { name: '同名教师的课表', exact: true });
  await expect(parent.locator('[data-csd-lesson-detail]').first()).toBeVisible();
  mode('success', 850, 2);
  const before = calls().length;
  await parent.locator('[data-teacher-timetable-sync]').click();
  await expect(parent.locator('[data-teacher-timetable-sync]')).toHaveAttribute('aria-busy', 'true');
  await page.keyboard.press('Escape');
  await expect(parent).toHaveCount(0);
  await expect(page.locator('[data-teacher-no="000123"]')).toBeFocused();
  await expect.poll(() => calls().slice(before).some(call => call.path.endsWith('cxJsKb.html'))).toBe(true);
  // Flush a later local request after the upstream detail response, without recreating a dialog.
  await page.request.get(`${API}/000123/timetable?year=2026-2027&term=1`);
  await expect(page.locator('[role="dialog"]:visible')).toHaveCount(0);
  await expect(page.locator('[data-teacher-no="000123"]')).toBeFocused();
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
