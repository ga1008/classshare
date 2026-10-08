import { test,expect,type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { loginTeacher,expectSessionRole } from '../fixtures/p03';
import { armFrames,collectedFrames,clickFrames,expectFade,type MotionFrame } from '../fixtures/motion-frames';
const runtime=path.resolve(process.env.LQ_MOTION_RUNTIME||'.codex-temp/lq-motion-20261008-runtime');
const fixture=JSON.parse(fs.readFileSync(path.join(runtime,'fixture.json'),'utf8'));
if(!fixture.uiV3Synthetic||!fixture.lqS3Synthetic||!runtime.startsWith(path.resolve('.codex-temp')+path.sep))throw Error('Owned synthetic LQ fixture required');
const graph=JSON.parse(fs.readFileSync('static/assets/manifest.json','utf8')).revision;
const asset=(name:string)=>`/static/assets/${graph}/js/${name}.js`;
const pageErrors=new WeakMap<Page,string[]>();
async function loginStudent(page:Page,owned:any){
  await page.goto('/student/login');await page.waitForLoadState('networkidle');
  await page.locator('#identifier').fill(owned.student.studentNumber);
  await page.locator('#password').fill(owned.password);
  await expect(page.locator('#identifier')).toHaveValue(owned.student.studentNumber);
  await expect(page.locator('#password')).toHaveValue(owned.password);
  await Promise.all([page.waitForURL(/\/dashboard(?:\?|$)/),page.locator('#student-password-login-form button[type="submit"]').click()]);
  await page.waitForLoadState('networkidle');await expectSessionRole(page,'student');
}
test.beforeEach(async({page})=>{
  const errors:string[]=[];pageErrors.set(page,errors);page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await expect.poll(async()=>{try{return (await(await page.request.get('/api/internal/health')).json()).database_path;}catch{return null;}},{timeout:30000}).toBe(fixture.databasePath);
  await page.addInitScript(()=>{const w=window as any,native=requestAnimationFrame.bind(window);w.__motionNativeRAF=native;w.__motionRaf={scheduled:0,fired:0};window.requestAnimationFrame=callback=>{w.__motionRaf.scheduled++;return native(time=>{w.__motionRaf.fired++;callback(time);});};});
});
test.afterEach(async({page},info)=>{
  const revisions=await page.evaluate(()=>[...new Set(performance.getEntriesByType('resource').map(e=>e.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(revisions).toEqual([graph]);await info.attach('asset-graph',{body:JSON.stringify(revisions),contentType:'application/json'});
  await info.attach('render-context',{body:JSON.stringify(await page.evaluate(()=>({viewport:{width:innerWidth,height:innerHeight},appearance:document.documentElement.dataset.appearance,glass:document.documentElement.dataset.lqGlass,motion:document.documentElement.dataset.lqMotion,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,touch:matchMedia('(hover: none)').matches}))),contentType:'application/json'});
  const errors=pageErrors.get(page)||[];await info.attach('uncaught-browser-errors',{body:JSON.stringify(errors),contentType:'application/json'});expect(errors).toEqual([]);
});
async function evidence(info:any,frames:Record<string,MotionFrame[]>){await info.attach('rendered-motion-frames',{body:JSON.stringify(frames),contentType:'application/json'});}
async function blurHosts(page:Page){
  return page.evaluate(()=>[...document.querySelectorAll<HTMLElement>('*')].filter(node=>{
    if(!node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))return false;
    const box=node.getBoundingClientRect(),filter=getComputedStyle(node).backdropFilter;
    return filter!=='none'&&box.width>0&&box.height>0&&box.right>0&&box.bottom>0&&box.left<innerWidth&&box.top<innerHeight;
  }).map(node=>({tag:node.tagName,id:node.id,class:node.className,filter:getComputedStyle(node).backdropFilter})));
}

test('real component laboratory preserves menu and modal motion with glass disabled',async({page},info)=>{
  await loginTeacher(page,fixture);await page.goto('/dev/lq');
  await expect(page.locator('[data-lq-preview]')).toHaveAttribute('data-lq-interactions-ready','true');
  await page.locator('[data-lq-preview-option][name="glass"]').selectOption('off');
  await expect(page.locator('html')).toHaveAttribute('data-lq-glass','off');
  const menuIn=await clickFrames(page,page.locator('#preview-menu-trigger'),'#preview-menu');expectFade(menuIn,'laboratory menu enter');
  await armFrames(page,'#preview-menu','keydown');await page.keyboard.press('Escape');const menuOut=await collectedFrames(page);expectFade(menuOut,'laboratory menu exit');
  const dialogIn=await clickFrames(page,page.locator('[data-lq-demo-dialog="modal"]'),'.lq-dialog__surface');expectFade(dialogIn,'laboratory modal enter');
  await armFrames(page,'.lq-dialog__surface','keydown');await page.keyboard.press('Escape');const dialogOut=await collectedFrames(page);expectFade(dialogOut,'laboratory modal exit');
  await expect(page.locator('[data-lq-demo-dialog="modal"]')).toBeFocused();
  await evidence(info,{menuIn,menuOut,dialogIn,dialogOut});
});

test('real classroom menu course details and materials preserve both presence directions',async({page,isMobile},info)=>{
  await loginTeacher(page,fixture);await page.goto(`/classroom/${fixture.classOfferingId}`);
  const restingBlur=await blurHosts(page);expect(restingBlur.length,JSON.stringify(restingBlur)).toBeLessThanOrEqual(2);
  const menu=page.locator('#classroom-more-menu--lq-trigger');await expect(menu).toBeVisible();
  await armFrames(page,'#classroom-more-menu',isMobile?'click':'pointerover');
  if(isMobile)await menu.click();else await menu.hover();const menuIn=await collectedFrames(page);expectFade(menuIn,'classroom more menu enter');
  await armFrames(page,'#classroom-more-menu','keydown');await page.keyboard.press('Escape');const menuOut=await collectedFrames(page);expectFade(menuOut,'classroom more menu exit');
  const detailsIn=await clickFrames(page,page.locator('#hero-course-detail-btn'),'.course-popover-card');expectFade(detailsIn,'course details enter');
  const detailsBlur=await blurHosts(page);expect(detailsBlur.length,JSON.stringify(detailsBlur)).toBeLessThanOrEqual(3);
  await page.screenshot({path:info.outputPath('course-details-open.png')});
  const detailsOut=await clickFrames(page,page.locator('#course-popover-close'),'.course-popover-card');expectFade(detailsOut,'course details exit');
  await expect(page.locator('#hero-course-detail-btn')).toBeFocused();
  const materialsIn=await clickFrames(page,page.locator('[data-cw-open="materials"]'),'.cw-dialog');expectFade(materialsIn,'materials enter');
  const materialsBlur=await blurHosts(page);expect(materialsBlur.length,JSON.stringify(materialsBlur)).toBeLessThanOrEqual(3);
  await page.screenshot({path:info.outputPath('materials-open.png')});
  const materialsOut=await clickFrames(page,page.locator('.cw-dialog .ui-dialog-close'),'.cw-dialog');expectFade(materialsOut,'materials exit');
  await expect(page.locator('.cw-dialog')).toHaveCount(0);
  await evidence(info,{menuIn,menuOut,detailsIn,detailsOut,materialsIn,materialsOut});
  await info.attach('actual-blur-hosts',{body:JSON.stringify({restingBlur,detailsBlur,materialsBlur}),contentType:'application/json'});
});

test('real student dashboard dialogs and classroom tab drafts retain intermediate frames',async({page},info)=>{
  await loginStudent(page,fixture);
  const history=page.getByRole('button',{name:'全部事项与历史',exact:true});
  const historyIn=await clickFrames(page,history,'.ls-dialog');expectFade(historyIn,'dashboard history enter');
  const historyOut=await clickFrames(page,page.locator('.ls-dialog .ui-dialog-close'),'.ls-dialog');expectFade(historyOut,'dashboard history exit');
  await expect(history).toBeFocused();
  const add=page.locator('.ls-focus [data-agenda-add-todo]');
  const todoIn=await clickFrames(page,add,'.agenda-todo-modal__card');expectFade(todoIn,'todo enter');
  await page.locator('#agendaTodoForm [name="title"]').fill('退出过程仍保留的草稿');
  const todoOut=await clickFrames(page,page.locator('.agenda-todo-modal button[data-todo-close]').first(),'.agenda-todo-modal__card');expectFade(todoOut,'todo exit');
  await page.goto(`/classroom/${fixture.classOfferingId}`);await page.locator('#classroom-activity-tab-discussion').click();
  await page.locator('#chat-input').fill('活动切换保留原生输入');
  await page.evaluate(()=>{(window as any).__savedDraft=document.querySelector('#chat-input');});
  const tabOut=await clickFrames(page,page.locator('#classroom-activity-tab-polls'),'[data-classroom-activity-panel="discussion"]');expectFade(tabOut,'activity panel exit');
  await page.locator('#classroom-activity-tab-resources').click();await page.locator('#classroom-activity-tab-discussion').click();
  await expect(page.locator('#chat-input')).toHaveValue('活动切换保留原生输入');
  expect(await page.evaluate(()=>(window as any).__savedDraft===document.querySelector('#chat-input'))).toBe(true);
  await evidence(info,{historyIn,historyOut,todoIn,todoOut,tabOut});
});

test('real 3D timetable expands and contracts through rendered frames and stops scheduling at rest',async({page},info)=>{
  await loginStudent(page,fixture);
  const opener=page.locator('[data-csd-stage] .cs-card.is-active .cs-card__bar');await expect(opener).toBeVisible();
  const entering=await clickFrames(page,opener,'.cs-expand');expectFade(entering,'3D expanded schedule enter');
  await expect(page.locator('.cs-expand .cs-lesson--cell').first()).toBeVisible();
  await page.screenshot({path:info.outputPath('3d-open.png')});
  const leaving=await clickFrames(page,page.locator('[data-csd-expand-close]'),'.cs-expand');expectFade(leaving,'3D expanded schedule exit');
  await page.mouse.move(0,0);await page.waitForTimeout(450);
  const before=await page.evaluate(()=>(window as any).__motionRaf);await page.waitForTimeout(450);
  const after=await page.evaluate(()=>(window as any).__motionRaf);expect(after).toEqual(before);
  await info.attach('idle-raf',{body:JSON.stringify({before,after,windowMs:450}),contentType:'application/json'});
  await evidence(info,{entering,leaving});
});

test('real teacher evaluation and global search use shared domain presence',async({page},info)=>{
  await loginTeacher(page,fixture);
  if(!await page.locator('[data-group-mode="flat"]').isVisible())await page.locator('.ls-course-options > summary').click();
  await page.locator('[data-group-mode="flat"]').click();
  const evaluation=page.locator('[data-academic-evaluation-open]').first();
  const evaluationIn=await clickFrames(page,evaluation,'.academic-evaluation-modal__dialog');expectFade(evaluationIn,'evaluation enter');
  const evaluationOut=await clickFrames(page,page.locator('.academic-evaluation-modal__close'),'.academic-evaluation-modal__dialog');expectFade(evaluationOut,'evaluation exit');
  const search=page.locator('[data-global-search-open]').first();
  if(await search.count()){
    const searchIn=await clickFrames(page,search,'.global-search-panel');expectFade(searchIn,'global search enter');
    await page.locator('[data-global-search-input]').fill('合成课程');
    const searchOut=await clickFrames(page,page.locator('[data-global-search-close]'),'.global-search-panel');expectFade(searchOut,'global search exit');
    await evidence(info,{evaluationIn,evaluationOut,searchIn,searchOut});
  }else{
    // The documented keyboard command is the real alternate global entry.
    await armFrames(page,'.global-search-panel','keydown');await page.keyboard.press('/');const searchIn=await collectedFrames(page);expectFade(searchIn,'global search keyboard enter');
    const searchOut=await clickFrames(page,page.locator('[data-global-search-close]'),'.global-search-panel');expectFade(searchOut,'global search exit');
    await evidence(info,{evaluationIn,evaluationOut,searchIn,searchOut});
  }
});

test('real process forms tree selection and academic comparison keep contents through exits',async({page},info)=>{
  await loginTeacher(page,fixture);await page.goto('/manage/archive/assessment-plans');
  const processIn=await clickFrames(page,page.locator('[data-ap-create-blank]').first(),'.lp-modal');expectFade(processIn,'process form enter');
  await page.locator('.lp-modal input').first().fill('取消时不写入的合成标题');
  const processOut=await clickFrames(page,page.locator('.lp-modal__close'),'.lp-modal');expectFade(processOut,'process form exit');
  expect(processOut.filter(f=>f.visible&&f.opacity>0).every(f=>f.text.includes('创建'))).toBe(true);
  const treeIn=await clickFrames(page,page.locator('[data-ap-generate-open]').first(),'.tsf-modal');expectFade(treeIn,'tree form enter');
  const treeOut=await clickFrames(page,page.locator('[data-tsf-close]'),'.tsf-modal');expectFade(treeOut,'tree form exit');
  await page.goto('/manage/library/courses');
  const academicIn=await clickFrames(page,page.locator('#heroCourseAcademicSyncBtn'),'.academic-sync-dialog');expectFade(academicIn,'academic comparison enter');
  await page.route('**/api/manage/academic-sync/preview',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({requires_confirmation:true,items:[{key:'qa-course',entity_type:'course',entity_label:'课程',title:'合成课程',status:'conflict',allowed_actions:['merge','skip'],default_action:'merge',requires_confirmation:true,fields:[{name:'name',label:'课程名称',local:'本地合成课程',remote:'教务合成课程',requires_choice:true}]}]})}));
  await page.locator('[data-academic-sync-confirm]').click();
  await page.locator('[data-academic-sync-field-row]').first().focus();await armFrames(page,'.academic-sync-detail-dialog','keydown');await page.keyboard.press('Enter');const detailIn=await collectedFrames(page);expectFade(detailIn,'academic detail enter');
  const detailOut=await clickFrames(page,page.locator('[data-academic-sync-detail-close]'),'.academic-sync-detail-dialog');expectFade(detailOut,'academic detail exit');
  await expect(page.locator('[data-academic-sync-dialog]')).toBeVisible();
  const academicOut=await clickFrames(page,page.locator('[data-academic-sync-close]'),'.academic-sync-dialog');expectFade(academicOut,'academic comparison exit');
  await evidence(info,{processIn,processOut,treeIn,treeOut,academicIn,academicOut,detailIn,detailOut});
});

test('real feedback and nested class student drawer retain drafts through shared exits',async({page},info)=>{
  await loginTeacher(page,fixture);await page.goto(`/classroom/${fixture.classOfferingId}`);
  await page.locator('#classroom-personal-menu--lq-trigger').click();
  const feedbackIn=await clickFrames(page,page.locator('[data-open-feedback]:visible').first(),'#feedback-modal > .modal');expectFade(feedbackIn,'feedback enter');
  await page.locator('#feedback-title').fill('取消后仍保留的合成反馈');
  const feedbackOut=await clickFrames(page,page.locator('[data-feedback-dismiss]'),'#feedback-modal > .modal');expectFade(feedbackOut,'feedback exit');
  await expect(page.locator('#feedback-title')).toHaveValue('取消后仍保留的合成反馈');
  await page.goto('/manage/teaching/classes');
  const drawerIn=await clickFrames(page,page.locator('[data-action="open-students"]').first(),'.class-student-drawer');expectFade(drawerIn,'class students drawer enter');
  const childIn=await clickFrames(page,page.locator('#classStudentAddBtn'),'#classStudentAddModal > section');expectFade(childIn,'class student child enter');
  await page.locator('#classStudentAddName').fill('不提交的合成学生');
  const childOut=await clickFrames(page,page.locator('#classStudentAddCancel'),'#classStudentAddModal > section');expectFade(childOut,'class student child exit');
  await expect(page.locator('#classStudentDrawer')).toBeVisible();await expect(page.locator('#classStudentAddBtn')).toBeFocused();
  const drawerOut=await clickFrames(page,page.locator('#classStudentDrawerClose'),'.class-student-drawer');expectFade(drawerOut,'class students drawer exit');
  await evidence(info,{feedbackIn,feedbackOut,drawerIn,drawerOut,childIn,childOut});
});

test('real classroom onboarding animates steps and nested editor while dismissal failures veto close',async({page},info)=>{
  await loginTeacher(page,fixture);
  const entering=await clickFrames(page,page.locator('[data-teacher-onboarding-open]').first(),'.teacher-onboarding-dialog');expectFade(entering,'classroom onboarding enter');
  const childIn=await clickFrames(page,page.locator('[data-action="create-semester"]'),'.teacher-onboarding-submodal-card');expectFade(childIn,'onboarding nested semester enter');
  await page.locator('[data-submodal-body] input[name="name"]').fill('不保存的合成学期');
  const childOut=await clickFrames(page,page.locator('[data-submodal-close]'),'.teacher-onboarding-submodal-card');expectFade(childOut,'onboarding nested semester exit');
  await expect(page.locator('[data-action="create-semester"]')).toBeFocused();
  await page.locator('[data-onboarding-content] [data-select-id]').first().click();
  await expect(page.locator('[data-onboarding-next]')).toBeEnabled();
  const step=await clickFrames(page,page.locator('[data-onboarding-next]'),'[data-onboarding-content] .onboarding-step-shell');expectFade(step,'onboarding step transition');
  await page.locator('#onboardingCourseNameInput').fill('未完成的新课程草稿');
  await page.route('**/api/manage/teacher-onboarding/dismiss',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'合成保存失败'})}));
  await page.locator('[data-teacher-onboarding-dismiss]').first().click();await expect(page.getByText('合成保存失败',{exact:true})).toBeVisible();
  await expect(page.locator('.teacher-onboarding-dialog')).toBeVisible();await expect(page.locator('#onboardingCourseNameInput')).toHaveValue('未完成的新课程草稿');
  await page.getByRole('button',{name:'关闭通知',exact:true}).click();await expect(page.getByText('合成保存失败',{exact:true})).toHaveCount(0);
  await page.unroute('**/api/manage/teacher-onboarding/dismiss');
  const leaving=await clickFrames(page,page.locator('[data-teacher-onboarding-dismiss]').first(),'.teacher-onboarding-dialog');expectFade(leaving,'classroom onboarding exit');
  await expect(page.locator('[data-teacher-onboarding-modal]')).toBeHidden();
  await evidence(info,{entering,childIn,childOut,step,leaving});
});

