import { test, expect, Page, Route } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const origin = 'https://attendance-ui.test';
const builtManifest = process.env.ATTENDANCE_PRODUCTION_ASSETS === '1' ? JSON.parse(fs.readFileSync('static/assets/manifest.json','utf8')) : null;
const asset = (name:string) => '/static/' + (builtManifest ? builtManifest.entries[name] : name);
const session = { id: 12, title: '合成课堂第 3 次课', session_date: '2026-11-09', academic_section_text: '1–2 节' };
function record(id: number, measured: number | null = null) {
  return { id: id + 1000, student_id: id, student_number: `S${String(id).padStart(3, '0')}`, name: `合成同学${id}`, status: 'PENDING', status_label: '待确认', revision: 0, expected: true,
    first_seen_ms: 1794182400000, reason: '待核对', evidence: { login: { successful_count: 1, distinct_ip_count: 1 }, classroom_access: { page_view_count: 1, operation_record_count: 2 }, learning: { measured_seconds: measured }, collection: { status: measured === 0 ? 'observed_zero' : 'no_observation' } } };
}
function round(count = 200) { return { id: 9, session_id: 12, class_offering_id: 2, state: 'running', revision: 1, is_current: true, ends_at_ms: 1794187800000,
  context: { year: '2026-2027', term: 1 }, records: Array.from({ length: count }, (_, i) => record(i + 1, i === 0 ? 0 : null)), summary: { expected: count, exempt: 0, counts: { PENDING: count } }, policy: { revision: 0, networks: ['192.0.2.0/24'] } }; }
