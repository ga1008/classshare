import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const markup = execFileSync(python, ['-c', String.raw`
import re, sys
from pathlib import Path
from jinja2 import Environment, StrictUndefined
source = Path('templates/dashboard_teacher.html').read_text(encoding='utf-8')
env = Environment(autoescape=True, undefined=StrictUndefined)
env.globals['lq_family_enabled'] = lambda _: True
article = re.search(r'<article class="ls-course-row[\s\S]+?</article>', source).group()
offerings = []
for i, title in enumerate(['计算机网络原理', 'Python程序设计', '计算机网络原理', '上学期数据库课程'], 1):
    item = dict.fromkeys(re.findall(r'offering\.(\w+)', article))
    item.update(id=i, course_name=title, class_name='人工智能2601班（专升本）' if i != 3 else '计算机科学2606班（专升本）',
                course_id=1 if i == 3 else i, class_id=1 if i != 3 else 2, department_label='信息工程系',
                semester_key='2026-2027-1' if i < 4 else '2025-2026-2', semester_label='2026-2027第一学期' if i < 4 else '2025-2026第二学期',
                initially_visible=i < 4, grading_count=1, timeline_items=[], search_text=title + ' 人工智能2601班 信息工程系')
    offerings.append(item)
context = dict(class_offerings=offerings, dashboard_initial_filter='all', dashboard_initial_search='',
    dashboard_current_semester_key='2026-2027-1', dashboard_initial_visible_count=3,
    dashboard_filters=[dict(href='#', value='all', label='全部', count=4), dict(href='#', value='attention', label='待处理', count=0)],
    dashboard_semester_options=[dict(key='2026-2027-1', label='2026-2027第一学期', count=3), dict(key='2025-2026-2', label='2025-2026第二学期', count=1)])
toolbar = re.search(r'<details class="ls-course-options"[\s\S]+?</details>', source).group()
search = re.search(r'<form method="get" action="/dashboard" class="ls-course-search"[\s\S]+?</form>', source).group()
list_open = re.search(r'<div class="ls-course-list[\s\S]+?>', source).group()
template = search + toolbar + list_open + '{% for offering in class_offerings %}{% set offering_summary = {} %}' + article + '{% endfor %}</div>'
sys.stdout.reconfigure(encoding='utf-8')
print(env.from_string(template).render(**context))
`], { encoding: 'utf8' });