test('real signature handwriting pixels remain until the shared exit finishes',async({page,isMobile},info)=>{
  await loginTeacher(page,fixture);await page.goto('/manage/me/signatures');
  await page.locator('#signature-open-upload-btn').click();
  const entering=await clickFrames(page,page.locator('#signature-open-pad-btn'),'[role="dialog"][aria-label="手写签名"]');expectFade(entering,'signature pad enter');
  const canvas=page.locator('[data-pad-canvas]'),box=(await canvas.boundingBox())!;
  if(isMobile){const cdp=await page.context().newCDPSession(page);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:box.x+30,y:box.y+100}]});for(let i=1;i<=10;i++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:box.x+30+i*10,y:box.y+100+i*3}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await cdp.detach();}
  else{await page.mouse.move(box.x+30,box.y+100);await page.mouse.down();await page.mouse.move(box.x+180,box.y+130,{steps:12});await page.mouse.up();}
  await expect(page.locator('[data-pad-confirm]')).toBeEnabled();
  await page.evaluate(()=>{
    const c=document.querySelector<HTMLCanvasElement>('[data-pad-canvas]')!,s=c.closest('[role="dialog"]')!,raf=(window as any).__motionNativeRAF;
    (window as any).__canvasFrames=new Promise(resolve=>{const frames:any[]=[],started=performance.now();const sample=()=>{if(c.isConnected){const data=c.getContext('2d')!.getImageData(0,0,c.width,c.height).data;let dark=0;for(let i=0;i<data.length;i+=16)if(data[i]<100&&data[i+3]>0)dark++;frames.push({time:performance.now()-started,opacity:Number(getComputedStyle(s).opacity),dark});}if(performance.now()-started<650)raf(sample);else resolve(frames);};document.querySelector('[data-pad-cancel]')!.addEventListener('click',sample,{capture:true,once:true});});
  });
  const leaving=await clickFrames(page,page.locator('[data-pad-cancel]'),'[role="dialog"][aria-label="手写签名"]');expectFade(leaving,'signature pad exit');
  const pixels=await page.evaluate(()=>(window as any).__canvasFrames);expect(pixels.some((f:any)=>f.opacity>.03&&f.opacity<.97)).toBe(true);expect(pixels.every((f:any)=>f.dark>0)).toBe(true);
  await expect(canvas).toHaveCount(0);await expect(page.locator('#signature-upload-modal')).toBeVisible();
  await info.attach('signature-content-process',{body:JSON.stringify({entering,leaving,pixels}),contentType:'application/json'});
});