type APIHandler = (route: Route, pathname: string, body: any) => Promise<any>;
async function fixture(page: Page, handler: APIHandler, role = 'teacher', extra = '', markup?: string) {
  const errors: string[] = []; page.on('pageerror', error => { errors.push(error.message); console.error(error.stack); });
  await page.route(`${origin}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/api/')) return handler(route, pathname, route.request().postData() ? route.request().postDataJSON() : null);
    if (pathname.startsWith('/static/')) {
      const target = path.resolve('.' + pathname);
      if (!target.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(target)) return route.fulfill({ status: 404 });
      return route.fulfill({ body: fs.readFileSync(target), contentType: pathname.endsWith('.css') ? 'text/css' : pathname.endsWith('.js') ? 'text/javascript' : 'application/octet-stream' });
    }
    return route.fulfill({ contentType: 'text/html; charset=utf-8', body: markup || `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"></head><body><button id="entry">进入课次</button><script type="module">
      import {openDialog} from '${asset('js/lq/dialogs.js')}'; import {openLoginAttendance} from '${asset('js/login_attendance.js')}';
      document.querySelector('#entry').onclick=()=>{const b=document.createElement('button'); b.textContent='${role === 'teacher' ? '自动点名' : '我的签到'}';b.id='attendance';const parent=openDialog({title:'课次详情',body:b},{trigger:document.querySelector('#entry')});b.onclick=()=>{window.attendance=openLoginAttendance({classOfferingId:2,session:${JSON.stringify(session)},userRole:'${role}',trigger:b,${extra}});};window.parentLayer=parent;};window.ready=true;</script></body></html>` });
  });
  await page.goto(origin); await page.waitForFunction(() => (window as any).ready);
  return errors;
}
async function open(page: Page, role = 'teacher') { await page.getByRole('button', { name: '进入课次', exact: true }).click(); await page.getByRole('button', { name: role === 'teacher' ? '自动点名' : '我的签到', exact: true }).click(); }
const json = (route: Route, value: any, status = 200) => route.fulfill({ status, json: value });
async function choose(page:Page,dialog:any,label:string,option:string){const control=dialog.getByRole('combobox',{name:label,exact:true});if(await control.evaluate((n:Element)=>n.tagName==='SELECT'))await control.selectOption({label:option});else{await control.click();await page.getByRole('option',{name:option,exact:true}).click();}}

test('LQ attendance 200-person roster, six-state batch conflict, details, bounded cards and nested draft focus', async ({ page }) => {
  let current = round(), patches: any[] = [];
  const errors = await fixture(page, async (route, url, body) => {
    if (url.endsWith('/sessions/12')) return json(route, { session, policy: current.policy, teacher_network: { ip: '192.0.2.1' }, round: current });
    if (url.includes('/students/')) { const id = Number(url.split('/').at(-1)); return json(route, { record: current.records[id - 1], history: [] }); }
    if (url.endsWith('/records')) { patches.push(body); current.records[0].revision++; return json(route, { results: body.records.map((r: any, i: number) => ({ student_id: r.student_id, result: i === 0 ? 'conflict' : 'updated' })), round: current }); }
    return json(route, { detail: 'unexpected ' + url }, 500);
  });
  await open(page); const dialog = page.getByRole('dialog', { name: '自动点名', exact: true });
  await expect(dialog.locator('[data-student-id]')).toHaveCount(40);
  await expect(dialog.getByText('200 人 · 第 1 / 5 页')).toBeVisible();
  await dialog.getByRole('button', { name: '查看详情', exact: true }).first().click();
  await expect(dialog.getByText('0 分 0 秒', { exact: false })).toBeVisible();
  await expect(dialog.getByText('可靠记录为零', { exact: false })).toBeVisible();
  await dialog.getByRole('button', { name: '全班', exact: true }).click();
  await expect(dialog.locator('.la-selection-count')).toHaveText('已选择 200 人');
  expect(await dialog.locator('select').last().locator('option').count()).toBe(6);
  await dialog.getByLabel('修改理由').fill('合成核验：教师现场确认');
  await dialog.getByRole('button', { name: '应用到已选学生' }).click();
  await expect(dialog.getByRole('alert')).toContainText('1 人的版本发生冲突');
  expect(patches[0].records).toHaveLength(200); expect(patches[0].records[0].revision).toBe(0);
  await expect(dialog.locator('.la-selection-count')).toHaveText('已选择 1 人');
  await expect(dialog.getByLabel('修改理由')).toHaveValue('合成核验：教师现场确认');
  await page.keyboard.press('Escape');
  const confirm = page.getByRole('dialog', { name: '关闭并放弃未提交内容？' }); await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: '取消', exact: true }).click(); await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape'); await page.getByRole('dialog', { name: '关闭并放弃未提交内容？' }).getByRole('button', { name: '关闭', exact: true }).last().click();
  await expect(dialog).not.toBeVisible(); await expect(page.getByRole('button', { name: '自动点名', exact: true })).toBeFocused();
  expect(errors).toEqual([]);
});

test('LQ student only reads self, preserves failed appeal, and shows pending state after accepted submission', async ({ page }) => {
  const urls: string[] = [], reasons: string[] = []; let attempts = 0, submitted = false;
  const errors = await fixture(page, async (route, url, body) => {
    urls.push(url);
    if (url.endsWith('/sessions/12/me')) return json(route, { ...record(4, null), round: { id: 9, state: 'published', ends_at_ms: 1794187800000 }, status: 'PENDING', message: '签到待核对', participation: { measured_seconds: null }, can_appeal: !submitted, appeal: submitted ? { id: 70, status: 'pending' } : null });
    if (url.endsWith('/appeals')) { reasons.push(body.reason); if (++attempts === 1) return json(route, { detail: '合成暂时故障' }, 503); submitted = true; return json(route, { request: { id: 70 } }); }
    return json(route, { detail: 'teacher route forbidden' }, 403);
  }, 'student');
  await open(page, 'student'); const dialog = page.getByRole('dialog', { name: '我的签到', exact: true });
  await expect(dialog.getByText('无法完整观察', { exact: false })).toBeVisible();
  await expect(dialog.locator('[data-student-id]')).toHaveCount(0);
  await dialog.getByLabel('申诉说明').fill('我在教室完成了实践，请老师核对');
  await dialog.getByRole('button', { name: '提交签到申诉' }).click();
  await expect(dialog.getByRole('alert')).toContainText('合成暂时故障');
  await expect(dialog.getByLabel('申诉说明')).toHaveValue('我在教室完成了实践，请老师核对');
  await dialog.getByRole('button', { name: '提交签到申诉' }).click();
  await expect(dialog.getByText('签到申诉正在处理中。')).toBeVisible(); await expect(dialog.getByRole('button', { name: '提交签到申诉' })).toBeDisabled();
  expect(reasons).toEqual(['我在教室完成了实践，请老师核对', '我在教室完成了实践，请老师核对']); expect(urls.every(url => url.endsWith('/me') || url.endsWith('/appeals'))).toBeTruthy(); expect(errors).toEqual([]);
});

test('LQ exact notification rejects a mismatched round without falling back', async ({ page }) => {
  const errors = await fixture(page, async (route, url) => json(route, { ...round(), session_id: 99 }), 'teacher', 'attendanceId:9,studentId:4');
  await open(page); await expect(page.getByRole('alert')).toContainText('指定点名不属于本课次'); expect(errors).toEqual([]);
});

test('LQ school handoff gates writes, uses per-preview retry keys and limits confirmation scope', async ({ page }) => {
  const requests: Array<{url:string,body:any}> = []; const current = {...round(2),state:'published'}; let previewNo = 10, enqueueCount = 0;
  const run = (id = previewNo) => ({ id, binding_id: 3, revision: 1, state: 'preview', operations: [{ student_number: 'S001', target_status: 'PRESENT', remote_status: 'UNCHECKED', state: 'needs_teacher_action', reason: '请先在官方端更正' }] });
  const errors = await fixture(page, async (route, url, body) => {
    requests.push({url,body});
    if (url.endsWith('/sessions/12')) return json(route, { session, policy: current.policy, round: current });
    if (url.endsWith('/capabilities')) return json(route, { read_sources: true, read_checkins: true, readback: true, start: false, end: false, update_student: false, message: '官方端交接与回读' });
    if (url.endsWith('/source-options')) return json(route, { items: [{ course_name:'合成课程', course_code:'T001', teaching_class_name:'合成班', source_token:'synthetic-token' }] });
    if (url.endsWith('/checkins')) return json(route, { items: [{ id:99, createTime:'2026-11-09 08:00', section:'1–2' }] });
    if (url.endsWith('/bindings')) return json(route, body ? { id:3, revision:0 } : {items:[]});
    if (url.endsWith('/preview')) return json(route, run(++previewNo));
    if (url.endsWith('/refresh')) return json(route, {...run(),state:'confirmed',message:'学生状态已回读一致',operations:[]});
    if (url.endsWith('/runs') && body) return ++enqueueCount === 1 ? json(route,{detail:'合成暂时故障'},503) : json(route, {id:previewNo,state:'pending'},202);
    if (url.endsWith('/runs')) return json(route, {items:[]});
    return json(route,{detail:'unexpected '+url},500);
  });
  await open(page); await page.getByRole('button', {name:'智慧课堂核对',exact:true}).click(); const dialog=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});
  await expect(dialog.getByText('学校改签：未开放')).toBeVisible();
  await dialog.getByRole('button',{name:'读取学校授课班'}).click(); await choose(page,dialog,'学校授课班','合成课程 · T001 · 合成班');
  await dialog.getByRole('button',{name:'读取该班已有点名'}).click(); await choose(page,dialog,'学校已存在的点名','2026-11-09 08:00 · 1–2 · 点名 99');
  await dialog.getByRole('button',{name:'绑定并生成差异预览'}).click(); await expect(dialog.getByText('请先在官方端更正',{exact:true})).toBeVisible();
  await dialog.getByRole('button',{name:'后台逐人回读核对'}).click(); await expect(dialog.getByRole('alert')).toContainText('合成暂时故障');
  await dialog.getByRole('button',{name:'后台逐人回读核对'}).click(); await expect(dialog.getByText('学生状态已回读一致')).toBeVisible();
  const sent=requests.filter(x=>x.url.endsWith('/runs')&&x.body); expect(sent[0].body.idempotency_key).toBe(sent[1].body.idempotency_key);
  await expect(dialog.getByText('这里只核对所选点名', {exact:false})).toBeVisible();
  await dialog.getByLabel('学年',{exact:true}).fill('2027-2028'); await dialog.getByLabel('学年',{exact:true}).dispatchEvent('change');
  await expect(dialog.getByRole('button',{name:'后台逐人回读核对'})).toBeDisabled();
  expect(requests.some(x=>/\/(start|end|update_student)$/.test(x.url))).toBeFalsy(); expect(errors).toEqual([]);
});

test('LQ repeated attendance open and close removes child roots and does not accumulate polls', async ({page})=>{
  let gets=0;const current=round(2);const errors=await fixture(page,async(route,url)=>{gets++;return json(route,{session,policy:current.policy,round:current});});
  await page.getByRole('button',{name:'进入课次',exact:true}).click();
  for(let i=0;i<20;i++) { await page.getByRole('button',{name:'自动点名',exact:true}).click(); const d=page.getByRole('dialog',{name:'自动点名',exact:true}); await expect(d.locator('[data-student-id]')).toHaveCount(2); await page.keyboard.press('Escape'); await expect(d).not.toBeVisible(); }
  expect(gets).toBe(20); await expect(page.locator('.lq-dialog-root')).toHaveCount(1); expect(errors).toEqual([]);
});

test('LQ attendance narrow viewport remains scrollable without horizontal overflow', async ({ page }) => {
  await page.setViewportSize({width:390,height:844}); const current=round(200);
  const errors=await fixture(page, async(route,url)=>json(route,{session,policy:current.policy,round:current}));
  await open(page); const dialog=page.getByRole('dialog',{name:'自动点名',exact:true}); await expect(dialog.locator('[data-student-id]')).toHaveCount(40);
  expect(await dialog.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBeTruthy();
  await page.screenshot({path:'.codex-temp/login-attendance-mobile.png',fullPage:true}); expect(errors).toEqual([]);
});

test('LQ historical integer-zero round is read-only and teacher appeal drafts survive detail switching and refresh', async ({ page }) => {
  const current = { ...round(2), is_current: 0 }; const errors = await fixture(page, async (route, url) => {
    if (url.endsWith('/sessions/12')) return json(route,{session,policy:current.policy,round:current});
    if (url.includes('/students/')) return json(route,{record:current.records[Number(url.split('/').at(-1))-1],history:[],pending_appeal:{id:70,status:'pending'}});
    if (url.endsWith('/api/approvals/70')) return json(route,{request:{id:70,status:'pending',can_decide:true,reason:'请核对合成签到'}});
    return json(route,{detail:'unexpected'},500);
  });
  await open(page); const dialog=page.getByRole('dialog',{name:'自动点名',exact:true}); await expect(dialog.locator('[data-student-id]')).toHaveCount(2);
  await expect(dialog.getByRole('button',{name:'结束本地点名',exact:true})).toHaveCount(0); await expect(dialog.getByRole('button',{name:'重新核算',exact:true})).toHaveCount(0);
  await dialog.getByRole('button',{name:'全班',exact:true}).click(); await expect(dialog.getByRole('button',{name:'应用到已选学生'})).toBeDisabled();
  current.is_current=1; await dialog.getByRole('button',{name:'刷新记录',exact:true}).click();
  await dialog.getByRole('button',{name:'查看详情',exact:true}).first().click(); await dialog.getByLabel('处理说明').fill('合成处理草稿，尚未提交');
  await dialog.getByRole('button',{name:'查看详情',exact:true}).last().click(); await expect(dialog.getByLabel('处理说明')).toHaveValue('合成处理草稿，尚未提交');
  await dialog.getByRole('button',{name:'刷新记录',exact:true}).click(); await expect(dialog.getByLabel('处理说明')).toHaveValue('合成处理草稿，尚未提交'); expect(errors).toEqual([]);
});

test('LQ teacher durable attention has one leased popup across tabs and separates email delivery from acknowledgement', async ({ page, context }) => {
  const row={request_id:70,student_id:4,name:'合成同学4',student_number:'S004',round_id:9,class_offering_id:2,session_id:12,email_delivery:{email_status:'queued'}};
  let owner='',acks=0; const urls:string[]=[];
  const api:APIHandler=async(route,url,body)=>{urls.push(url); if(url.endsWith('/attention')) return json(route,{items:acks?[]:[row]}); if(url.endsWith('/claim')){const claimed=!owner||owner===body.owner;if(claimed)owner=body.owner;return json(route,{claimed,item:row});} if(url.endsWith('/ack')){acks++;return json(route,{acknowledged:true});} return json(route,{detail:'unexpected'},500);};
  const errors=await fixture(page,api); const second=await context.newPage(); const errors2=await fixture(second,api);
  for(const target of [page,second]) await target.evaluate(async(url)=>{const m=await import(url);(window as any).disposeAttention=m.initAttendanceAttention({navigate:(url:string)=>(window as any).navigated=url});},asset('js/login_attendance_attention.js'));
  await expect.poll(async()=>await page.getByRole('dialog',{name:'待处理签到申诉'}).count()+await second.getByRole('dialog',{name:'待处理签到申诉'}).count()).toBe(1);
  const winner=await page.getByRole('dialog',{name:'待处理签到申诉'}).count()?page:second; const dialog=winner.getByRole('dialog',{name:'待处理签到申诉'});
  await expect(dialog.getByText('通知邮件等待发送；站内提醒已生效。')).toBeVisible(); expect(acks).toBe(0);
  await winner.keyboard.press('Escape'); await expect(dialog).toBeVisible();
  await dialog.getByRole('button',{name:'查看并处理'}).click(); await expect.poll(()=>winner.evaluate(()=>(window as any).navigated)).toBe('/classroom/2?session_id=12&attendance_id=9&student_id=4&appeal_id=70'); expect(acks).toBe(1);
  for(const target of [page,second]) await target.evaluate(()=>(window as any).disposeAttention()); await second.close(); expect([...errors,...errors2]).toEqual([]);
});

test('LQ real classroom controller delivers an exact attendance deep link into its owned lesson layer',async({page})=>{
  const current=round(2), urls:string[]=[]; const lesson={...session,order_index:1,is_anchor:true};
  const markup=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"></head><body>
  <div id="teaching-plan-widget"><div id="teachingTimelineScroll"><button data-session-order="1">合成课次</button></div></div>
  <div id="teachingSessionModal" hidden><section class="teaching-session-modal"><h2 id="teachingSessionModalTitle">课次详情</h2><button id="teachingSessionModalClose">关闭</button><div class="teaching-session-modal-body"><button id="teachingSessionKnowledgeTreeBtn">知识树</button><button id="teachingSessionAutoAttendanceBtn" hidden>自动点名</button><button id="teachingSessionCheckinBtn">点名统计</button></div></section></div>
  <script>history.replaceState(null,'','/?session_id=12&attendance_id=9&student_id=1');window.APP_CONFIG={classOfferingId:2,userInfo:{id:1,role:'teacher'},teachingPlan:{sessions:[${JSON.stringify(lesson)}]}};</script>
  <script type="module">import {initClassroomPage} from '${asset('js/classroom_page.js')}';initClassroomPage();window.ready=true;</script></body></html>`;
  const errors=await fixture(page,async(route,url)=>{urls.push(url);if(url.endsWith('/rounds/9'))return json(route,current);if(url.endsWith('/students/1'))return json(route,{record:current.records[0],history:[]});return json(route,{items:[]});},'teacher','',markup);
  const d=page.getByRole('dialog',{name:'自动点名',exact:true}); await expect(d.locator('[data-student-id]')).toHaveCount(2);
  await expect(d.getByRole('heading',{name:'合成同学1 · 本次签到'})).toBeVisible();
  expect(urls.some(url=>url.endsWith('/rounds/9'))).toBeTruthy();expect(urls.some(url=>url.endsWith('/sessions/12'))).toBeFalsy();
  expect(await page.evaluate(()=>{const trigger=document.querySelector('#teachingSessionAutoAttendanceBtn');return Boolean((window as any).attendance)||trigger?.isConnected;})).toBeTruthy();expect(errors).toEqual([]);
});

