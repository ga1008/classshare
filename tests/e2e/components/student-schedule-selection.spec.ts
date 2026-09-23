import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const markup = execFileSync(python, ['-c', String.raw`
import sys
from pathlib import Path
from jinja2 import Environment, StrictUndefined
env = Environment(autoescape=True, undefined=StrictUndefined)
env.globals['lq_family_enabled'] = lambda _: True
sys.stdout.reconfigure(encoding='utf-8')
print(env.from_string(Path('templates/partials/student_dashboard_schedule.html').read_text(encoding='utf-8')).render(class_offerings=[], dashboard_initial_search=''))
`], { encoding: 'utf8' });

for (const width of [1280, 390]) test(`student semester selections keep the native refresh and collection filters at ${width}px`, async ({ page }, info) => {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const queries: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const terms = [{ year: '2026-2027', term: '1', label: '2026-2027第一学期', status: 'current' }, { year: '2025-2026', term: '2', label: '2025-2026第二学期', status: 'ended' }];
  await page.route('https://student-selection.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/api/dashboard/course-schedule/overview') {
      queries.push(url.search);
      const selected = url.searchParams.get('year') === '2025-2026' ? terms[1] : terms[0];
      return route.fulfill({ json: { status: 'success', overview: { selected_term: selected, terms,
        authorized_courses: [{ id: 1, course_name: '计算机网络', class_name: '一班', teacher_name: '教师', semester: terms[0].label }, { id: 2, course_name: '上学期课程', class_name: '一班', semester: terms[1].label, is_history: true }],
        section_range: { min: 1, max: 8 }, weeks: [{ week_index: 1, label: '第1周', is_current: true, lessons: [] }],
      } } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="${width === 390 ? 'dark' : 'light'}" data-lq-tier="A" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/lq/pages/dashboard.css"><style>body{margin:0;padding:16px}main{min-width:0;max-width:1000px;margin:auto}</style></head><body class="dashboard-page ls-page role-student"><main data-lq-dashboard data-dashboard-user-id="synthetic" id="root">${markup}</main></body></html>` });
  });
  await page.goto('https://student-selection.test/');
  await expect(page.locator('[data-student-schedule-term]')).toBeVisible(); // Native SSR/no-JS fallback.
  await page.evaluate(async () => {
    (window as any).semesterChanges = 0;
    document.querySelector('[data-student-schedule-term]')!.addEventListener('change', () => (window as any).semesterChanges++);
    const { initStudentDashboardSchedule } = await import('/static/js/student_dashboard_schedule.js');
    initStudentDashboardSchedule(document.querySelector('#root'));
  });
  const term = page.getByRole('combobox', { name: '课表学期', exact: true });
  await expect(term).toBeEnabled(); await expect(term).toHaveValue(terms[0].label);
  await expect(page.locator('[data-student-schedule-agenda]')).toBeHidden();
  await expect(page.locator('[data-student-schedule-courses]')).toBeHidden();
  await expect(page.locator('[data-student-schedule-retry]')).toBeHidden();
  await term.click();
  const popup = page.locator('.lq-selection__popup');
  await expect(popup).toBeVisible();
  expect(await popup.evaluate(node => getComputedStyle(node).backdropFilter)).toContain('blur(');
  await term.press('End'); await term.press('Enter');
  await expect(term).toHaveValue(`${terms[1].label} · 往期`);
  await expect(page.locator('[data-student-schedule-term]')).toHaveValue('2025-2026|2');
  expect(queries).toHaveLength(2); expect(queries[1]).toContain('year=2025-2026&term=2');
  expect(await page.evaluate(() => (window as any).semesterChanges)).toBe(1);
  await page.getByRole('tab', { name: /全部课程/ }).click();
  await expect(term).toBeHidden();
  await expect(page.locator('[data-student-schedule-deck]')).toBeHidden();
  await expect(page.locator('[data-student-schedule-agenda]')).toBeHidden();
  const collection = page.getByRole('combobox', { name: '课程集合学期', exact: true });
  await collection.click(); await collection.press('End'); await collection.press('Enter');
  await expect(page.locator('[data-student-course-list] .ls-schedule-course')).toHaveCount(1);
  await expect(page.locator('[data-student-course-list] h3')).toHaveText('上学期课程');
  const state = page.getByRole('combobox', { name: '课程安排状态', exact: true });
  await state.click(); await state.press('Home'); await state.press('ArrowDown'); await state.press('Enter');
  await page.getByRole('button', { name: '清除集合筛选' }).click();
  await expect(collection).toHaveValue('全部学期'); await expect(state).toHaveValue('全部课程');
  await expect(page.locator('[data-student-course-list] .ls-schedule-course')).toHaveCount(2);
  expect(queries).toHaveLength(2); // Collection filtering does not refetch or alter the schedule term.
  await page.getByRole('tab', { name: '日程列表', exact: true }).click();
  await expect(page.locator('[data-student-schedule-deck]')).toBeHidden();
  await expect(page.locator('[data-student-schedule-courses]')).toBeHidden();
  await expect(page.locator('[data-student-schedule-agenda]')).toBeVisible();
  await expect(term).toBeVisible(); await expect(term).toHaveValue(`${terms[1].label} · 往期`);
  await term.click(); await page.keyboard.press('Escape');
  await expect(popup).toBeHidden(); await expect(term).toBeFocused();
  const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(node => node.getBoundingClientRect().right > innerWidth + 1).map(node => ({ tag: node.tagName, cls: node.className, width: node.getBoundingClientRect().width, hidden: node.getAttribute('hidden') })).slice(0, 8));
  expect(await page.evaluate(() => document.documentElement.scrollWidth), JSON.stringify(overflow)).toBe(width);
  await page.screenshot({ path: info.outputPath('student-semester-selection.png'), fullPage: true });
  expect(errors).toEqual([]);
});