test('real AI workspace modes and history keep drafts and reject late focus after reversal',async({page},info)=>{
  await loginTeacher(page,fixture);
  await expect(page.locator('#ai-chat-fab')).toBeEnabled();
  const entering=await clickFrames(page,page.locator('#ai-chat-fab'),'.ai-workspace-container');expectFade(entering,'AI workspace enter');
  await page.screenshot({path:info.outputPath('AI-workspace-open.png')});
  const draft=page.locator('#ai-chat-textarea');await draft.fill('模式切换和快速关闭仍保留的草稿');
  await page.evaluate(()=>{(window as any).__aiDraft=document.querySelector('#ai-chat-textarea');});
  const modeIn=await clickFrames(page,page.locator('[data-ai-mode-select="agent"]'),'[data-ai-workspace-panel="agent"]');expectFade(modeIn,'AI agent panel enter');
  await page.locator('[data-ai-mode-select="chat"]').click();await expect(draft).toHaveValue('模式切换和快速关闭仍保留的草稿');
  const release: {run?:()=>void}={};
  await page.route('**/api/ai/workspace/sessions',async route=>{
    await new Promise<void>(resolve=>{release.run=resolve;});
    await route.fulfill({contentType:'application/json',body:JSON.stringify({sessions:[{session_uuid:'synthetic-delayed',title:'退出后到达的合成对话'}],legacy_sessions:[]})});
  });
  const historyIn=await clickFrames(page,page.locator('#ai-chat-history-toggle'),'.ai-conversation-history');expectFade(historyIn,'AI history enter');
  const historyOut=await clickFrames(page,page.getByRole('button',{name:'收起对话历史',exact:true}),'.ai-conversation-history');expectFade(historyOut,'AI history exit');
  await draft.focus();release.run?.();await page.waitForTimeout(100);
  await expect(draft).toBeFocused();await expect(page.locator('.ai-conversation-history')).toBeHidden();
  expect(await page.locator('.ai-conversation-history').textContent()).not.toContain('退出后到达的合成对话');
  const reversal=await page.evaluate(async()=>{
    const manager=(window as any).aiChat.windowManager, surface=document.querySelector('.ai-workspace-container')!;
    manager.close();await new Promise(resolve=>(window as any).__motionNativeRAF(resolve));
    const before=Number(getComputedStyle(surface).opacity);manager.open({focus:false});
    return {before,after:Number(getComputedStyle(surface).opacity)};
  });
  expect(Math.abs(reversal.before-reversal.after)).toBeLessThan(.08);
  await expect(page.locator('.ai-workspace-container')).toHaveCSS('opacity','1');
  await expect(draft).toHaveValue('模式切换和快速关闭仍保留的草稿');
  expect(await page.evaluate(()=>(window as any).__aiDraft===document.querySelector('#ai-chat-textarea'))).toBe(true);
  const leaving=await clickFrames(page,page.locator('#ai-chat-btn-close'),'.ai-workspace-container');expectFade(leaving,'AI workspace exit');
  await expect(page.locator('#ai-chat-modal')).toBeHidden();await expect(page.locator('#ai-chat-fab')).toBeVisible();
  await evidence(info,{entering,modeIn,historyIn,historyOut,leaving});await info.attach('AI-reversal',{body:JSON.stringify(reversal),contentType:'application/json'});
});