test('LQ merged history deep link has no lesson dependency and gates manual edits separately from live operations',async({page})=>{
  const current={...round(2),id:42,session_id:null,is_current:0,can_edit:true,history_only:true,state:'published',context:{session_title:'已合并的原课次',session_date:'2026-09-13',course_name:'合成原课程'}};
  current.records.forEach(row=>(row as any).class_name='原合成班'); const calls:string[]=[];
  const markup=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"></head><body><button id="cw-attendance-history">历史点名</button><script>history.replaceState(null,'','/?attendance_id=42&history_only=1');window.APP_CONFIG={classOfferingId:2,userInfo:{id:1,role:'teacher'},teachingPlan:{sessions:[]}};</script><script type="module">import {initClassroomPage} from '${asset('js/classroom_page.js')}';initClassroomPage();window.ready=true;</script></body></html>`;
  const errors=await fixture(page,async(route,url,body)=>{calls.push(url);if(url.endsWith('/rounds/42'))return json(route,current);if(url.endsWith('/records'))return json(route,{results:body.records.map((r:any)=>({student_id:r.student_id,result:'updated'})),round:current});return json(route,{detail:'must not load another lesson'},500);},'teacher','',markup);
  const d=page.getByRole('dialog',{name:'历史点名详情',exact:true}); await expect(d.getByRole('heading',{name:'已合并的原课次'})).toBeVisible(); await expect(d.getByText('2026-09-13 原合成班 · 北京时间',{exact:false})).toBeVisible();
  await expect(d.getByRole('button',{name:'结束本地点名'})).toHaveCount(0);await expect(d.getByRole('button',{name:'重新核算'})).toHaveCount(0);await expect(d.getByRole('button',{name:'智慧课堂核对',exact:true})).toHaveCount(0);
  await d.getByRole('button',{name:'全班',exact:true}).click();await d.getByLabel('修改理由').fill('教师核对原班级历史事实');await expect(d.getByRole('button',{name:'应用到已选学生'})).toBeEnabled();await d.getByRole('button',{name:'应用到已选学生'}).click();await expect(d.locator('.la-bulk-fields button[data-lq-component="button"]')).toBeDisabled();await expect(d.locator('.la-selection-count')).toHaveText('已选择 0 人');
  expect(calls.every(url=>url.includes('/rounds/42'))).toBeTruthy();expect(errors).toEqual([]);
});

