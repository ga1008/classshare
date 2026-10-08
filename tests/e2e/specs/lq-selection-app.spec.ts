import {test,expect,type Page,type Locator} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import {loginTeacher,expectSessionRole} from '../fixtures/p03';
const runtime=path.resolve(process.env.LQ_SELECTION_RUNTIME||'.codex-temp/lq-selection-20261008-runtime');
const f=JSON.parse(fs.readFileSync(path.join(runtime,'fixture.json'),'utf8')),s=f.selectSchedule;
if(!f.uiV3Synthetic||!f.lqS3Synthetic||!s?.synthetic||!runtime.startsWith(path.resolve('.codex-temp')+path.sep))throw Error('Owned synthetic runtime required');
const graph=JSON.parse(fs.readFileSync('static/assets/manifest.json','utf8')).revision;
const dropdown=`/static/assets/${graph}/js/lq/dropdown.js`;
const errors=new WeakMap<Page,string[]>();
test.beforeEach(async({page})=>{
  const list:string[]=[];errors.set(page,list);page.on('pageerror',e=>list.push(e.message));page.on('console',m=>{if(m.type()==='warning'&&/LQ dropdown (declaration could not initialize|enhancement unavailable)/.test(m.text()))list.push(m.text());});
  await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await expect.poll(async()=>{try{return(await(await page.request.get('/api/internal/health')).json()).database_path;}catch{return null;}},{timeout:30_000}).toBe(f.databasePath);
});
test.afterEach(async({page},info)=>{
  const graphs=await page.evaluate(()=>[...new Set(performance.getEntriesByType('resource').map(e=>e.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(graphs).toEqual([graph]);expect(errors.get(page)).toEqual([]);
  await info.attach('resource-and-runtime',{body:JSON.stringify({graphs,errors:errors.get(page),databasePath:f.databasePath}),contentType:'application/json'});
});
async function student(page:Page){
  await page.goto('/student/login');await page.waitForLoadState('networkidle');
  await page.locator('#identifier').fill(f.student.studentNumber);await page.locator('#password').fill(f.password);
  await Promise.all([page.waitForURL(/\/dashboard/),page.locator('#student-password-login-form button[type="submit"]').click()]);await page.waitForLoadState('networkidle');await expectSessionRole(page,'student');
}
async function triggerFor(select:Locator){
  // A late dynamic declaration may intentionally initialize on its first real
  // pointer activation. Exercise that public path; never bind it from the test.
  const bound=await select.evaluate(async(n,url)=>Boolean((await import(url)).getDropdown(n)),dropdown);
  if(!bound)await select.click();
  await expect.poll(()=>select.evaluate(async(n,url)=>Boolean((await import(url)).getDropdown(n)),dropdown)).toBe(true);
  const id=await select.evaluate(async(n,url)=>(await import(url)).getDropdown(n).trigger.id,dropdown);
  const trigger=select.page().locator(`[id="${id}"]`);if(!bound&&await trigger.getAttribute('aria-expanded')==='true')await select.page().keyboard.press('Escape');return trigger;
}
async function choose(select:Locator,name:string){
  const trigger=await triggerFor(select);await trigger.click();
  await select.page().getByRole('option',{name,exact:true}).click();
  await expect(trigger).toContainText(name);return trigger;
}
async function openSync(page:Page){const button=page.locator('[data-academic-schedule-sync]');if(!await button.isVisible())await page.locator('[data-lq-pane-open="actions"]').click();await button.click();}
async function overview(page:Page,isStudent=false){
  const response=await page.request.get(isStudent?'/api/dashboard/course-schedule/overview':`/api/manage/academic/course-schedule/overview?year=${s.year}&term=${s.term}`);
  expect(response.status()).toBe(200);return(await response.json()).overview;
}
async function expanded(page:Page,week:number,isStudent=false){
  if(isStudent){
    const label=page.locator('[data-student-week-label]');
    for(let i=0;i<22;i++){const current=Number((await label.innerText()).match(/第\s*(\d+)\s*周/)?.[1]);if(current===week)break;await page.locator(current>week?'[data-student-week-prev]':'[data-student-week-next]').click();}
    await page.locator('[data-student-schedule-expand]').click();
  }else{const slider=page.locator('[data-csd-slider]').first();await slider.fill(String(week));await slider.dispatchEvent('input');await page.locator('.cs-card.is-active .cs-card__bar').click();}
  await expect(page.getByRole('dialog',{name:'整周课表'})).toBeVisible();
}

test('real course statistics long class picker filters once and programmatic reset stays synchronized',async({page},info)=>{
  await loginTeacher(page,f);await page.goto('/manage/academic/course-schedule');await page.waitForLoadState('networkidle');
  for(const selector of ['[data-cs-term]','[data-cs-course]','[data-cs-class]']){
    const select=page.locator(selector);const trigger=await triggerFor(select);await expect(trigger).toBeVisible();expect(await trigger.locator('button').count()).toBe(0);
    const widths=await trigger.evaluate(n=>({trigger:n.getBoundingClientRect().width,slot:n.closest('.lq-field__control')?.getBoundingClientRect().width}));
    expect(widths.slot).toBeGreaterThan(150);expect(Math.abs(widths.trigger-widths.slot!)).toBeLessThanOrEqual(1);
  }
  const requests:string[]=[];page.on('request',r=>{if(r.url().includes('/api/manage/academic/course-schedule/overview'))requests.push(r.url());});
  const native=page.locator('[data-cs-class]'),trigger=await triggerFor(native);await trigger.click();
  const search=page.locator('.lq-dropdown__popup .lq-dropdown__search');await expect(search).toBeVisible();await search.fill('人工智能');
  await expect(page.getByRole('option',{name:'全部班级',exact:true})).toHaveCount(0);await expect(page.getByRole('option',{name:s.longClassLabel,exact:true})).toBeVisible();await search.fill('');
  const option=page.getByRole('option',{name:s.longClassLabel,exact:true});await expect(option).toBeVisible();
  const box=await option.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual((await page.evaluate(()=>innerWidth))+1);
  await page.screenshot({path:info.outputPath('long-class-options.png')});
  await option.click();await expect(native).toHaveValue(s.longClassLabel);await expect(trigger).toContainText(s.longClassLabel);
  await expect.poll(()=>requests.length).toBe(1);await page.waitForLoadState('networkidle');expect(requests).toHaveLength(1);expect(new URL(requests[0]).searchParams.get('class_label')).toBe(s.longClassLabel);
  await page.locator('[data-cs-reset]').click();await expect(native).toHaveValue('');await expect(trigger).toContainText('全部班级');await page.waitForLoadState('networkidle');expect(requests).toHaveLength(2);
  await page.locator('.cs-course-card').first().click();await expect(page.locator('[data-cs-course]')).toHaveValue(s.courseName);await expect(await triggerFor(page.locator('[data-cs-course]'))).toContainText(s.courseName);
  await page.locator('[data-cs-reset]').click();await expect(page.locator('[data-cs-course]')).toHaveValue('');await expect(page.locator('[data-cs-reset]')).toBeDisabled();
  await info.attach('filter-requests',{body:JSON.stringify(requests),contentType:'application/json'});
});

test('real native sync dialog keeps labels keyboard disabled validation form reset and parent ownership',async({page},info)=>{
  await loginTeacher(page,f);await page.goto('/manage/academic/course-schedule');await openSync(page);
  const dialog=page.locator('dialog.cs-sync-dialog'),scope=dialog.locator('select[name="scope"]'),term=dialog.locator('select[name="term"]');
  const scopeTrigger=await triggerFor(scope),termTrigger=await triggerFor(term);await expect(scopeTrigger).toHaveAccessibleName('同步范围');await expect(termTrigger).toHaveAccessibleName('学期');
  await scopeTrigger.focus();await scopeTrigger.press('ArrowDown');await page.getByRole('option',{name:'发现教务当前学期',exact:true}).click();
  await expect(scope).toHaveValue('current');await expect(termTrigger).toBeDisabled();await expect(dialog.locator('input[name="year"]')).toBeDisabled();
  expect(await dialog.locator('form').evaluate(n=>Object.fromEntries(new FormData(n as HTMLFormElement)))).toEqual({scope:'current'});
  await choose(scope,'指定学年学期');await expect(termTrigger).toBeEnabled();await termTrigger.focus();await termTrigger.press('ArrowDown');await termTrigger.press('End');await termTrigger.press('Enter');
  await expect(term).toHaveValue('3');await expect(termTrigger).toContainText('夏季学期');
  await dialog.locator('form').evaluate(n=>(n as HTMLFormElement).reset());await expect(term).toHaveValue('1');await expect(termTrigger).toContainText('第一学期');
  await dialog.locator('input[name="year"]').fill('');expect(await dialog.locator('form').evaluate(n=>(n as HTMLFormElement).reportValidity())).toBe(false);await expect(dialog.locator('input[name="year"]')).toBeFocused();
  await termTrigger.click();expect(await page.locator('.lq-dropdown__popup').evaluate(n=>Boolean(n.closest('dialog[open]')))).toBe(true);await page.keyboard.press('Escape');await expect(dialog).toBeVisible();
  const submitted:any[]=[];await page.route('**/api/manage/academic/course-schedule/academic-sync',async route=>{submitted.push(route.request().postDataJSON());await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({status:'error',message:'合成验收：同步冲突，已有课表保留'})});});
  await dialog.locator('input[name="year"]').fill(s.year);await dialog.locator('button[type="submit"]').click();await expect.poll(()=>submitted.length).toBe(1);expect(submitted[0]).toEqual({year:s.year,term:'1'});await expect(dialog).toBeHidden();
  await expect(page.locator('.cs-course-card').first()).toBeVisible();await openSync(page);
  await dialog.locator('[data-sync-cancel]').click();await expect(dialog).toBeHidden();await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0);
  await info.attach('native-values',{body:JSON.stringify({scope:await scope.inputValue(),term:await term.inputValue(),submitted}),contentType:'application/json'});
});

test('real React task and dashboard filters preserve state across unmount without duplicate selection owners',async({page},info)=>{
  await loginTeacher(page,f);await page.goto(`/classroom/${f.classOfferingId}`);await page.locator('[data-cw-task-collection]').click();
  const status=page.locator('.cw-dialog .cw-filterbar select').first();await status.evaluate(n=>{n.dataset.qaChanges='0';n.addEventListener('change',()=>{n.dataset.qaChanges=String(Number(n.dataset.qaChanges)+1);});});await choose(status,'已关闭');await expect(status).toHaveValue('closed');await expect(status).toHaveAttribute('data-qa-changes','1');await expect(status).toHaveAttribute('aria-hidden','true');
  await page.locator('.cw-dialog .ui-dialog-close').click();await expect(page.locator('.cw-dialog')).toHaveCount(0);await page.locator('[data-cw-task-collection]').click();
  await expect(page.locator('.cw-dialog .cw-filterbar .lq-dropdown')).toHaveCount(2);for(const native of await page.locator('.cw-dialog .cw-filterbar select').all()){await expect(await triggerFor(native)).toBeVisible();await expect(native).toHaveAttribute('aria-hidden','true');}await page.locator('.cw-dialog .ui-dialog-close').click();
  await page.goto('/dashboard');await page.getByRole('button',{name:'全部事项与历史',exact:true}).click();
  const selectors=page.locator('.ls-dialog select');await expect(selectors).toHaveCount(4);for(let i=0;i<4;i++)await expect(await triggerFor(selectors.nth(i))).toBeVisible();
  const date=selectors.nth(2);await date.evaluate(n=>{n.dataset.qaChanges='0';n.addEventListener('change',()=>{n.dataset.qaChanges=String(Number(n.dataset.qaChanges)+1);});});
  await choose(date,'本周');await expect(date).toHaveValue('this_week');await expect(date).toHaveAttribute('data-qa-changes','1');await page.locator('.ls-dialog').getByRole('button',{name:'清除筛选',exact:true}).click();await expect(date).toHaveValue('');await expect(await triggerFor(date)).toContainText('全部日期');await expect(date).toHaveAttribute('data-qa-changes','1');
  await info.attach('react-native-events',{body:'{"taskChoiceChange":1,"dateChoiceChange":1,"programmaticResetExtraChanges":0}',contentType:'application/json'});
  await page.locator('.ls-dialog .ui-dialog-close').click();await expect(page.locator('.lq-dropdown__popup')).toHaveCount(0);
});

test('student filters retain the declared single owner when global dropdown enhancement runs',async({page},info)=>{
  await student(page);
  const owners:any[]=[];
  for(const selector of ['[data-student-schedule-term]','[data-student-course-term]','[data-student-course-state]']){
    const native=page.locator(selector);await expect(native).toHaveCount(1);
    const owner=await native.evaluate(async(n,url)=>({dropdown:Boolean((await import(url)).getDropdown(n)),selection:Boolean(n.ownerDocument[Symbol.for('lanshare.lq.selection-bindings.v1') as any]?.get(n)),declaredSelection:n.hasAttribute('data-lq-selection-owner')}),dropdown);
    expect(Number(owner.dropdown)+Number(owner.selection)).toBe(1);expect(owner.selection).toBe(owner.declaredSelection);owners.push({selector,...owner});
  }
  await info.attach('retained-Selection-owner',{body:JSON.stringify(owners),contentType:'application/json'});
});

for(const role of ['teacher','student'])test(`real ${role} 3D planned approved and pending cards navigate without inflating canonical hours`,async({page},info)=>{
  if(role==='student')await student(page);else{await loginTeacher(page,f);await page.goto('/manage/academic/course-schedule');}
  const data=await overview(page,role==='student');expect(data.planned_changes).toHaveLength(1);expect(data.approved_changes).toHaveLength(1);expect(data.summary.slot_count).toBe(s.officialCount);expect(data.summary.prediction_count).toBe(1);
  const plan=data.planned_changes[0];expect(plan.phase).toBe('planned');expect(plan.approval_status).toBe('approved');
  const canonical=data.weeks.flatMap((w:any)=>w.lessons);expect(canonical.some((l:any)=>l.is_change_plan)).toBe(false);
  await expanded(page,plan.original_week_index,role==='student');
  const source=page.locator('.cs-expand [data-event-key]').filter({has:page.getByRole('button',{name:/已批准.*新位置/})});await expect(source).toHaveCount(1);
  await source.locator('[data-csd-change]').click();await expect(page.locator('[data-csd-expand-title]')).toContainText(`第${plan.proposed_week_index}周`);
  const target=page.locator('.cs-expand [data-event-key*="'+':planned:'+plan.request_id+'"]').first();await expect(target).toBeVisible();await expect(target).toContainText('不计入正式课时');
  await page.locator('[data-csd-expand-title]').hover();await expect(page.locator('.cs-expand .is-preview,.cs-expand .is-preview-closing,.cs-expand .is-preview-moving')).toHaveCount(0);
  await page.waitForFunction(()=>document.getAnimations().filter(a=>a.playState==='running'&&a.effect?.getComputedTiming().iterations!==Infinity).every(a=>!((a.effect as KeyframeEffect)?.target as Element)?.closest('.cs-expand')));
  await page.screenshot({path:info.outputPath(`planned-${role}-target.png`)});await target.locator('[data-csd-change]').click();await expect(page.locator('[data-csd-expand-title]')).toContainText(`第${plan.original_week_index}周`);
  await page.locator('[data-csd-expand-close]').click();await expanded(page,data.approved_changes[0].original_week_index,role==='student');await expect(page.locator('.cs-expand .cs-lesson--history')).toHaveCount(1);
  const pending=canonical.find((l:any)=>l.adjustment?.phase==='pending'&&l.adjustment?.endpoint==='original');expect(pending).toBeTruthy();
  await page.locator('[data-csd-expand-close]').click();await expanded(page,s.changes.pending.details[0].original.week,role==='student');
  await page.locator(`.cs-expand [data-event-key="${pending.event_key}"] [data-csd-change]`).click();
  const pendingTarget=page.locator(`.cs-expand [data-event-key="${pending.adjustment.counterpart_event_key}"]`);await expect(pendingTarget).toBeVisible();await expect(pendingTarget.locator('[data-csd-change]')).not.toContainText('已批准');
  if(role==='student')expect((await page.request.get(`/api/manage/academic/course-schedule/editor?year=${s.year}&term=${s.term}`)).status()).toBe(403);
  await info.attach('canonical-schedule-contract',{body:JSON.stringify({summary:data.summary,planned:data.planned_changes,approved:data.approved_changes,canonicalCount:canonical.length}),contentType:'application/json'});
});

test('real editor keeps holiday makeup separate and planned endpoints cannot start edit requests',async({page},info)=>{
  await loginTeacher(page,f);await page.goto(`/manage/academic/course-schedule/editor?year=${s.year}&term=${s.term}`);
  const swapWeek=Math.floor((Date.parse('2026-10-10')-Date.parse(s.week1Monday))/604800000)+1;
  await page.locator(`[data-cse-week="${swapWeek}"]`).click();
  await expect(page.locator('[data-cse-calnote]')).toContainText('10/10');await expect(page.locator('[data-cse-calnote]')).toContainText('调休上课');
  await expect(page.locator('[data-cse-swap-dot="2026-10-10"]').first()).toBeVisible();
  await expect(page.locator('.cse-dayhead__tag--workday')).toContainText('补周三 10/7');
  const planSourceWeek=s.changes.planned.details[0].original.week;await page.locator(`[data-cse-week="${planSourceWeek}"]`).click();
  await expect(page.locator('[data-cse-request-jump]').first()).toBeVisible();
  await expect(page.getByRole('region',{name:'调课申请关系'})).toContainText('与节假日调休分开');
  const requestLinks=page.locator('[data-cse-request-jump]');const planLink=requestLinks.filter({hasText:s.changes.planned.details[0].proposed.date}).first();
  const calls:string[]=[];page.on('request',r=>{if(/availability|learning-materials|schedule.*save/.test(r.url()))calls.push(r.url());});
  await planLink.click();const target=page.locator('[data-cse-lesson*="'+':planned:'+s.changes.planned.request_id+'"]').first();await expect(target).toBeVisible();await target.press('Enter');
  expect(calls).toEqual([]);await expect(page.locator('[data-cse-drawer]')).toBeHidden();
  await page.screenshot({path:info.outputPath('editor-planned-readonly.png')});
  await info.attach('plan-readonly-requests',{body:JSON.stringify(calls),contentType:'application/json'});
});

for(const mode of ['off','reduced','opaque'])test(`real dropdown ${mode} keeps choices usable with bounded blur and no idle animation loop`,async({page},info)=>{
  await page.addInitScript(()=>{const w=window as any,raf=requestAnimationFrame.bind(window);w.__selectionFrames={scheduled:0,fired:0};w.__selectionNativeRAF=raf;window.requestAnimationFrame=callback=>{w.__selectionFrames.scheduled++;return raf(time=>{w.__selectionFrames.fired++;callback(time);});};});
  if(mode==='reduced')await page.emulateMedia({reducedMotion:'reduce'});
  await loginTeacher(page,f);await page.goto('/manage/academic/course-schedule');await page.waitForLoadState('networkidle');
  if(mode!=='reduced')await page.locator('html').evaluate((n,value)=>n.setAttribute(value==='off'?'data-lq-motion':'data-lq-glass','off'),mode);
  const select=page.locator('[data-cs-class]'),trigger=await triggerFor(select);await trigger.click();
  const popup=page.locator('.lq-dropdown__popup');await expect(popup).toBeVisible();
  if(mode==='opaque'){await expect(popup).toHaveCSS('backdrop-filter','none');expect(await popup.evaluate(n=>getComputedStyle(n).backgroundColor)).not.toMatch(/rgba\([^)]*,\s*0(?:\.|\))/);}
  else expect(await popup.evaluate(n=>n.getAnimations().filter(a=>a.playState==='running').length)).toBe(0);
  await page.waitForTimeout(400);const before=await page.evaluate(()=>(window as any).__selectionFrames);await page.waitForTimeout(400);const after=await page.evaluate(()=>(window as any).__selectionFrames);expect(after).toEqual(before);
  const blur=await page.evaluate(()=>[...document.querySelectorAll('*')].flatMap(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return r.width>1&&r.height>1&&r.right>0&&r.left<innerWidth&&r.bottom>0&&r.top<innerHeight&&s.visibility!=='hidden'&&s.display!=='none'&&s.backdropFilter!=='none'?[{tag:n.tagName,id:n.id,classes:n.className,filter:s.backdropFilter,leaf:n.matches('button,input,select,textarea,[role="option"]')}]:[];}));
  expect(blur.filter(n=>n.leaf)).toEqual([]);expect(blur.length).toBeLessThanOrEqual(mode==='opaque'?0:3);
  await page.getByRole('option',{name:s.longClassLabel,exact:true}).click();await expect(select).toHaveValue(s.longClassLabel);await expect(trigger).toContainText(s.longClassLabel);
  await info.attach('selection-mode-idle',{body:JSON.stringify({mode,before,after,blur}),contentType:'application/json'});
});

