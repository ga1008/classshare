import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { buildReactFixture } from './lq-react-fixture';

let script: string;
test.beforeAll(async () => {
  script = await buildReactFixture(`
    import React from 'react';
    import {ClassroomWorkspace} from '@/islands/classroom-workspace';
    import {DashboardWorkspace} from '@/islands/dashboard-workspace';
    import {normalizeDashboardItems} from '@/lib/dashboard-workspace';
    import {mountReactIslands,unmountReactIsland} from '@/lib/mount-react-island';
    import {classroomReadiness} from '@/lib/classroom-bootstrap-ready';
    import {loadLayerSystem} from '@/lib/lq-layer';
    const initial={total:1,filtered_total:1,pending_total:1,actionable_total:1,has_more:false,focus_items:[],attention_items:[],offering_options:[],generated_at:'',next_transition_at:'',next_cursor:null,action_summary:{total:1,today:0,overdue:0,undated:1},all_items:normalizeDashboardItems([{key:'todo:1',kind:'manual',title:'Fixture todo',agenda_data:{is_manual:true,todo_id:1}}])};
    window.workspaceApi={
      classroom:()=>{document.body.className='classroom-workspace-v2';return mountReactIslands({islandName:'classroom-fixture',getProps:()=>({}),render:()=> <ClassroomWorkspace/>});},
      dashboard:()=>{document.body.className='ls-page';return mountReactIslands({islandName:'dashboard-fixture',getProps:()=>({}),render:()=> <DashboardWorkspace initial={initial}/>});},
      unmount:id=>unmountReactIsland(document.getElementById(id)),
      ready:()=>classroomReadiness.complete(),system:()=>loadLayerSystem(document),
    };
    window.fixtureLoaded=true;
  `);
});