test('LQ teacher history picker pages without merging lesson identities or accumulating all pages',async({page})=>{
  const current=round(2), history={...round(1),id:42,session_id:null,is_current:0,can_edit:false,history_only:true,context:{session_title:'原班级归档课次',session_date:'2026-09-13'}};
  const before:(string|null)[]=[];
  const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/history')){const value=new URL(route.request().url()).searchParams.get('before_id');before.push(value);return json(route,{items:[{id:value?41:42,context:history.context}],next_before_id:value?null:42});}
    if(url.endsWith('/rounds/42'))return json(route,history);return json(route,{detail:'unexpected'},500);
  });
  await open(page);await page.getByRole('dialog',{name:'自动点名',exact:true}).getByRole('button',{name:'历史点名',exact:true}).click();const picker=page.getByRole('dialog',{name:'历史点名',exact:true});
  await choose(page,picker,'历史点名','2026-09-13 原班级归档课次 · 点名 42');await picker.getByRole('button',{name:'查看所选历史'}).click();const d=page.getByRole('dialog',{name:'历史点名详情',exact:true});await expect(d.getByRole('heading',{name:'原班级归档课次'})).toBeVisible();
  await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await picker.getByRole('button',{name:'更早记录'}).click();await expect(picker.locator('select option')).toHaveCount(2);await expect(picker.getByRole('button',{name:'更早记录'})).toBeDisabled();expect(before).toEqual([null,'42']);expect(errors).toEqual([]);
});