test('deferred declared controls activate once on first real pointer and profile identity remains named',async({page,isMobile},info)=>{
  test.setTimeout(180_000);
  await loginTeacher(page,f);
  const routes:[string,number][]=[
    [`/exam/${f.s3.authoringPaperId}/edit`,1],
    ['/manage/archive/attendance-reports',1],
    ...f.platformAudit.attendanceReportIds.map((id:number)=>[`/manage/archive/attendance-reports/${id}`,2] as [string,number]),
    ['/manage/teaching/offerings',1],
  ];
  const evidence:any[]=[];
  for(const [url,count] of routes){
    await page.goto(url);await page.waitForLoadState('networkidle');
    const indexes=await page.evaluate(async(moduleURL)=>{
      const {getDropdown}=await import(moduleURL);
      return [...document.querySelectorAll('select')].flatMap((n,i)=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return n.hasAttribute('data-lq-dropdown')&&!getDropdown(n)&&r.width>1&&r.height>1&&s.display!=='none'&&s.visibility!=='hidden'&&!n.closest('[hidden]')?[i]:[];});
    },dropdown);
    expect(indexes,url).toHaveLength(count);
    for(const index of indexes){
      const native=page.locator('select').nth(index);await expect(native).toBeEnabled();
      const before=await native.evaluate(n=>{const s=n as HTMLSelectElement;s.dataset.qaInput='0';s.dataset.qaChange='0';s.addEventListener('input',()=>s.dataset.qaInput=String(Number(s.dataset.qaInput)+1));s.addEventListener('change',()=>s.dataset.qaChange=String(Number(s.dataset.qaChange)+1));return{value:s.value,options:s.options.length,label:s.selectedOptions[0]?.textContent};});
      if(isMobile)await native.tap();else await native.click();
      await expect.poll(()=>native.evaluate(async(n,u)=>Boolean((await import(u)).getDropdown(n)),dropdown)).toBe(true);
      const binding=await native.evaluate(async(n,u)=>{const b=(await import(u)).getDropdown(n);return{triggerId:b.trigger.id,popupId:b.popup.id,selectionOwner:Boolean(n.ownerDocument[Symbol.for('lanshare.lq.selection-bindings.v1') as any]?.get(n))};},dropdown);
      expect(binding.selectionOwner).toBe(false);
      const trigger=page.locator(`[id="${binding.triggerId}"]`),popup=page.locator('.lq-dropdown__popup:visible');
      await expect(trigger).toHaveAttribute('aria-expanded','true');await expect(trigger).toHaveAccessibleName(/\S/);
      await expect(page.locator('.lq-dropdown__popup:visible')).toHaveCount(1);await expect(popup).toBeVisible();
      await expect(popup.locator('[role="option"]')).toHaveCount(before.options);
      expect(await popup.evaluate(n=>getComputedStyle(n).backdropFilter)).toContain('blur(');
      const box=await popup.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(-1);expect(box!.x+box!.width).toBeLessThanOrEqual(await page.evaluate(()=>innerWidth)+1);
      await page.keyboard.press('Escape');await expect(trigger).toHaveAttribute('aria-expanded','false');
      await expect(native).toHaveValue(before.value);await expect(native).toHaveAttribute('data-qa-input','0');await expect(native).toHaveAttribute('data-qa-change','0');
      evidence.push({url,index,before,after:binding,firstActivation:isMobile?'tap':'click',events:{input:0,change:0}});
    }
  }
  expect(evidence).toHaveLength(9);
  await page.goto('/profile?section=settings');await page.waitForLoadState('networkidle');
  await page.locator('[data-identity-add]').click();
  const identity=page.locator('select[data-identity-field="identity_category"]').first();
  await expect(identity).toHaveCount(1);const trigger=await triggerFor(identity);await expect(trigger).toHaveAccessibleName('任职身份');
  await trigger.click();await expect(page.locator('.lq-dropdown__popup:visible')).toHaveCount(1);await expect(page.getByRole('option').first()).toBeVisible();
  await page.screenshot({path:info.outputPath('profile-identity-options.png')});await page.keyboard.press('Escape');
  await info.attach('deferred-first-activation',{body:JSON.stringify({graph,evidence,profileIdentity:{accessibleName:'任职身份',singlePopup:true}}),contentType:'application/json'});
});