test('real same-origin document navigation exposes native snapshot intermediate frames',async({page},info)=>{
  await page.addInitScript(()=>{
    window.addEventListener('pagereveal',async(event:any)=>{
      const transition=event.viewTransition, frames:any[]=[];
      if(!transition){sessionStorage.setItem('__documentMotion',JSON.stringify({supported:'onpagereveal' in window,transition:false,frames}));return;}
      try{await transition.ready;}catch{return;}
      const started=performance.now(),raf=(window as any).__motionNativeRAF||requestAnimationFrame.bind(window);
      const sample=()=>{
        const old=getComputedStyle(document.documentElement,'::view-transition-old(root)'),next=getComputedStyle(document.documentElement,'::view-transition-new(root)');
        frames.push({time:performance.now()-started,old:Number(old.opacity),next:Number(next.opacity)});
        sessionStorage.setItem('__documentMotion',JSON.stringify({supported:true,transition:true,frames}));
        if(performance.now()-started<450)raf(sample);
      };sample();
    });
  });
  await loginTeacher(page,fixture);await page.evaluate(()=>sessionStorage.removeItem('__documentMotion'));
  const link=page.locator(`a[href="/classroom/${fixture.classOfferingId}"]`).first();await expect(link).toBeVisible();await link.click();
  await expect(page).toHaveURL(new RegExp(`/classroom/${fixture.classOfferingId}$`));
  await expect.poll(()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('__documentMotion')||'null'))).not.toBeNull();
  await page.waitForTimeout(500);
  const record=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('__documentMotion')!));
  if(record.supported){expect(record.transition).toBe(true);expect(record.frames.some((f:any)=>f.next>.03&&f.next<.97&&f.old>.03&&f.old<.97)).toBe(true);}
  await expect(page.locator('#hero-course-detail-btn')).toBeVisible();
  await info.attach('native-document-motion',{body:JSON.stringify(record),contentType:'application/json'});
});

for(const mode of ['off','reduced'])test(`real ${mode} keeps classroom dialogs usable without visible partial frames`,async({page},info)=>{
  if(mode==='reduced')await page.emulateMedia({reducedMotion:'reduce'});
  await loginTeacher(page,fixture);await page.goto(`/classroom/${fixture.classOfferingId}`);
  if(mode==='off')await page.locator('html').evaluate(n=>n.setAttribute('data-lq-motion','off'));
  const entering=await clickFrames(page,page.locator('#hero-course-detail-btn'),'.course-popover-card');
  expect(entering.filter(f=>f.visible).every(f=>f.opacity===1)).toBe(true);
  const leaving=await clickFrames(page,page.locator('#course-popover-close'),'.course-popover-card');
  expect(leaving.filter(f=>f.visible).every(f=>f.opacity===1||f.opacity===0)).toBe(true);
  await expect(page.locator('#course-info-popover')).toBeHidden();await expect(page.locator('#hero-course-detail-btn')).toBeFocused();
  await evidence(info,{entering,leaving});
});