test('LQ school recovery reloads the actual binding revision and requires local end before binding',async({page})=>{
  const current={...round(2),state:'published'};let revision:any=null;const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/capabilities'))return json(route,{read_sources:true,read_checkins:true,readback:true,update_student:false});
    if(url.endsWith('/bindings'))return json(route,{items:[{id:'binding-synthetic',revision:7,state:'active'}]});
    if(url.endsWith('/runs'))return json(route,{items:[{id:'run-synthetic'}]});
    if(url.endsWith('/runs/run-synthetic'))return json(route,{id:'run-synthetic',binding_id:'binding-synthetic',revision:2,state:'needs_teacher_action',operations:[]});
    if(url.endsWith('/reauthorize')){revision=body.expected_revision;return json(route,{id:'binding-synthetic',revision:8,state:'active'});}
    if(url.endsWith('/preview'))return json(route,{id:'preview-synthetic',binding_id:'binding-synthetic',revision:0,state:'preview',operations:[]});
    return json(route,{detail:'unexpected'},500);
  });
  await open(page);await page.getByRole('button',{name:'智慧课堂核对',exact:true}).click();const d=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});await expect(d.getByRole('button',{name:'原学校账号重新验证后续接'})).toBeEnabled();await d.getByRole('button',{name:'原学校账号重新验证后续接'}).click();await expect.poll(()=>revision).toBe(7);expect(errors).toEqual([]);
});

