import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

const source = fs.readFileSync('static/js/manage_offerings.js', 'utf8');
const moduleSource = source.slice(0, source.lastIndexOf('\nbindEvents();')) + `
bindEvents();
window.planFixture = { preview: fetchPreview, save: () => handleSave({preventDefault(){}}) };
`;
const form = execFileSync('python', ['-X','utf8','-c', `
from pathlib import Path
from jinja2 import Environment
s=Path('templates/manage/offerings.html').read_text(encoding='utf-8')
s=s[s.index('<form id="offeringSaveForm"'):]
s=s[:s.index('</form>')+7]
print(Environment().from_string(s).render(explain_button=lambda *a,**k:'', my_semesters=[dict(id=90,name='2026秋季',start_date='2026-09-01',end_date='2027-01-30')], my_classes=[dict(id=30,name='示例班级')], my_courses=[dict(id=20,name='示例课程')], my_textbooks=[dict(id=91,title='示例教材')]))
`], {encoding:'utf8'});

async function mount(page: Page) {
  const posts: any[] = [], previews: any[] = [], errors: string[] = [];
  const state = { saveStatus:503, revision:'a'.repeat(64), blockers: [] as string[], holdPreview:false, held:false, holdSave:false, saveHeld:false };
  let releasePreview!: () => void, releaseSave!: () => void;
  const previewGate = new Promise<void>(resolve => {releasePreview=resolve;});
  const saveGate = new Promise<void>(resolve => {releaseSave=resolve;});
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://offering-plan.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/static/js/manage_offerings.js') return route.fulfill({contentType:'text/javascript',body:moduleSource});
    if (pathname.startsWith('/static/js/')) return route.fulfill({contentType:'text/javascript',body:fs.readFileSync('.'+pathname,'utf8')});
    if (pathname.endsWith('/preview')) {
      const body = route.request().postDataJSON(); previews.push(body);
      const result = {status:'success',plan_revision:state.revision,course_name:'示例课程',class_name:'示例班级',edit_impact:{canceled_count:1,blockers:[...state.blockers]},preview:{sessions:[],schedule_info:body.first_class_date,warnings:[],session_count:2}};
      if (state.holdPreview) {state.holdPreview=false;state.held=true;await previewGate;}
      return route.fulfill({json:result});
    }
    if (pathname.endsWith('/save')) {
      posts.push(route.request().postDataJSON());
      if (state.holdSave) {state.saveHeld=true;await saveGate;}
      return route.fulfill({status:state.saveStatus,json:{detail:state.saveStatus===409?'排课资料已更新，请重新预览后保存。':'暂时无法保存，表单已保留。'}});
    }
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${fs.readFileSync('static/css/tailwind-app.css','utf8')}</style><body><main style="max-width:980px;margin:auto;padding:20px">${form}<div id="offeringEditorState"><span id="offeringEditorStateText">编辑课堂</span></div></main><script type="module" src="/static/js/manage_offerings.js"></script></body></html>`});
  });
  await page.goto('http://offering-plan.test/');
  await page.waitForFunction(() => !!(window as any).planFixture);
  await page.evaluate(() => {
    const values = {offeringIdInput:'40',offeringSemesterSelect:'90',offeringClassSelect:'30',offeringCourseSelect:'20',offeringTextbookSelect:'91',offeringFirstClassDateInput:'2026-09-10',offeringScheduleSourceSelect:'fixed_cycle'};
    for (const [id,value] of Object.entries(values)) (document.getElementById(id) as HTMLInputElement).value=value;
    document.getElementById('weeklyScheduleContainer')!.innerHTML='<div data-schedule-row><select data-field="weekday"><option value="3" selected>周四</option></select><input data-field="section_count" value="2"></div>';
  });
  return {posts,previews,errors,state,releasePreview,releaseSave};
}

test('reviewed preview revision is submitted, failed save preserves inputs and can retry', async ({page}) => {
  const h=await mount(page);
  await page.getByRole('button',{name:'刷新预览'}).click();
  await expect(page.locator('#offeringPreviewWarnings')).toContainText('停排 1 个原课次');
  await page.getByRole('button',{name:'开设课堂',exact:true}).click();
  await expect.poll(()=>h.posts.length).toBe(1);
  expect(h.posts[0].expected_plan_revision).toBe('a'.repeat(64));
  await expect(page.locator('#offeringFirstClassDateInput')).toHaveValue('2026-09-10');
  await expect(page.locator('#offeringSaveBtn')).toBeEnabled();
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.posts.length).toBe(2);
  expect(h.posts[1]).toEqual(h.posts[0]);
  expect(h.errors).toEqual([]);
});

test('409 requires a new displayed preview and another user save, without automatic write replay', async ({page}) => {
  const h=await mount(page);h.state.saveStatus=409;
  await page.evaluate(() => (window as any).planFixture.preview());
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.posts.length).toBe(1);
  await expect(page.locator('#offeringSaveBtn')).toBeEnabled();
  h.state.revision='b'.repeat(64);
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.previews.length).toBe(2);
  await expect(page.getByText('预览已更新，请核对时间安排和历史影响后再次保存。')).toBeVisible();
  expect(h.posts).toHaveLength(1);
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.posts.length).toBe(2);
  expect(h.posts[1].expected_plan_revision).toBe('b'.repeat(64));
});

test('late preview cannot replace a newer form or its accepted revision', async ({page}) => {
  const h=await mount(page);h.state.holdPreview=true;
  await page.evaluate(() => {void (window as any).planFixture.preview();});
  await expect.poll(()=>h.state.held).toBe(true);
  h.state.revision='b'.repeat(64);
  await page.locator('#offeringFirstClassDateInput').fill('2026-09-17');
  await page.evaluate(() => (window as any).planFixture.preview());
  await expect(page.locator('#offeringPreviewMeta')).toContainText('2026-09-17');
  h.releasePreview();
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.posts.length).toBe(1);
  expect(h.posts[0]).toMatchObject({first_class_date:'2026-09-17',expected_plan_revision:'b'.repeat(64)});
  await expect(page.locator('#offeringPreviewMeta')).toContainText('2026-09-17');
});

test('historical-content blockers are visible and prevent submission; no preview auto-submits', async ({page}) => {
  const h=await mount(page);h.state.blockers=['已有学习记录，不能改为其他课程内容。'];
  await page.locator('#offeringSaveBtn').click();
  await expect(page.locator('#offeringPreviewWarnings')).toContainText(h.state.blockers[0]);
  expect(h.posts).toHaveLength(0);
  await page.locator('#offeringSaveBtn').click();
  await expect(page.getByText(h.state.blockers[0],{exact:true})).toHaveCount(2);
  expect(h.posts).toHaveLength(0);
});

test('duplicate submit stays disabled until the actual request settles', async ({page}) => {
  const h=await mount(page);h.state.holdSave=true;
  await page.evaluate(() => (window as any).planFixture.preview());
  await page.locator('#offeringSaveBtn').click();
  await expect.poll(()=>h.state.saveHeld).toBe(true);
  await expect(page.locator('#offeringSaveBtn')).toBeDisabled();
  await page.evaluate(() => (window as any).planFixture.save());
  expect(h.posts).toHaveLength(1);
  h.releaseSave();
  await expect(page.locator('#offeringSaveBtn')).toBeEnabled();
});
