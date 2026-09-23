import { test, expect, type Locator, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';

// Real authored templates and the production React island, isolated from app/DB
// imports. Do not copy their DOM: that concealed the SSR-only extra surface.
const python = process.env.LQ_TEST_PYTHON || (process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python');
const fixture = JSON.parse(execFileSync(python, ['-c', String.raw`
import importlib.util, json, re, sys, types
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup
root = Path.cwd()
package = types.ModuleType('layout_surface_fixture')
package.__path__ = [str(root / 'classroom_app')]
sys.modules[package.__name__] = package
def load(name):
    spec = importlib.util.spec_from_file_location(package.__name__ + '.' + name, root / 'classroom_app' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
components, menus, navmenus, shells, collapsibles = [load(name) for name in ['lq_components', 'lq_menu_tooltip', 'lq_nav_menu', 'lq_shells', 'lq_collapsible']]
def props(kind, **values):
    if kind in collapsibles.COLLAPSIBLE_KINDS: return collapsibles.lq_collapsible_props(kind, **values)
    if kind in shells.SHELL_KINDS: return shells.lq_shell_props(kind, **values)
    if kind in navmenus.NAV_MENU_KINDS: return navmenus.lq_nav_menu_kind_props(kind, **values)
    if kind in menus.MENU_TOOLTIP_KINDS: return menus.lq_menu_tooltip_props(kind, **values)
    return components.lq_props(kind, **values)
env = Environment(loader=FileSystemLoader(root / 'templates'), autoescape=True, undefined=StrictUndefined)
env.globals.update(lq_props=props, lq_family_enabled=lambda _: True, asset_url=lambda name: '/static/' + name)
items = [dict(key='todo:' + str(i), kind='manual', title=title, subtitle='Python 入门', offering_id=1,
              href='/classroom/1', status='pending', is_completed=False, is_actionable=True,
              starts_at='', due_at='', date_label='今天', time_label='09:00', type_label='待办',
              status_label='待处理', action_label='查看', date_bucket='today', agenda_data={})
         for i, title in enumerate(['完成今天的学习任务', '复习课堂知识', '整理学习笔记'], 1)]
workspace = dict(total=3, filtered_total=3, pending_total=3, actionable_total=3, has_more=False,
                 focus_items=items, attention_items=items, all_items=items, offering_options=[],
                 action_summary=dict(total=3, today=3, overdue=0, undated=0),
                 generated_at='', next_transition_at='', next_cursor=None)
action = Markup(env.from_string("{% from 'macros/lq/button.html' import lq_btn %}{{ lq_btn('创建课堂', variant='prominent', attrs={'data-fixture-action': ''}) }}").render())
result = dict(workspace=workspace)
result['calendar'] = env.get_template('partials/semester_calendar_panel.html').render()
result['collapsible'] = env.from_string("{% from 'macros/lq/collapsible.html' import lq_collapsible %}{% call lq_collapsible('layout-courses', '我的课堂', mode='responsive') %}<p>保留课程内容</p>{% endcall %}").render()
classroom = (root / 'templates/classroom_main_v4.html').read_text(encoding='utf-8')
card = re.search(r'<article\s+class="card assignment-card assignment-card-unified[\s\S]+?</article>', classroom)
assert card
# Full production article; synthetic optional values exclude unrelated controls.
assignment = dict.fromkeys(re.findall(r'assignment\.(\w+)', card.group()))
assignment.update(id=991, title='合成课堂作业', assessment_kind_label='课程作业', effective_status='published', source_feature='personal_stage')
result['assignment'] = env.from_string(card.group()).render(
    assignment=assignment, classroom=dict(id=991), user_info=dict(role='teacher'),
    status_label='进行中', status_class='badge-success', teacher_metrics=dict(submitted_count=2, graded_count=1),
    teacher_pending_grade_count=1, teacher_grading_count=0, teacher_returned_count=0,
    teacher_unsubmitted_count=1, teacher_late_count=0, teacher_total_students=3)
for role in ['student', 'teacher']:
    user = dict(role=role, id=991, name='合成' + ('学生' if role == 'student' else '教师'), nickname='', email='fixture@example.invalid')
    context = dict(user_info=user, ui_palette=dict(enabled=False), navbar_shell_id='navbar-topbar',
                   navbar_shell_report=False, navbar_shell_title='课堂平台', navbar_shell_actions=action,
                   page_title='教师工作台', active_page='home', manage_pilot_actions=action,
                   manage_nav=dict(active_domain='home', domain_meta={}, domains=[]),
                   workspace=workspace, inbox=dict(total=3, items=items, sources=[]))
    template = 'dashboard.html' if role == 'student' else 'dashboard_teacher.html'
    source = (root / 'templates' / template).read_text(encoding='utf-8')
    section = re.search(r'<section class="ls-focus[\s\S]+?</section>', source)
    assert section, template
    result[role] = dict(
        topbar=env.get_template('partials/lq_navbar_topbar.html' if role == 'student' else 'manage/lq_topbar.html').render(**context),
        sidebar=env.get_template('manage/lq_sidebar.html').render(**context) if role == 'teacher' else '',
        focus=env.from_string(section.group()).render(**context))
assert not any(name in sys.modules for name in ['app', 'core', 'classroom_app', 'sqlite3', 'psycopg', 'dotenv'])
result['isolated'] = True
sys.stdout.reconfigure(encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
`], { encoding: 'utf8' }));

type Role = 'student' | 'teacher';
type Mode = { role: Role; appearance: 'light' | 'dark'; glass: 'tinted' | 'off'; tier?: 'A' | 'C'; contrast?: 'normal' | 'more' };
const revision = process.env.LQ_LAYOUT_ASSET_REVISION || '';
const staticRoot = path.resolve('static');
const assetRoot = revision ? path.join(staticRoot, 'assets', revision) : staticRoot;
const vite = JSON.parse(fs.readFileSync(path.join(staticRoot, 'dist/manifest.json'), 'utf8'));
const island = '/static/dist/' + vite['frontend/src/islands/dashboard-workspace.tsx'].file;

function localAsset(urlPath: string) {
  if (!urlPath.startsWith('/static/')) return null;
  const relative = urlPath.slice('/static/'.length);
  const file = path.resolve(relative.startsWith('dist/') || relative.startsWith('assets/') ? staticRoot : assetRoot, relative);
  return file.startsWith(staticRoot + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile() ? file : null;
}

async function mount(page: Page, mode: Mode) {
  const html = fixture[mode.role];
  const dashboard = `<section class="ls-shell" data-dashboard-root data-dashboard-role="${mode.role}" data-lq-dashboard><div data-lanshare-island="dashboard-workspace" data-island-id="dashboard-workspace"><script type="application/json" data-dashboard-workspace-payload>${JSON.stringify(fixture.workspace)}</script>${html.focus}</div></section>`;
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'https://lq-layout-surfaces.test') return route.abort();
    const file = localAsset(url.pathname);
    if (file) return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    if (url.pathname === '/api/dashboard/workspace') return route.fulfill({ json: { status: 'success', workspace: fixture.workspace } });
    if (url.pathname !== '/') return route.fulfill({ status: 404, body: 'isolated layout fixture' });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-ui-palette="indigo" data-appearance="${mode.appearance}" data-lq-glass="${mode.glass}" data-lq-tier="${mode.tier || 'A'}" data-lq-contrast="${mode.contrast || 'normal'}" data-lq-forced-colors="false"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>布局外壳材质回归</title><link rel="stylesheet" href="/static/css/tailwind-app.css"><style>body{margin:0}.fixture-main{min-width:0}body:not(.lq-manage-shell) .fixture-main{max-width:1080px;margin:auto}</style></head><body class="dashboard-page ls-page role-${mode.role}${mode.role === 'teacher' ? ' lq-manage-shell' : ''}">${mode.role === 'teacher' ? `<div class="lq-manage-layout">${html.sidebar}<div class="manage-main">${html.topbar}<main class="manage-content fixture-main">${dashboard}</main></div></div>` : `${html.topbar}<main class="fixture-main">${dashboard}</main>`}</body></html>` });
  });
  await page.goto('https://lq-layout-surfaces.test/');
  await expect(page.locator('.ls-focus-item')).toHaveCount(3);
}

async function paint(locator: Locator) {
  return locator.evaluate(element => {
    const s = getComputedStyle(element);
    return {
      background: s.backgroundColor, image: s.backgroundImage, shadow: s.boxShadow,
      borders: [s.borderTopWidth, s.borderRightWidth, s.borderBottomWidth, s.borderLeftWidth],
      radius: parseFloat(s.borderTopLeftRadius), filter: s.backdropFilter,
      before: { content: getComputedStyle(element, '::before').content, background: getComputedStyle(element, '::before').backgroundColor, image: getComputedStyle(element, '::before').backgroundImage },
    };
  });
}

async function expectLayoutOnly(locator: Locator, borderWidth = '0px') {
  const state = await paint(locator);
  expect(state.background).toBe('rgba(0, 0, 0, 0)');
  expect(state.image).toBe('none');
  expect(state.shadow).toBe('none');
  expect(state.borders).toEqual(Array(4).fill(borderWidth));
  if (!['none', 'normal'].includes(state.before.content)) {
    expect(state.before.background).toBe('rgba(0, 0, 0, 0)');
    expect(state.before.image).toBe('none');
  }
}

async function expectSurface(locator: Locator, rounded = true) {
  const state = await paint(locator);
  expect(state.background).not.toBe('rgba(0, 0, 0, 0)');
  if (rounded) expect(state.radius).toBeGreaterThan(0);
}

async function desktopContract(page: Page, role: Role) {
  const pane = page.locator('[data-lq-shell="topbar"] [data-lq-pane="actions"]');
  await expectLayoutOnly(pane);
  await expectLayoutOnly(pane.locator(':scope > .lq-shell-pane__surface'));
  await expectSurface(page.locator('[data-lq-shell="topbar"]'));
  await expectSurface(page.locator('[data-fixture-action]'));
  if (role === 'student') {
    await expectSurface(page.locator('.lq-report-card-utilities'));
    await expectSurface(page.locator('.lq-report-card-tools'));
    await expectSurface(page.locator('[data-message-center-bell]'));
  } else {
    await expectLayoutOnly(page.locator('.lq-manage-sidebar'));
    await expectLayoutOnly(page.locator('.lq-manage-sidebar-pane'));
    await expectSurface(page.locator('.lq-sidebar__surface'));
  }
}

async function dashboardContract(page: Page, role: Role) {
  const list = page.locator('.ls-focus-list');
  await expect(list).not.toHaveClass(/\blq-list\b/);
  await expect(list).not.toHaveAttribute('data-lq-material');
  await expectLayoutOnly(list);
  await expectSurface(page.locator('.ls-focus'));
  await expectSurface(page.locator('.ls-focus .ls-button-primary').first());
  if (role === 'student') {
    const cards = page.locator('.ls-focus-item');
    for (let i = 0; i < 3; i++) expect((await paint(cards.nth(i))).radius).toBeGreaterThan(0);
    // The gap between individual cards must show the parent panel, not a list plate.
    const gap = await cards.evaluateAll(nodes => nodes[1].getBoundingClientRect().top - nodes[0].getBoundingClientRect().bottom);
    expect(gap).toBeGreaterThan(0);
  }
}

test.describe('Real navigation and dashboard layout surfaces', () => {
  for (const role of ['student', 'teacher'] as const) {
    for (const appearance of ['light', 'dark'] as const) {
      for (const glass of ['tinted', 'off'] as const) {
        test(`${role} ${appearance} ${glass}: SSR, enhancement, React and drawer resize`, async ({ page }, testInfo) => {
          test.setTimeout(60_000);
          expect(fixture.isolated).toBe(true);
          const errors: string[] = [];
          page.on('pageerror', error => errors.push(error.message));
          await mount(page, { role, appearance, glass });
          await desktopContract(page, role);
          await dashboardContract(page, role);
          await page.screenshot({ path: testInfo.outputPath('desktop-ssr.png'), fullPage: true });

          await page.evaluate(async () => {
            const { enhanceShell } = await import('/static/js/lq/shells.js');
            document.querySelectorAll('[data-lq-shell]').forEach(root => enhanceShell(root));
          });
          await expect(page.locator('[data-lq-shell="topbar"] [data-lq-pane="actions"]')).toHaveAttribute('data-lq-pane-mode', 'inline');
          await desktopContract(page, role);
          await page.addScriptTag({ type: 'module', url: island });
          await expect(page.locator('[data-lanshare-island="dashboard-workspace"]')).toHaveAttribute('data-react-mounted', 'true');
          await dashboardContract(page, role);
          await page.screenshot({ path: testInfo.outputPath('desktop-enhanced.png'), fullPage: true });

          await page.setViewportSize({ width: 390, height: 844 });
          const trigger = page.locator('[data-lq-shell="topbar"] > [data-lq-pane-open="actions"]');
          const pane = page.locator('[data-lq-shell="topbar"] [data-lq-pane="actions"]');
          await expect(pane).toBeHidden();
          await trigger.click();
          await expect(pane).toBeVisible();
          await expect(pane).toHaveAttribute('data-lq-pane-mode', 'drawer');
          await expect(pane.locator(':scope > .lq-shell-pane__surface')).toHaveClass(/\blq-glass--thick\b/);
          await expectSurface(pane.locator(':scope > .lq-shell-pane__surface'));
          const material = await paint(pane.locator(':scope > .lq-shell-pane__surface'));
          expect(material.filter === 'none').toBe(glass === 'off');
          await expect(page.locator('[data-fixture-action]')).toBeInViewport();
          const contrast = await new AxeBuilder({ page }).include('[data-lq-pane="actions"]').withRules(['color-contrast']).analyze();
          expect(contrast.violations).toEqual([]);
          await page.screenshot({ path: testInfo.outputPath('mobile-drawer.png'), fullPage: true });
          await pane.locator('.lq-shell-pane__close[data-lq-pane-close]').click();
          await expect(pane).toBeHidden();
          await expect(trigger).toBeFocused();
          await trigger.click();
          await expect(pane).toBeVisible();
          await page.setViewportSize({ width: 1440, height: 980 });
          await expect(pane).toHaveAttribute('data-lq-pane-mode', 'inline');
          await desktopContract(page, role);
          await dashboardContract(page, role);
          expect(errors).toEqual([]);
          await testInfo.attach('asset-provenance.json', { contentType: 'application/json', body: JSON.stringify({
            revision: revision || 'working-built-assets',
            css: createHash('sha256').update(fs.readFileSync(localAsset('/static/css/tailwind-app.css')!)).digest('hex'),
            island,
          }, null, 2) });
        });
      }
    }
  }

  for (const appearance of ['light', 'dark'] as const) {
    for (const fallback of [
      { name: 'tinted', glass: 'tinted' }, { name: 'off', glass: 'off' },
      { name: 'tier-C', glass: 'tinted', tier: 'C' }, { name: 'contrast-more', glass: 'tinted', contrast: 'more' },
    ] as const) {
      test(`nested ${appearance} ${fallback.name}: desktop wrappers and mobile/standalone surfaces`, async ({ page }, testInfo) => {
        await mount(page, { role: 'teacher', appearance, ...fallback });
        await page.evaluate(({ calendar, collapsible, assignment }) => {
          const host = document.createElement('div');
          host.id = 'nested-surfaces';
          host.innerHTML = `<section data-lq-dashboard><section class="ls-courses" data-lq-material="content">${collapsible}</section><section class="ls-schedule"><div class="ls-calendar-host">${calendar}</div></section></section><div id="standalone-calendar">${calendar}</div><div class="classroom-page classroom-workspace-v2 role-teacher">${assignment}</div>`;
          document.querySelector('main')!.append(host);
        }, fixture);
        const nested = page.locator('#nested-surfaces');
        const disclosure = nested.locator('.lq-collapsible');
        const embedded = nested.locator('.ls-calendar-host [data-lq-calendar]');
        const independent = nested.locator('#standalone-calendar [data-lq-calendar]');
        const card = nested.locator('.assignment-card-unified');
        const checkNested = async () => {
          // Increased contrast intentionally retains a 2px accessibility outline.
          // Its embedded calendar still must not paint a second fill or shadow.
          await expectLayoutOnly(embedded, fallback.name === 'contrast-more' ? '2px' : '0px');
          await expectSurface(independent);
          await expectSurface(card);
          for (const selector of ['.card-body', '.assignment-card-top', '.assignment-card-footer']) {
            const state = await paint(card.locator(selector));
            expect(state.background, selector).toBe('rgba(0, 0, 0, 0)');
            expect(state.image, selector).toBe('none');
            expect(state.shadow, selector).toBe('none');
          }
          await expectSurface(card.locator('.assignment-card-insight').first());
          await expectSurface(card.locator('.assignment-card-primary-link'));
        };
        await expectLayoutOnly(disclosure);
        await checkNested();
        await page.setViewportSize({ width: 390, height: 844 });
        await expectSurface(disclosure);
        await checkNested();
        await nested.screenshot({ path: testInfo.outputPath('nested-mobile.png') });
      });
    }
  }
});