test('LQ measured learning is healthy evidence and does not enter the collection review filter',async({page})=>{
  const current=round(3);current.records[1].evidence.collection.status='measured';current.records[1].evidence.learning.measured_seconds=45;
  (current.records[1].evidence as any).network_access={first_ip:'192.0.2.14',last_ip:'192.0.2.15',distinct_ips:['192.0.2.14','192.0.2.15'],source_types:['login'],observation_count:3};
  const errors=await fixture(page,async(route,url)=>url.includes('/students/')?json(route,{record:current.records[1],history:[]}):json(route,{session,policy:current.policy,round:current}));
  await open(page);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'查看详情',exact:true}).nth(1).click();await expect(d.getByText('已采集有效学习',{exact:false})).toBeVisible();await expect(d.getByText('平台接入 IP（本轮时间窗）：首次 192.0.2.14；末次 192.0.2.15。')).toBeVisible();
  await choose(page,d,'筛选','采集待核对');await expect(d.locator('[data-student-id]')).toHaveCount(1);await expect(d.locator('[data-student-id]')).toHaveAttribute('data-student-id','3');expect(errors).toEqual([]);
});

test('LQ durable teacher attention resumes after a persisted page lifecycle without replaying acknowledgement',async({page})=>{
  const row={request_id:70,student_id:4,name:'合成同学4',round_id:9,class_offering_id:2,session_id:12};let reads=0,acks=0;
  const errors=await fixture(page,async(route,url)=>{if(url.endsWith('/attention')){reads++;return json(route,{items:[row]});}if(url.endsWith('/claim'))return json(route,{claimed:true,item:row});if(url.endsWith('/ack')){acks++;return json(route,{acknowledged:true});}return json(route,{});});
  await page.evaluate(async(url)=>{const m=await import(url);(window as any).disposeAttention=m.initAttendanceAttention();},asset('js/login_attendance_attention.js'));
  const d=page.getByRole('dialog',{name:'待处理签到申诉'});await expect(d).toBeVisible();
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true})));await expect(d).not.toBeVisible();
  await page.evaluate(()=>window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true})));await expect(d).toBeVisible();expect(reads).toBeGreaterThanOrEqual(2);expect(acks).toBe(0);
  await page.evaluate(()=>(window as any).disposeAttention());expect(errors).toEqual([]);
});