async function mount(page: Page, width = 1280, appearance = 'light') {
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const requests: string[] = [];
  await page.route('https://dashboard-layout.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/api/manage/academic/course-schedule/overview') {
      requests.push(url.search);
      const year = url.searchParams.get('year') || '2026-2027', term = url.searchParams.get('term') || '1';
      return route.fulfill({ json: { status: 'success', overview: {
        selected_term: { year, term, label: year === '2026-2027' ? '2026-2027第一学期' : '2025-2026第二学期' },
        terms: [{ year: '2026-2027', term: '1', label: '2026-2027第一学期', status: 'current' }, { year: '2025-2026', term: '2', label: '2025-2026第二学期', status: 'ended' }],
        section_range: { min: 1, max: 8 }, weeks: [{ week_index: 1, label: '第1周', lessons: [] }],
      } } });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="${appearance}" data-lq-tier="A" data-lq-glass="tinted"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>教师课堂布局</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/lq/pages/dashboard.css"><style>body{margin:0}main{padding:24px;max-width:1180px;margin:auto}@media(min-width:1024px){main{margin-left:264px;width:calc(100% - 264px)}}</style></head><body class="dashboard-page ls-page role-teacher"><main><section data-dashboard-root data-lq-dashboard data-dashboard-role="teacher" data-initial-group-mode="flat" data-current-semester-key="2026-2027-1"><section class="ls-courses" data-lq-material="content"><h2>我的课堂</h2>${markup}<p data-results-summary></p><span data-visible-count>3</span><div data-empty-search hidden><div data-empty-search-chips></div><div data-empty-search-suggestions></div></div></section></section></main></body></html>` });
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto('https://dashboard-layout.test/');
  return requests;
}

async function readableCards(page: Page, count = 3) {
  const cards = page.locator('[data-offering-card]:visible');
  await expect(cards).toHaveCount(count);
  const metrics = await cards.evaluateAll(nodes => nodes.map(node => {
    const title = node.querySelector('h3')!, copy = node.querySelector('.ls-course-copy')!, rect = node.getBoundingClientRect();
    return { width: copy.getBoundingClientRect().width, height: title.getBoundingClientRect().height,
      line: parseFloat(getComputedStyle(title).lineHeight), overflow: node.scrollWidth - node.clientWidth,
      right: rect.right, left: rect.left, enter: node.querySelector('[data-offering-enter]')?.getAttribute('href') };
  }));
  for (const item of metrics) {
    expect(item.width).toBeGreaterThanOrEqual(160);
    expect(item.height).toBeLessThanOrEqual(item.line * 2 + 1);
    expect(item.overflow).toBeLessThanOrEqual(1);
    expect(item.left).toBeGreaterThanOrEqual(0);
    expect(item.right).toBeLessThanOrEqual(page.viewportSize()!.width);
    expect(item.enter).toMatch(/^\/classroom\/\d+$/);
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(page.viewportSize()!.width);
}

for (const { width, appearance } of [{ width: 1280, appearance: 'light' }, { width: 1024, appearance: 'dark' }, { width: 390, appearance: 'dark' }]) {
  test(`teacher cards retain title width in SSR and all three views ${width}/${appearance}`, async ({ page }, info) => {
    const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
    await mount(page, width, appearance);
    await readableCards(page); // Actual Jinja before its controller runs.
    await page.evaluate(async () => { await import('/static/js/dashboard.js'); });
    await page.locator('.ls-course-options > summary').click();
    const identities = await page.locator('[data-offering-card]').evaluateAll(nodes => { (window as any).originalCards = nodes; return nodes.length; });
    expect(identities).toBe(3);
    for (const mode of ['flat', 'department', 'course']) {
      await page.locator(`[data-group-mode="${mode}"]`).click();
      await readableCards(page);
      expect(await page.locator('.dashboard-group-section').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).backgroundImage === 'none'))).toBe(true);
      expect(await page.locator('[data-offering-card]').evaluateAll(nodes => nodes.every(node => (window as any).originalCards.includes(node)))).toBe(true);
      await page.screenshot({ path: info.outputPath(`courses-${mode}.png`), fullPage: true });
    }
    await page.locator('[data-dashboard-search]').fill('Python');
    await readableCards(page, 1);
    await expect(page.locator('[data-offering-card]:visible h3')).toHaveText('Python程序设计');
    expect(errors).toEqual([]);
  });
}

test('both semester controls use shared glass and keep filter/deck values through view changes', async ({ page }) => {
  const requests = await mount(page);
  await page.evaluate(async () => { await import('/static/js/dashboard.js'); });
  await page.locator('.ls-course-options > summary').click();
  const filter = page.getByRole('combobox', { name: '课堂筛选学期', exact: true });
  await filter.click();
  const popup = page.locator('.lq-selection__popup');
  await expect(popup).toBeVisible();
  expect(await popup.evaluate(node => getComputedStyle(node).backdropFilter)).toContain('blur(');
  await filter.press('ArrowDown'); await filter.press('Home'); await filter.press('Enter');
  await readableCards(page, 4);
  await expect(page.locator('[data-semester-filter]')).toHaveValue('');
  await filter.press('ArrowDown'); await filter.press('ArrowDown'); await filter.press('Enter');
  await readableCards(page, 3);
  await page.locator('[data-group-mode="schedule3d"]').click();
  const deck = page.getByRole('combobox', { name: '学年学期', exact: true });
  await expect(deck).toBeEnabled();
  await expect(deck).toHaveValue('2026-2027第一学期（进行中）');
  await deck.press('ArrowDown'); await deck.press('End'); await deck.press('Enter');
  await expect(filter).toHaveValue('2025-2026第二学期（1）');
  await expect(deck).toHaveValue('2025-2026第二学期（已结束）');
  await expect.poll(() => requests.some(request => request.includes('year=2025-2026') && request.includes('term=2'))).toBe(true);
  await page.evaluate(() => { (window as any).originalDeck = document.querySelector('.cs-deck'); });
  await page.locator('[data-group-mode="flat"]').click(); await readableCards(page, 1);
  await page.locator('[data-group-mode="schedule3d"]').click();
  await expect(deck).toHaveCount(1); await expect(deck).toHaveValue('2025-2026第二学期（已结束）');
  expect(await page.evaluate(() => document.querySelector('.cs-deck') === (window as any).originalDeck)).toBe(true);
  await deck.click(); await expect(popup).toBeVisible();
  await page.keyboard.press('Escape'); await expect(popup).toBeHidden(); await expect(deck).toBeFocused();
});