async function setup(page: Page, savedRestore = false) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin!=='http://lq-workspaces.test'){await route.abort();throw new Error(`Unexpected network: ${url.origin}`);}
    if(url.pathname==='/fixture.js'){await route.fulfill({contentType:'text/javascript',body:script});return;}
    const css=url.pathname.match(/^\/static\/css\/(tailwind-app|classroom_workspace)\.css$/);
    if(css){await route.fulfill({contentType:'text/css',body:fs.readFileSync(path.resolve('static/css',`${css[1]}.css`),'utf8')});return;}
    const match=url.pathname.match(/^\/static\/(?:assets\/[a-f0-9]{64}\/)?js\/(lq\/layer\.js|ui_overlay_motion\.js|ui_popover\.js|ui_popover_geometry\.js)$/);
    if(match){await route.fulfill({contentType:'text/javascript',body:fs.readFileSync(path.resolve('static/js',match[1]),'utf8')});return;}
    if(url.pathname!=='/'){await route.abort();throw new Error(`Unexpected fixture path: ${url.pathname}`);}
    await route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/classroom_workspace.css"><style>
      /* Keep an observable exit; actual shell sizing/scrolling/order use product CSS. */
      [data-ui-dialog-overlay],[data-ui-dialog-content]{opacity:1;transition:opacity .16s}[data-state="closed"]{opacity:0;pointer-events:none}
      [hidden]{display:none!important}
    </style><body style="overflow:clip">
      <button id="tasks-trigger" data-cw-open="tasks">tasks</button><div id="cw-tasks-preview"></div><div id="classroom" data-lanshare-island="classroom-fixture"></div>
      <div id="source-storage"><span id="source-before"></span><div id="source" data-cw-source="tasks" hidden><input id="classroom-draft" value="classroom draft"><button id="external-editor" data-cw-external-modal>external editor</button></div><span id="source-after"></span></div>
      <button id="legacy-create" data-cw-create-assignment>legacy create</button>
      <section data-dashboard-root data-dashboard-role="student"><button id="calendar-trigger" data-ls-open="calendar">calendar</button><button id="legacy-add" data-agenda-add-todo>legacy add</button><div id="dashboard" data-lanshare-island="dashboard-fixture"></div></section>
      <div id="calendar-storage" data-ls-calendar-storage hidden><span></span><div id="calendar" data-semester-calendar-root><input id="calendar-week" value="week 6"><button data-semester-todo-add>calendar add</button></div><span id="calendar-after"></span></div>
      <script>
        window.__LS_ASSET_REV='${'b'.repeat(64)}';
        window.APP_CONFIG={userRole:'teacher',classOfferingId:123,userInfo:{id:9},assignmentWorkspaceItems:[],teachingPlan:{sessions:[{id:1,order_index:1,title:'lesson'}]}};
        window.stats={closed:0,external:[],created:0,todo:[],selected:[],invalidations:0};window.requests=[];
        window.fetch=(url,options)=>new Promise(resolve=>window.requests.push({url,signal:options.signal,resolve:data=>resolve({ok:true,json:async()=>data})}));
        document.addEventListener('classroom:workspace-closed',()=>{window.stats.closed++;});
        document.addEventListener('classroom:select-session',event=>window.stats.selected.push(event.detail));
        window.addEventListener('lanshare:dashboard-calendar-invalidate',()=>window.stats.invalidations++);
        document.getElementById('external-editor').addEventListener('click',()=>window.stats.external.push({parent:document.getElementById('source').parentElement.id,locked:document.body.style.overflow}));
        document.getElementById('legacy-create').addEventListener('click',()=>window.stats.created++);
        document.getElementById('legacy-add').addEventListener('click',()=>window.stats.todo.push({parent:document.getElementById('calendar').parentElement.id,locked:document.body.style.overflow}));
        window.addEventListener('lanshare:todo-edit',event=>{window.lastTodo=event.detail;});
        ${savedRestore ? "sessionStorage.setItem('classroom-workspace:teacher:123:9',JSON.stringify({restore:true,panel:'session-detail',sessionOrder:1}));" : ''}
      </script><script type="module" src="/fixture.js"></script></body>`});
  });
  await page.goto('http://lq-workspaces.test/');
  await page.waitForFunction(()=>(window as any).fixtureLoaded);
  return errors;
}
const dialog=(page:Page)=>page.locator('[data-ui-dialog-content]');
const nativeSelect=(page:Page,label:string)=>dialog(page).locator('.lq-field').filter({has:page.locator('label').filter({hasText:new RegExp(`^${label}$`)})}).locator('select');

test('real classroom surface retains identity/draft/order and delivers the editor only after exit',async({page})=>{
  const errors=await setup(page);
  await page.evaluate(()=>{const w=window as any;w.source=document.getElementById('source');w.workspaceApi.classroom();});
  for(let index=0;index<3;index++){
    await page.locator('#tasks-trigger').click();await expect(dialog(page)).toBeVisible();
    await page.locator('#classroom-draft').fill(`draft ${index}`);
    await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);
    await expect(page.locator('#tasks-trigger')).toBeFocused();
  }
  expect(await page.evaluate(()=>({same:(window as any).source===document.getElementById('source'),parent:document.getElementById('source')!.parentElement!.id,next:document.getElementById('source')!.nextElementSibling!.id,hidden:document.getElementById('source')!.hidden,closed:(window as any).stats.closed}))).toEqual({same:true,parent:'source-storage',next:'source-after',hidden:true,closed:3});
  await page.locator('#tasks-trigger').click();await expect(dialog(page)).toBeVisible();
  await expect(page.locator('#classroom-draft')).toHaveValue('draft 2');
  await page.locator('#external-editor').click();
  await expect(dialog(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.external)).toEqual([{parent:'source-storage',locked:'clip'}]);
  expect(errors).toEqual([]);
});

test('reopening the classroom cancels an editor handoff from an unfinished exit',async({page})=>{
  await setup(page);await page.evaluate(()=>(window as any).workspaceApi.classroom());
  await page.locator('#tasks-trigger').click();await expect(dialog(page)).toBeVisible();
  await page.locator('#external-editor').click();
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','closing');
  await page.evaluate(()=>document.dispatchEvent(new CustomEvent('classroom:workspace-panel',{detail:{panel:'tasks'}})));
  await expect(page.locator('[data-ui-dialog-root]')).toHaveAttribute('data-lq-layer-state','open');
  await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.external)).toEqual([]);
  expect(await page.evaluate(()=>(window as any).stats.closed)).toBe(1);
});

test('a pending classroom editor cannot click after unmount',async({page})=>{
  await setup(page);await page.evaluate(()=>(window as any).workspaceApi.classroom());
  await page.getByRole('button',{name:'新建作业',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('正在准备');
  await page.evaluate(()=>{const w=window as any;w.workspaceApi.unmount('classroom');w.workspaceApi.ready();});
  expect(await page.evaluate(()=>(window as any).stats.created)).toBe(0);
});

for(const unmount of [false,true])test(`saved classroom readiness intent survives StrictMode without a late dispatch (unmount=${unmount})`,async({page})=>{
  await setup(page,true);await page.evaluate(()=>(window as any).workspaceApi.classroom());
  await expect(page.locator('[data-cw-task-collection]')).toBeVisible();
  await page.evaluate(flag=>{const w=window as any;if(flag)w.workspaceApi.unmount('classroom');w.workspaceApi.ready();},unmount);
  await expect.poll(()=>page.evaluate(()=>(window as any).stats.selected.length)).toBe(unmount?0:1);
});

test('real dashboard calendar restores its original node before the todo handoff',async({page})=>{
  const errors=await setup(page);await page.evaluate(()=>{const w=window as any;w.calendar=document.getElementById('calendar');w.workspaceApi.dashboard();});
  await page.locator('#calendar-trigger').click();await expect(dialog(page)).toBeVisible();
  await page.locator('#calendar-week').fill('week 8');
  await dialog(page).getByRole('button',{name:'全部事项',exact:true}).click();
  expect(await page.evaluate(()=>({same:(window as any).calendar===document.getElementById('calendar'),parent:document.getElementById('calendar')!.parentElement!.id,next:document.getElementById('calendar')!.nextElementSibling!.id}))).toEqual({same:true,parent:'calendar-storage',next:'calendar-after'});
  await dialog(page).getByRole('button',{name:'学期日历',exact:true}).click();
  await expect(page.locator('#calendar-week')).toHaveValue('week 8');
  await page.locator('[data-semester-todo-add]').click();await expect(dialog(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.todo)).toEqual([{parent:'calendar-storage',locked:'clip'}]);
  expect(errors).toEqual([]);
});

test('dashboard unmount cancels late todo return and late request side effects',async({page})=>{
  const errors=await setup(page);await page.evaluate(()=>(window as any).workspaceApi.dashboard());
  await page.getByRole('button',{name:'全部事项与历史',exact:true}).click();await expect(dialog(page)).toBeVisible();
  await dialog(page).getByRole('button',{name:'编辑',exact:true}).click();await expect(dialog(page)).toHaveCount(0);
  await page.evaluate(()=>window.dispatchEvent(new Event('lanshare:dashboard-workspace-refresh')));
  await expect.poll(()=>page.evaluate(()=>(window as any).requests.length)).toBe(1);
  await page.evaluate(()=>{const w=window as any;w.workspaceApi.unmount('dashboard');w.lastTodo.afterClose();w.requests[0].resolve({all_items:[],total:0});});
  expect(await page.evaluate(()=>(window as any).requests[0].signal.aborted)).toBe(true);
  await expect(dialog(page)).toHaveCount(0);
  expect(await page.evaluate(()=>(window as any).stats.invalidations)).toBe(0);
  await expect(page.locator('body')).toHaveCSS('overflow','clip');
  expect(errors).toEqual([]);
});

for (const appearance of ['light', 'dark']) for (const width of [1440, 390]) test(`LQ actual workspace controls use shared components ${appearance} ${width}`, async ({ page }) => {
  await page.setViewportSize({ width, height: 980 });
  const errors = await setup(page);
  await page.evaluate(appearance => {
    Object.assign(document.documentElement.dataset, { theme: 'lanshare', appearance, lqGlass: 'tinted' });
    (window as any).workspaceApi.dashboard();
  }, appearance);
  expect(await page.locator('#dashboard button:not(.lq-btn):not(.lq-chip)').count()).toBe(0);
  await page.getByRole('button', { name: '全部事项与历史', exact: true }).click();
  await expect(dialog(page)).toBeVisible();
  await expect(dialog(page).getByLabel('搜索事项')).toHaveClass(/lq-input/);
  await expect(dialog(page)).toHaveAttribute('data-lq-material', 'raised');
  await expect(dialog(page)).toHaveCSS('position', 'fixed');
  await expect(dialog(page)).toHaveCSS('backdrop-filter', /blur\(/);
  await expect(page.locator('[data-ui-dialog-overlay]')).toHaveClass(/lq-scrim/);
  await expect(page.locator('[data-ui-dialog-overlay]')).toHaveCSS('backdrop-filter', 'none');
  await expect(nativeSelect(page, '课堂')).toHaveClass(/lq-select/);
  await expect(dialog(page).getByRole('combobox', { name: '课堂', exact: true })).toHaveClass(/lq-dropdown__trigger/);
  await expect(dialog(page).getByRole('button', { name: '关闭', exact: true })).toHaveClass(/lq-btn/);
  await expect(dialog(page).getByRole('button', { name: '全部事项', exact: true })).toHaveAttribute('aria-pressed', 'true');
  expect(await dialog(page).locator('[data-lq-component]').evaluateAll(nodes => nodes.every(node => getComputedStyle(node).backdropFilter === 'none'))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  fs.mkdirSync('.codex-temp/lq-react-workspaces', { recursive: true });
  await expect(dialog(page)).toHaveCSS('opacity', '1');
  const materials = async () => page.locator('[data-ui-dialog-root], [data-ui-dialog-overlay], [data-ui-dialog-content]').evaluateAll(nodes => nodes.map(node => {
    const css = getComputedStyle(node);
    return { classes: node.className, opacity: css.opacity, background: css.background, backdrop: css.backdropFilter, filter: css.filter, transform: css.transform, blur: css.getPropertyValue('--lq-material-blur') };
  }));
  fs.writeFileSync(`.codex-temp/lq-react-workspaces/dashboard-${appearance}-${width}-material.json`, JSON.stringify(await materials(), null, 2));
  await page.screenshot({ path: `.codex-temp/lq-react-workspaces/dashboard-${appearance}-${width}.png`, fullPage: true });
  await dialog(page).getByLabel('搜索事项').fill('待办查询');
  await expect.poll(() => page.evaluate(() => (window as any).requests.some((request: any) => new URL(request.url, location.href).searchParams.get('q') === '待办查询'))).toBe(true);
  await page.keyboard.press('Escape');
  await expect(dialog(page)).toHaveCount(0);
  await page.evaluate(() => {
    const w = window as any;
    w.workspaceApi.unmount('dashboard');
    w.APP_CONFIG.assignmentWorkspaceItems = [{ id: 22, title: '课堂实验报告', kind: 'homework', status: 'new', submissionStatus: 'unsubmitted', accepting: false, lateOpen: false, deadlinePhase: 'none', countdownAt: '', serverNow: '', canResubmit: false, resubmissionDueAt: '', groupPending: false }];
    w.workspaceApi.classroom();
  });
  await expect(page.locator('#cw-tasks-preview .lq-chip--filter')).toHaveCount(3);
  await expect(page.locator('#cw-tasks-preview .cw-task-card.lq-card.lq-surface[data-lq-component="surface"]')).toHaveCount(1);
  await expect(page.locator('#cw-tasks-preview .cw-task-card')).toHaveCSS('backdrop-filter', 'none');
  expect(await page.locator('#cw-tasks-preview button:not(.lq-btn):not(.lq-chip)').count()).toBe(0);
  await page.locator('#cw-tasks-preview').screenshot({ path: `.codex-temp/lq-react-workspaces/classroom-cards-${appearance}-${width}.png` });
  await page.locator('#tasks-trigger').click();
  await expect(nativeSelect(page, '任务分类')).toHaveClass(/lq-select/);
  await dialog(page).getByRole('combobox', { name: '任务状态', exact: true }).click();
  await page.getByRole('option', { name: '草稿', exact: true }).click();
  await expect(nativeSelect(page, '任务状态')).toHaveValue('draft');
  await expect(dialog(page).getByRole('combobox', { name: '任务状态', exact: true })).toHaveText('草稿');
  await expect(dialog(page).getByLabel('查找任务')).toHaveClass(/lq-input/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(dialog(page)).toHaveCSS('opacity', '1');
  fs.writeFileSync(`.codex-temp/lq-react-workspaces/classroom-${appearance}-${width}-material.json`, JSON.stringify(await materials(), null, 2));
  await page.screenshot({ path: `.codex-temp/lq-react-workspaces/classroom-${appearance}-${width}.png`, fullPage: true });
  await page.evaluate(() => { document.documentElement.dataset.lqGlass = 'off'; });
  await expect(dialog(page)).toHaveCSS('backdrop-filter', 'none');
  await dialog(page).getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog(page)).toHaveCount(0);
  await expect(page.locator('#tasks-trigger')).toBeFocused();
  expect(errors).toEqual([]);
});