test('LQ lesson can restart after void and refresh, while an older void link cannot replace a newer live round',async({page})=>{
  let current=round(2), old:any=null;const creates:any[]=[];
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/void')){current={...current,state:'void',is_current:0 as any,revision:2};old=current;return json(route,current);}
    if(url.endsWith('/sessions/12/rounds')){creates.push(body);current={...round(2),id:10};return json(route,current);}
    if(url.endsWith('/rounds/9'))return json(route,old);
    if(url.endsWith('/rounds/10'))return json(route,current);
    return json(route,{detail:'unexpected '+url},500);
  });
  await open(page);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.locator('[data-student-id]')).toHaveCount(2);
  await d.getByRole('button',{name:'作废本轮',exact:true}).click();await d.getByLabel('修改理由').fill('教师核对后作废，重新确认本轮网络');await d.getByRole('button',{name:'作废本轮',exact:true}).click();
  await page.getByRole('dialog',{name:'作废本轮点名？',exact:true}).getByRole('button',{name:'作废本轮',exact:true}).click();
  await expect(d.getByRole('button',{name:'开启本地点名',exact:true})).toBeVisible();await d.getByRole('button',{name:'刷新记录',exact:true}).click();await expect(d.getByRole('button',{name:'开启本地点名',exact:true})).toBeVisible();
  await d.getByText('教室网络与本课时段',{exact:true}).click();await d.getByLabel('我已核对以上为本次课堂使用的网络',{exact:true}).check();await d.getByRole('button',{name:'开启本地点名',exact:true}).click();await expect(d.getByRole('status')).toContainText('本地点名已开启');expect(creates).toHaveLength(1);expect(creates[0].supersedes_id).toBeUndefined();
  await page.evaluate(()=>(window as any).attendance.destroy());await page.getByRole('button',{name:'自动点名',exact:true}).click();await expect(d.locator('[data-student-id]')).toHaveCount(2);
  await page.evaluate(async(url)=>{(window as any).attendance.destroy();const m=await import(url);(window as any).attendance=m.openLoginAttendance({classOfferingId:2,session:{id:12},userRole:'teacher',trigger:document.querySelector('#attendance'),attendanceId:9});},asset('js/login_attendance.js'));
  await expect(d.getByText('已作废',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'开启本地点名',exact:true})).toHaveCount(0);expect(errors).toEqual([]);
});

test('LQ stale teacher attention loses ownership with 404 and releases the page without acknowledging',async({page})=>{
  const row={request_id:70,student_id:4,name:'合成同学4',round_id:9,class_offering_id:2,session_id:12};let lost=false,ackAttempts=0;
  const errors=await fixture(page,async(route,url)=>{if(url.endsWith('/attention'))return json(route,{items:lost?[]:[row]});if(url.endsWith('/claim'))return json(route,{claimed:true,item:row});if(url.endsWith('/ack')){lost=true;ackAttempts++;return json(route,{detail:'提醒不存在或当前无权处理。'},404);}return json(route,{});});
  await page.evaluate(async(url)=>{const m=await import(url);(window as any).disposeAttention=m.initAttendanceAttention();},asset('js/login_attendance_attention.js'));
  const d=page.getByRole('dialog',{name:'待处理签到申诉'});await expect(d).toBeVisible();await d.getByRole('button',{name:'查看并处理'}).click();await expect(d).not.toBeVisible();
  await page.getByRole('button',{name:'进入课次',exact:true}).click();await expect(page.getByRole('dialog',{name:'课次详情',exact:true})).toBeVisible();expect(ackAttempts).toBe(1);expect(page.url()).toBe(origin+'/');await page.evaluate(()=>(window as any).disposeAttention());expect(errors).toEqual([]);
});

test('LQ generic attendance approval keeps its note across a revision conflict and refreshes the displayed revision before approval',async({page})=>{
  let revision=2,approved=false;const submitted:any[]=[];
  const item=()=>({id:70,request_type:'login_attendance_appeal',title:'合成签到申诉',status:approved?'approved':'pending',status_label:approved?'已通过':'待审批',reason:'请核对本人的课堂记录',applicant_name:'合成同学4',created_at:'2026-11-09T10:00:00',can_decide:!approved,payload:{record_revision:1},detail:{attendance:{record:{...record(4),revision}}},events:[]});
  const markup=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"></head><body><div id="approvals"></div><script type="module">import {mountPanel} from '${asset('js/approval_workflow.js')}';window.panel=mountPanel(document.querySelector('#approvals'),{startOpen:true});window.ready=true;</script></body></html>`;
  const errors=await fixture(page,async(route,url,body)=>{if(url==='/api/approvals')return json(route,{items:[item()],pending_count:approved?0:1});if(url.endsWith('/70/approve')){submitted.push(body);if(body.decision_payload.record_revision!==revision)return json(route,{detail:'学生考勤已变化，请打开最新详情后处理'},409);approved=true;return json(route,{request:item()});}if(url.endsWith('/70'))return json(route,{request:item()});return json(route,{detail:'unexpected'},500);},'teacher','',markup);
  await page.locator('[data-apr-open="70"]').click();await page.locator('[data-apr-field="note"]').fill('教师已核对签到证据，保留此审批说明');
  expect(await page.locator('[data-apr-field="attendance_status"] option').count()).toBe(6);revision=3;await page.locator('[data-apr-decide="approve"]').click();await expect.poll(()=>submitted.length).toBe(1);expect(submitted[0].decision_payload.record_revision).toBe(2);await expect(page.locator('[data-apr-field="note"]')).toHaveValue('教师已核对签到证据，保留此审批说明');
  await page.locator('[data-apr-attendance-refresh]').click();await expect(page.locator('[data-apr-field="note"]')).toHaveValue('教师已核对签到证据，保留此审批说明');await page.locator('[data-apr-decide="approve"]').click();await expect.poll(()=>submitted.length).toBe(2);expect(submitted[1].decision_payload).toEqual({status:'PRESENT',record_revision:3});await expect(page.locator('[data-apr-modal-body]')).toContainText('已通过');expect(errors).toEqual([]);
});
