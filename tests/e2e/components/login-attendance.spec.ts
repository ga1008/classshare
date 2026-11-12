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
async function fixture(page: Page, handler: APIHandler, role = 'teacher', extra = '', markup?: string, workflowAPI = false, studentPushAPI = false, settlementAPI = false) {
  const errors: string[] = []; page.on('pageerror', error => { errors.push(error.message); console.error(error.stack); });
  await page.route(`${origin}/**`, async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (!workflowAPI && pathname.endsWith('/external/workflow')) return route.fulfill({json:{binding:{state:'unmatched',revision:0,message:'课次待对应'},plan:null,can_sync:false}});
    if (!studentPushAPI && /\/external\/students\/\d+\/push(?:\/check)?$/.test(pathname)) return route.fulfill({json:{gate:{allowed:false,code:'alignment_required',message:'请先对齐课程和课次'},push:null,local_saved:false}});
    if (!settlementAPI && /\/rounds\/\d+\/settlement$/.test(pathname)) return route.fulfill({json:{gate:{can_start:true,can_restart:false,code:'ready',message:'可按当前证据结算'},task:null,round_revision:1}});
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
async function open(page: Page, role = 'teacher', expanded = true) { await page.getByRole('button', { name: '进入课次', exact: true }).click(); await page.getByRole('button', { name: role === 'teacher' ? '自动点名' : '我的签到', exact: true }).click(); if(role==='teacher'&&expanded){await page.getByText('记录与更多操作',{exact:true}).click();await page.getByText('批量修改出勤状态',{exact:true}).click();} }
const json = (route: Route, value: any, status = 200) => route.fulfill({ status, json: value });
async function choose(page:Page,dialog:any,label:string,option:string){const control=dialog.getByRole('combobox',{name:label,exact:true});if(await control.evaluate((n:Element)=>n.tagName==='SELECT'))await control.selectOption({label:option});else{await control.click();await page.getByRole('option',{name:option,exact:true}).click();await expect(page.getByRole('option',{name:option,exact:true})).not.toBeVisible();}}

const timingNow=Date.parse('2026-11-12T10:00:00+08:00');
async function timingFixture(page:Page,options:any={}){
  const baseRound={...round(6),context:{year:'2026-2027',term:1,time_source:'server_actual'},starts_at_ms:timingNow-600000,actual_started_at_ms:timingNow-600000,ends_at_ms:timingNow+4800000,server_now_ms:timingNow,policy:{revision:3,networks:[],enforce_network:false,algorithm_version:'actual-window-v3-20261112'}};
  const s:any={server:timingNow,current:options.beforeStart?null:options.current||baseRound,task:null,processing:options.processing,calls:[],creates:0};
  s.dto=()=>({server_now_ms:s.server,round_revision:s.current?.revision||1,task:s.task,needs_rule_upgrade:Boolean(options.upgrade),gate:s.gate||{can_start:s.current?.state!=='settling',can_restart:s.current?.state==='settling',code:'ready',message:'可按当前证据结算'},processing:s.processing||{stage:s.current?.state==='settling'?'waiting_receipts':s.current?.state==='published'?'completed':'recording',wait_until_ms:s.current?.settle_at_ms,message:s.current?.state==='settling'?'正在接收本轮延迟补报':'本轮结果已保存'}});
  s.errors=await fixture(page,async(route,url,body)=>{
    s.calls.push({url,body,method:route.request().method()});
    if(url.endsWith('/sessions/12'))return json(route,{server_now_ms:s.server,default_duration_minutes:90,session,policy:{revision:3,networks:options.savedNetworks||[],enforce_network:false},teacher_network:{ip:'192.0.2.55',suggested_cidr:'192.0.2.0/24'},round:s.current});
    if(url.endsWith('/sessions/12/rounds')){s.creates++;if(options.createFailure&&s.creates===1)return json(route,{detail:'合成网段校验失败，请核对'},422);s.current={...baseRound,server_now_ms:s.server,starts_at_ms:s.server,actual_started_at_ms:s.server,ends_at_ms:s.server+body.duration_minutes*60000,policy:{...baseRound.policy,enforce_network:body.enforce_network,networks:body.networks??options.savedNetworks??[]}};return json(route,s.current);}
    if(url.endsWith('/rounds/9'))return json(route,{...s.current,server_now_ms:s.server});
    if(url.endsWith('/rounds/9/end')){s.current={...s.current,state:'settling',revision:s.current.revision+1,ended_at_ms:s.server,settle_at_ms:s.server+10000,settlement:null,settlement_gate:{can_start:false,can_restart:true,code:'restart_available',message:'可确认中断补报等待'},processing:{stage:'waiting_receipts',wait_until_ms:s.server+10000,message:'正在接收本轮延迟补报'}};return json(route,s.current);}
    if(url.endsWith('/settlement')){if(body){s.task=settlementTask();s.current={...s.current,state:'settling',revision:s.current.revision+1};s.processing={stage:'queued',message:'新的结算任务已受理'};return json(route,{...s.dto(),round:s.current},202);}return options.settlementGet?options.settlementGet(route,s):json(route,s.dto());}
    if(/\/students\/\d+$/.test(url))return json(route,{record:s.current.records[Number(url.split('/').at(-1))-1],history:[]});
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,false,false,true);
  if(options.dark)await page.evaluate(()=>{document.documentElement.dataset.appearance='dark';});await open(page,'teacher',false);s.dialog=page.getByRole('dialog',{name:'自动点名',exact:true});return s;
}

test('LQ network timing starts a past lesson now without restricting IP by default',async({page})=>{
  const s=await timingFixture(page,{beforeStart:true}),d=s.dialog;await expect(d.getByRole('switch',{name:'限定课堂网络'})).not.toBeChecked();await expect(d.getByLabel('本课教室 IP 或网段（每行一个）')).toBeHidden();await expect(d.getByLabel('预计记录时长（分钟）')).toHaveValue('90');await d.getByRole('button',{name:'开始记录',exact:true}).click();const request=s.calls.find((c:any)=>c.url.endsWith('/sessions/12/rounds')).body;expect(request).toMatchObject({enforce_network:false,duration_minutes:90,policy_revision:3});expect(request.networks).toBeUndefined();expect(request.starts_at).toBeUndefined();expect(request.ends_at).toBeUndefined();await expect(d.locator('.la-heading')).toContainText('2026-11-09');await expect(d.locator('.la-clock')).toContainText('2026/11/12 10:00:00');await expect(d.locator('.la-clock')).toContainText('已上课 0 分钟');expect(s.errors).toEqual([]);
});
test('LQ network timing preserves edited CIDRs across switch toggles and rejected creation',async({page})=>{
  const s=await timingFixture(page,{beforeStart:true,createFailure:true}),d=s.dialog,toggle=d.getByRole('switch',{name:'限定课堂网络'}),networks=d.getByLabel('本课教室 IP 或网段（每行一个）');await toggle.check();await expect(networks).toHaveValue('192.0.2.0/24');await networks.fill('198.51.100.0/24\n2001:db8::/64');await toggle.uncheck();await toggle.check();await expect(networks).toHaveValue('198.51.100.0/24\n2001:db8::/64');await d.getByLabel('预计记录时长（分钟）').fill('45');await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.getByRole('alert')).toContainText('合成网段校验失败');await expect(networks).toHaveValue('198.51.100.0/24\n2001:db8::/64');await expect(d.getByLabel('预计记录时长（分钟）')).toHaveValue('45');await d.getByRole('button',{name:'开始记录',exact:true}).click();expect(s.calls.filter((c:any)=>c.url.endsWith('/sessions/12/rounds')).map((c:any)=>c.body)).toEqual([expect.objectContaining({enforce_network:true,networks:['198.51.100.0/24','2001:db8::/64'],duration_minutes:45}),expect.objectContaining({enforce_network:true,networks:['198.51.100.0/24','2001:db8::/64'],duration_minutes:45})]);expect(s.errors).toEqual([]);
});
test('LQ network timing rejects empty enabled networks and invalid duration before creation',async({page})=>{
  const s=await timingFixture(page,{beforeStart:true}),d=s.dialog;await d.getByLabel('预计记录时长（分钟）').fill('241');await d.getByRole('button',{name:'开始记录',exact:true}).click();expect(s.creates).toBe(0);await d.getByLabel('预计记录时长（分钟）').fill('30');await d.getByRole('switch',{name:'限定课堂网络'}).check();await d.getByLabel('本课教室 IP 或网段（每行一个）').fill('');await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.getByRole('alert')).toContainText('请填写已确认的教室 IP 或网段');expect(s.creates).toBe(0);expect(s.errors).toEqual([]);
});
test('LQ network timing disabling IP preserves saved networks and ignores the hidden unfinished draft',async({page})=>{
  const saved=['192.0.2.0/24','2001:db8::/64'],s=await timingFixture(page,{beforeStart:true,savedNetworks:saved}),d=s.dialog;await d.getByRole('switch',{name:'限定课堂网络'}).check();await expect(d.getByLabel('本课教室 IP 或网段（每行一个）')).toHaveValue(saved.join('\n'));await d.getByLabel('本课教室 IP 或网段（每行一个）').fill('尚未填写完的网段');await d.getByRole('switch',{name:'限定课堂网络'}).uncheck();await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.locator('.la-clock')).toContainText('不限 IP');const body=s.calls.find((c:any)=>c.url.endsWith('/sessions/12/rounds')).body;expect(body.enforce_network).toBe(false);expect(body.networks).toBeUndefined();expect(s.current.policy.networks).toEqual(saved);expect(s.errors).toEqual([]);
});
test('LQ network timing ignores a skewed local wall clock and advances only by monotonic time',async({page})=>{
  await page.clock.install({time:new Date('2040-01-01T00:00:00Z')});const s=await timingFixture(page),d=s.dialog;await expect(d.locator('.la-clock')).toContainText('已上课 10 分钟');await page.clock.setFixedTime(new Date('2060-01-01T00:00:00Z'));await expect(d.locator('.la-clock')).toContainText('已上课 10 分钟');await page.clock.fastForward(60000);await expect(d.locator('.la-clock')).toContainText('已上课 11 分钟');expect(s.errors).toEqual([]);
});
test('LQ network timing end shows server receipt countdown then queued and completed without invented percent',async({page})=>{
  await page.clock.install({time:new Date('2040-01-01T00:00:00Z')});const s=await timingFixture(page),d=s.dialog;await d.getByRole('button',{name:'结束本地点名',exact:true}).click();await page.getByRole('dialog',{name:'结束本地点名？'}).getByRole('button',{name:'结束本地点名',exact:true}).click();await expect(d.getByLabel('补报剩余时间')).toContainText('补报等待剩余');await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).not.toHaveAttribute('value');await page.clock.fastForward(5000);await expect(d.getByLabel('补报剩余时间')).toContainText('0 分 0');await page.clock.fastForward(6000);await expect(d.getByLabel('补报剩余时间')).toContainText('等待后台开始核算');s.processing={stage:'queued',message:'等待后台领取本轮结算'};await page.clock.fastForward(3000);await expect(d.locator('.la-settlement')).toContainText('等待后台领取本轮结算');await expect(d.getByRole('progressbar')).not.toHaveAttribute('value');s.current={...s.current,state:'published',revision:s.current.revision+1};s.processing={stage:'completed',message:'本轮出勤结果已保存'};await page.clock.fastForward(3000);await expect(d.getByRole('progressbar')).toHaveAttribute('value','100');await expect(d.locator('.la-clock')).toContainText('本次记录 10 分钟');expect(s.errors).toEqual([]);
});
test('LQ network timing end snapshot immediately updates progress and restart gate despite stale or failed reads',async({page})=>{
  let delay=false,held=false,release:any;const s=await timingFixture(page,{settlementGet:async(route:Route,state:any)=>{if(delay&&!held){held=true;const old=state.dto();await new Promise(resolve=>{release=resolve;});return json(route,old).catch(()=>{});}return delay?json(route,{detail:'合成状态读取暂不可用'},503):json(route,state.dto());}}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();delay=true;await page.evaluate(()=>(window as any).attendance.refresh());await expect.poll(()=>held).toBe(true);await d.getByRole('button',{name:'结束本地点名',exact:true}).click();await page.getByRole('dialog',{name:'结束本地点名？'}).getByRole('button',{name:'结束本地点名',exact:true}).click();await expect(d.getByLabel('补报剩余时间')).toContainText('补报等待剩余');await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();release();await expect(d.locator('.la-settlement')).toContainText('合成状态读取暂不可用');await d.getByRole('button',{name:'立即结算',exact:true}).click();const confirm=page.getByRole('dialog',{name:'中断当前结算并重新开始？'});await expect(confirm).toBeVisible();await confirm.getByRole('button',{name:'取消',exact:true}).click();expect(s.calls.some((c:any)=>c.url.endsWith('/settlement')&&c.body)).toBe(false);expect(s.errors).toEqual([]);
});
test('LQ network timing all attendance states filter independently and time sorts keep unknown last',async({page})=>{
  const s=await timingFixture(page),d=s.dialog;const states=['ABSENT','SICK_LEAVE','PERSONAL_LEAVE','LATE','EARLY','PRESENT'];s.current.records=s.current.records.map((r:any,i:number)=>({...r,status:states[i],status_label:states[i],metrics:{access_span_seconds:[60,0,null,120,60,30][i],learning_seconds:[10,null,0,20,5,1][i],access_observation_count:i+1}}));await page.evaluate(()=>(window as any).attendance.refresh());for(const [label,id] of [['缺课',1],['病假',2],['事假',3],['迟到',4],['早退',5],['出勤',6]]){await choose(page,d,'筛选',String(label));await expect(d.locator('[data-student-id]')).toHaveCount(1);await expect(d.locator('[data-student-id]')).toHaveAttribute('data-student-id',String(id));}await choose(page,d,'筛选','全部学生');await choose(page,d,'排序','登录跨度：长到短');expect(await d.locator('[data-student-id]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('data-student-id')))).toEqual(['4','1','5','6','2','3']);await choose(page,d,'排序','学习时长：短到长');expect(await d.locator('[data-student-id]').evaluateAll(nodes=>nodes.map(n=>n.getAttribute('data-student-id')))).toEqual(['3','6','5','1','4','2']);await expect(d.locator('[data-student-id="3"]')).toContainText('登录跨度 未完整记录');await expect(d.locator('[data-student-id="3"]')).toContainText('学习时长 0 分 0 秒');await expect(d.locator('.la-metric-help')).toContainText('不代表连续在线');expect(s.errors).toEqual([]);
});
test('LQ network timing automatic completion refreshes results even when another action blocks settlement gate',async({page})=>{
  await page.clock.install({time:new Date('2040-01-01T00:00:00Z')});const s=await timingFixture(page,{current:{...round(2),state:'settling'}}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();s.gate={can_start:false,can_restart:false,code:'school_busy',message:'学校任务正在核对'};s.current={...s.current,state:'published',revision:2};s.processing={stage:'completed',message:'本轮已完成'};await page.clock.fastForward(3000);await expect(d.locator('.la-heading')).toContainText('已结束');await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeDisabled();expect(s.calls.filter((c:any)=>c.url.endsWith('/rounds/9'))).toHaveLength(1);expect(s.errors).toEqual([]);
});
test('LQ network timing legacy windows retain their original network meaning and are not labeled actual start',async({page})=>{
  const s=await timingFixture(page,{current:{...round(2),starts_at_ms:timingNow-600000,ends_at_ms:timingNow+3000000,server_now_ms:timingNow}}),d=s.dialog;await expect(d.locator('.la-clock')).toContainText('原记录窗口开始');await expect(d.locator('.la-clock')).toContainText('限定课堂网段');await expect(d.locator('.la-clock')).not.toContainText('实际开始');expect(s.errors).toEqual([]);
});
test('LQ network timing sorting is not an unsaved edit and closing releases its single clock timer',async({page})=>{
  await page.addInitScript(()=>{const intervals=new Set<number>(),set=window.setInterval.bind(window),clear=window.clearInterval.bind(window);(window as any).attendanceIntervals=intervals;window.setInterval=((...args:any[])=>{const id=set(args[0],args[1],...args.slice(2));intervals.add(id);return id;}) as any;window.clearInterval=(id)=>{intervals.delete(Number(id));clear(id);};});const s=await timingFixture(page),d=s.dialog;await choose(page,d,'排序','学习时长：长到短');await expect.poll(()=>page.evaluate(()=>(window as any).attendanceIntervals.size)).toBe(1);await d.getByRole('button',{name:'关闭',exact:true}).click();await expect(d).toBeHidden();await expect(page.getByRole('dialog',{name:'关闭并放弃未提交内容？'})).toHaveCount(0);await expect.poll(()=>page.evaluate(()=>(window as any).attendanceIntervals.size)).toBe(0);expect(s.errors).toEqual([]);
});
for(const restart of [false,true])test(`LQ network timing old-rule upgrade requires explicit confirmation${restart?' with interruption':''}`,async({page})=>{
  const current={...round(2),state:restart?'settling':'published'},s=await timingFixture(page,{current,upgrade:true}),d=s.dialog;const button=d.getByRole('button',{name:'立即结算',exact:true});await expect(button).toBeEnabled();await button.click();let confirm=page.getByRole('dialog',{name:restart?'中断当前结算并重新开始？':'按当前规则重新核算？'});await expect(confirm).toContainText('不再要求班级多数');await expect(confirm).toContainText('原 IP 限定及人工改签保留');await confirm.getByRole('button',{name:'取消',exact:true}).click();expect(s.calls.some((c:any)=>c.url.endsWith('/settlement')&&c.body)).toBe(false);await expect(button).toBeFocused();await button.click();confirm=page.getByRole('dialog',{name:restart?'中断当前结算并重新开始？':'按当前规则重新核算？'});await confirm.getByRole('button',{name:restart?'中断并重新结算':'按当前规则结算',exact:true}).click();await expect.poll(()=>s.calls.filter((c:any)=>c.url.endsWith('/settlement')&&c.body).length).toBe(1);expect(s.calls.find((c:any)=>c.url.endsWith('/settlement')&&c.body).body).toMatchObject({adopt_current_rules:true,restart});expect(s.errors).toEqual([]);
});
for(const dark of [false,true])for(const width of [390,1440])test(`LQ network timing ${dark?'dark':'light'} ${width}px keeps network setup and timing readable`,async({page})=>{
  await page.setViewportSize({width,height:980});const s=await timingFixture(page,{beforeStart:true,dark}),d=s.dialog;await d.getByRole('switch',{name:'限定课堂网络'}).check();await expect.poll(()=>d.evaluate(n=>n.closest('[data-lq-layer-state]')?.getAttribute('data-lq-layer-state'))).toBe('open');expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await page.screenshot({path:`.codex-temp/evidence/attendance-network-timing-20261112/setup-${dark?'dark':'light'}-${width}.png`});await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.locator('.la-clock')).toContainText('实际开始');await expect(d.locator('.la-student-metrics')).toHaveCount(6);expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await page.screenshot({path:`.codex-temp/evidence/attendance-network-timing-20261112/recording-${dark?'dark':'light'}-${width}.png`});expect(s.errors).toEqual([]);
});

const settlementTask=(generation=1,state='queued')=>({id:`settlement-${generation}`,generation,revision:1,state,stage:state==='running'?'collecting':state,completed:state==='completed'?4:0,total:4,percent:state==='completed'?100:null,message:'按本轮已采集证据核算',updated_at_ms:1794187800000});
async function settlementFixture(page:Page,options:any={}){
  const s:any={current:options.beforeStart?null:options.current||{...round(3),revision:2},task:options.task||null,calls:[],posts:0};
  s.dto=()=>{const restart=s.current?.state==='settling'||['queued','running','failed'].includes(s.task?.state);return {gate:options.gate||{can_start:!restart,can_restart:restart,code:restart?'restart_available':'ready',message:restart?'可确认中断当前等待或结算并重算':'可按当前证据立即结算'},task:s.task,round_revision:s.current?.revision||1};};
  s.accept=(body:any)=>{s.posts++;s.current={...s.current,state:'settling',revision:s.current.revision+1};s.task=settlementTask((s.task?.generation||0)+1);return {...s.dto(),round:s.current};};
  s.finish=()=>{s.current={...s.current,state:'published',revision:s.current.revision+1,records:s.current.records.map((r:any)=>({...r,status:'PRESENT',status_label:'出勤',revision:r.revision+1})),summary:{expected:3,exempt:0,counts:{PRESENT:3}}};s.task={...s.task,state:'completed',stage:'completed',completed:4,percent:100};};
  s.errors=await fixture(page,async(route,url,body)=>{
    s.calls.push({method:route.request().method(),url,body});
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:{revision:0,networks:['192.0.2.0/24']},window:{starts_at_ms:1794182400000,ends_at_ms:1794187800000},round:s.current});
    if(url.endsWith('/sessions/12/rounds')){s.current={...round(3),revision:2};return json(route,s.current);}
    if(url.endsWith('/rounds/9'))return options.roundRead?options.roundRead(route,s):json(route,s.current);
    if(url.endsWith('/settlement'))return body?(options.post?options.post(route,body,s):json(route,s.accept(body),202)):(options.get?options.get(route,s):json(route,s.dto()));
    if(/\/students\/\d+$/.test(url))return json(route,{record:s.current.records[Number(url.split('/').at(-1))-1],history:[]});
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,false,false,true);
  if(options.dark)await page.evaluate(()=>{document.documentElement.dataset.appearance='dark';});await open(page,'teacher',false);s.dialog=page.getByRole('dialog',{name:'自动点名',exact:true});return s;
}

test('LQ settlement is visible but disabled before recording and unlocks only after the server gate',async({page})=>{
  const s=await settlementFixture(page,{beforeStart:true}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeDisabled();expect(s.calls.some((c:any)=>c.url.endsWith('/settlement'))).toBe(false);await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();await expect(d.getByRole('button',{name:'结束本地点名',exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'重新核算',exact:true})).toHaveCount(0);expect(s.errors).toEqual([]);
});

for(const kind of ['scheduled','expired','historical','void'])test(`LQ settlement ${kind} gate never enables a forbidden calculation`,async({page})=>{
  const current={...round(3),state:kind==='expired'?'published':kind==='historical'?'published':kind,is_current:kind!=='historical'};
  const s=await settlementFixture(page,{current,gate:{can_start:false,can_restart:false,code:kind,message:'当前轮次不能立即结算'}}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeDisabled();expect(s.calls.some((c:any)=>c.method==='POST')).toBe(false);expect(s.errors).toEqual([]);
});

test('LQ settlement reports actual unknown and counted phases and refreshes final cards once',async({page})=>{
  const s=await settlementFixture(page),d=s.dialog;await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).not.toHaveAttribute('value');await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();expect(s.calls.find((c:any)=>c.method==='POST').body).toMatchObject({expected_round_revision:2,restart:false,expected_task_id:null,expected_task_generation:null});
  s.task={...s.task,state:'running',stage:'calculating',completed:2,total:4,percent:50};await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).toHaveAttribute('value','50',{timeout:8000});await expect(d.locator('.la-settlement')).toContainText('阶段 2/4');await expect(d.locator('.la-heading')).toContainText('正在结算');await expect(d.locator('.la-settlement')).not.toContainText('人');s.finish();await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).toHaveAttribute('value','100',{timeout:8000});await expect(d.locator('.la-student-open').first()).toContainText('出勤');expect(s.calls.filter((c:any)=>c.url.endsWith('/rounds/9'))).toHaveLength(1);expect(s.errors).toEqual([]);
});

for(const withTask of [false,true])test(`LQ settlement confirms interrupting ${withTask?'the active generation':'automatic receipt waiting'} and cancellation keeps it intact`,async({page})=>{
  const s=await settlementFixture(page,{current:{...round(3),revision:2,state:'settling'},task:withTask?settlementTask(4,'running'):null}),d=s.dialog;
  await d.getByRole('button',{name:'立即结算',exact:true}).click();let c=page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true});await expect(c).toBeVisible();await c.getByRole('button',{name:'取消',exact:true}).click();expect(s.posts).toBe(0);await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeFocused();await d.getByRole('button',{name:'立即结算',exact:true}).click();c=page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true});await c.getByRole('button',{name:'中断并重新结算',exact:true}).click();await expect.poll(()=>s.posts).toBe(1);expect(s.calls.find((x:any)=>x.method==='POST').body).toMatchObject({restart:true,expected_task_id:withTask?'settlement-4':null,expected_task_generation:withTask?4:null});await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();expect(s.errors).toEqual([]);
});

test('LQ settlement lost response replays exactly the same intent instead of restarting a received task',async({page})=>{
  let attempts=0,accepted:any;const s=await settlementFixture(page,{post:(route:Route,body:any,s:any)=>{attempts++;if(attempts===1){accepted=s.accept(body);return route.abort('failed');}return json(route,accepted,202);}}),d=s.dialog;
  await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect(d.locator('.la-settlement').getByRole('alert')).toContainText('结果尚未确认');await d.getByRole('button',{name:'确认结算请求',exact:true}).click();await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();const posts=s.calls.filter((c:any)=>c.method==='POST');expect(posts).toHaveLength(2);expect(posts[1].body).toEqual(posts[0].body);expect(s.posts).toBe(1);await expect(page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true})).toHaveCount(0);expect(s.errors).toEqual([]);
});

test('LQ settlement a concurrent task conflict refreshes identity and preserves batch drafts',async({page})=>{
  let attempts=0;const s=await settlementFixture(page,{post:(route:Route,body:any,s:any)=>{if(++attempts===1){s.current={...s.current,state:'settling',revision:5};s.task=settlementTask(5);return json(route,{detail:{code:'task_changed',message:'另一位老师已启动结算'}},409);}return json(route,s.accept(body),202);}}),d=s.dialog;
  await d.getByText('记录与更多操作',{exact:true}).click();await d.getByText('批量修改出勤状态',{exact:true}).click();await d.getByRole('button',{name:'全班',exact:true}).click();await d.getByLabel('修改理由').fill('批量草稿必须保留');await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect(d.locator('.la-settlement').getByRole('alert')).toContainText('本次结算请求未执行');await expect(d.getByLabel('修改理由')).toHaveValue('批量草稿必须保留');await d.getByRole('button',{name:'立即结算',exact:true}).click();await page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true}).getByRole('button',{name:'中断并重新结算',exact:true}).click();await expect.poll(()=>attempts).toBe(2);const posts=s.calls.filter((c:any)=>c.method==='POST');expect(posts[1].body).toMatchObject({restart:true,expected_round_revision:5,expected_task_id:'settlement-5',expected_task_generation:5});expect(posts[1].body.idempotency_key).not.toBe(posts[0].body.idempotency_key);expect(s.errors).toEqual([]);
});

test('LQ settlement completion behind student details preserves the form node and both drafts',async({page})=>{
  let release!:()=>void,readStarted=false;const wait=new Promise<void>(resolve=>release=resolve);
  const s=await settlementFixture(page,{roundRead:async(route:Route,s:any)=>{readStarted=true;await wait;return json(route,s.current);}}),d=s.dialog;
  try{await d.getByText('记录与更多操作',{exact:true}).click();await d.getByText('批量修改出勤状态',{exact:true}).click();await d.getByRole('button',{name:'全班',exact:true}).click();await d.getByLabel('修改理由').fill('未提交批量理由');await d.getByRole('button',{name:'立即结算',exact:true}).click();s.finish();await expect.poll(()=>readStarted,{timeout:8000}).toBe(true);await d.locator('.la-student-open').first().click();const child=page.getByRole('dialog',{name:'学生签到详情',exact:true});await child.getByLabel('改签理由').fill('新改签草稿');await child.getByLabel('改签理由').evaluate((n:Element)=>(window as any).settlementDraftNode=n);release();await expect(child.getByLabel('改签理由')).toHaveValue('新改签草稿');expect(await child.getByLabel('改签理由').evaluate((n:Element)=>n===(window as any).settlementDraftNode)).toBe(true);await expect(page.locator('.la-bulk-fields textarea')).toHaveValue('未提交批量理由');expect(s.errors).toEqual([]);}finally{release();}
});

test('LQ settlement only submission blocks closing and accepted work resumes after reopening',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);const s=await settlementFixture(page,{post:async(route:Route,body:any,s:any)=>{await wait;return json(route,s.accept(body),202);}}),d=s.dialog;
  try{await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeDisabled();await page.keyboard.press('Escape');await expect(d).toBeVisible();release();await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await page.getByRole('button',{name:'自动点名',exact:true}).click();await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).toBeVisible();expect(s.posts).toBe(1);expect(s.errors).toEqual([]);}finally{release();}
});

test('LQ settlement an already finished worker publishes immediately without an artificial progress delay',async({page})=>{
  const s=await settlementFixture(page,{post:(route:Route,body:any,s:any)=>{s.accept(body);s.finish();return json(route,{...s.dto(),round:s.current},202);}}),d=s.dialog;await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).toHaveAttribute('value','100');await expect(d.locator('.la-student-open').first()).toContainText('出勤');await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();expect(s.calls.filter((c:any)=>c.url.endsWith('/rounds/9'))).toHaveLength(1);expect(s.errors).toEqual([]);
});

test('LQ settlement a failed task stops fast polling and requires explicit confirmation to restart',async({page})=>{
  const s=await settlementFixture(page,{current:{...round(3),state:'settling'},task:{...settlementTask(3,'failed'),stage:'failed',completed:2,percent:50}}),d=s.dialog;await expect(d.locator('.la-settlement')).toContainText('结算未完成');const before=s.calls.filter((c:any)=>c.url.endsWith('/settlement')).length;await page.waitForTimeout(3200);expect(s.calls.filter((c:any)=>c.url.endsWith('/settlement'))).toHaveLength(before);await d.getByRole('button',{name:'立即结算',exact:true}).click();await page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true}).getByRole('button',{name:'中断并重新结算',exact:true}).click();await expect.poll(()=>s.posts).toBe(1);expect(s.calls.find((c:any)=>c.method==='POST').body).toMatchObject({restart:true,expected_task_id:'settlement-3',expected_task_generation:3});expect(s.errors).toEqual([]);
});

test('LQ settlement a delayed earlier generation cannot overwrite a confirmed restart',async({page})=>{
  let release!:()=>void,reads=0,waiting=false;const wait=new Promise<void>(resolve=>release=resolve);
  const s=await settlementFixture(page,{current:{...round(3),state:'settling'},task:settlementTask(1,'running'),get:async(route:Route,s:any)=>{if(++reads===2){waiting=true;const old={...s.dto(),task:settlementTask(1,'completed')};await wait;return json(route,old);}return json(route,s.dto());}}),d=s.dialog;
  try{await expect.poll(()=>waiting,{timeout:8000}).toBe(true);await d.getByRole('button',{name:'立即结算',exact:true}).click();await page.getByRole('dialog',{name:'中断当前结算并重新开始？',exact:true}).getByRole('button',{name:'中断并重新结算',exact:true}).click();await expect.poll(()=>s.posts).toBe(1);release();await expect(d.locator('.la-settlement')).toContainText('等待后台结算');await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).not.toHaveAttribute('value');expect(s.calls.filter((c:any)=>c.url.endsWith('/rounds/9'))).toHaveLength(0);expect(s.task.generation).toBe(2);expect(s.errors).toEqual([]);}finally{release();}
});

test('LQ settlement a school-independent status read failure can be retried without losing recording',async({page})=>{
  let reads=0;const s=await settlementFixture(page,{get:(route:Route,s:any)=>++reads===1?json(route,{detail:'暂时不可读取'},503):json(route,s.dto())}),d=s.dialog;await expect(d.locator('.la-settlement').getByRole('alert')).toContainText('已有记录与任务会保留');await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeDisabled();await d.getByRole('button',{name:'刷新结算状态',exact:true}).click();await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();await expect(d.getByRole('button',{name:'结束本地点名',exact:true})).toBeVisible();expect(s.posts).toBe(0);expect(s.errors).toEqual([]);
});

test('LQ settlement automatic receipt waiting finishes through a lightweight status read',async({page})=>{
  const s=await settlementFixture(page,{current:{...round(3),state:'settling'}}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();s.current={...s.current,state:'published',revision:3};await expect.poll(()=>s.calls.filter((c:any)=>c.url.endsWith('/rounds/9')).length,{timeout:8000}).toBe(1);await expect(d.locator('.la-heading')).toContainText('已结束');expect(s.posts).toBe(0);expect(s.errors).toEqual([]);
});

for(const dark of [false,true])for(const width of [390,1440])test(`LQ settlement ${dark?'dark':'light'} ${width}px retains the real progress and clickable restart action`,async({page})=>{
  await page.setViewportSize({width,height:980});const s=await settlementFixture(page,{dark,current:{...round(3),state:'settling'},task:{...settlementTask(2,'running'),stage:'calculating',completed:2,percent:50}}),d=s.dialog;await expect(d.getByRole('button',{name:'立即结算',exact:true})).toBeEnabled();await expect(d.getByRole('progressbar',{name:'本地点名结算进度'})).toHaveAttribute('value','50');await expect(d.locator('.la-heading')).toContainText('正在结算');expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await expect.poll(()=>d.evaluate(n=>n.closest('[data-lq-layer-state]')?.getAttribute('data-lq-layer-state'))).toBe('open');await page.screenshot({path:`.codex-temp/evidence/attendance-immediate-settlement-20261112/settlement-${dark?'dark':'light'}-${width}.png`});expect(s.errors).toEqual([]);
});

const readyStudentGate=()=>({allowed:true,code:'ready',message:'学校本次点名已开始过；已结束的点名仍可更新。',binding_revision:5,remote_checkin_id:'school-started-9'});
async function correctionFixture(page:Page,options:any={}){
  const s:any={current:options.current||{...round(2),state:'published',records:round(2).records.map(r=>({...r,revision:1}))},gate:options.gate||readyStudentGate(),push:options.push||null,calls:[],saves:0};
  s.dto=()=>({gate:s.gate,push:s.push,record:s.current.records[0],local_saved:s.saves>0||Boolean(s.push)});
  s.accept=(body:any)=>{s.saves++;s.current.records[0]={...s.current.records[0],revision:s.current.records[0].revision+1,status:body.status,status_label:body.status==='PRESENT'?'出勤':body.status==='SICK_LEAVE'?'病假':body.status,reason:body.reason};s.current.revision++;
    s.push=body.mode==='push'?{id:'student-intent-1',state:options.pushState||'confirmed',code:null,message:options.pushMessage||'',local_revision:s.current.records[0].revision,target_status:body.status,idempotency_key:body.idempotency_key,can_retry:['failed','blocked','outcome_unknown'].includes(options.pushState),readback_only:options.pushState==='outcome_unknown'}:null;return {...s.dto(),round:s.current};};
  s.errors=await fixture(page,async(route,url,body)=>{
    s.calls.push({method:route.request().method(),url,body});
    if(url.endsWith('/external/workflow')){const matched=options.gate?.code!=='alignment_required';return json(route,{binding:{state:matched?'matched':'unmatched',revision:5,target:matched?{course_name:'合成课堂',course_code:'TEST001',remote_checkin_id:'school-started-9'}:null,diagnostics:[]},plan:null,can_sync:matched&&s.current.state==='published'});}
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:s.current.policy,round:s.current});
    if(/\/rounds\/9\/students\/\d+$/.test(url))return json(route,{record:s.current.records[Number(url.split('/').at(-1))-1],history:[],pending_appeal:options.pendingAppeal});
    if(url.endsWith('/api/approvals/70')&&options.appeal)return options.appeal(route,s);
    if(url.endsWith('/push/check')){if(options.check)return options.check(route,body,s,url);s.gate=options.checkedGate||readyStudentGate();return json(route,s.dto());}
    if(url.endsWith('/push'))return options.read?options.read(route,body,s,url):json(route,s.dto());
    if(url.endsWith('/corrections'))return options.correct?options.correct(route,body,s,url):json(route,s.accept(body));
    if(url.endsWith('/retry')){if(options.retry)return options.retry(route,body,s,url);s.push={...s.push,state:'confirmed',can_retry:false,readback_only:false};return json(route,s.dto());}
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,true,true);
  if(options.dark)await page.evaluate(()=>{document.documentElement.dataset.appearance='dark';});await open(page,'teacher',false);s.parent=page.getByRole('dialog',{name:'自动点名',exact:true});s.card=s.parent.locator('.la-student-open').first();await s.card.click();s.dialog=page.getByRole('dialog',{name:'学生签到详情',exact:true});return s;
}

test('LQ single student local save is idempotent and does not dispatch a school or class command',async({page})=>{
  const s=await correctionFixture(page,{gate:{allowed:false,code:'alignment_required',message:'请先对齐课程与课次'}}),d=s.dialog;
  await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await expect(d.getByRole('button',{name:'保存',exact:true})).toBeEnabled();await d.getByLabel('改签理由').fill('教师现场复核，只保存本地');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByText('本地已保存。',{exact:true})).toBeVisible();
  const writes=s.calls.filter((c:any)=>c.method==='POST');expect(writes).toHaveLength(1);expect(writes[0].url).toBe('/api/classrooms/2/login-attendance/rounds/9/external/students/1/corrections');expect(writes[0].body).toMatchObject({mode:'local',status:'PRESENT',revision:1,reason:'教师现场复核，只保存本地'});expect(writes[0].body.expected_binding_revision).toBeUndefined();expect(writes[0].body.expected_remote_checkin_id).toBeUndefined();expect(s.current.records[1].revision).toBe(1);
  await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await expect(s.card).toBeFocused();await expect(s.card).toContainText('出勤');expect(s.errors).toEqual([]);
});

test('LQ single student delayed eligibility does not redraw drafts and permits ended school rollcall push',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);
  const s=await correctionFixture(page,{gate:{allowed:false,code:'checking',message:'正在核对'},check:async(route:Route,_body:any,s:any)=>{await wait;s.gate=readyStudentGate();return json(route,s.dto());}}),d=s.dialog;
  try{await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await expect(d.getByRole('button',{name:'保存',exact:true})).toBeEnabled();await choose(page,d,'出勤状态','病假');await d.getByLabel('改签理由').fill('老师核准病假，学校点名已经结束');release();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeEnabled();await expect(d.getByLabel('改签理由')).toHaveValue('老师核准病假，学校点名已经结束');
    await d.getByRole('button',{name:'保存并推送',exact:true}).click();await expect(d.getByText('本地已保存，智慧课堂已确认该学生为病假。',{exact:true})).toBeVisible();const saved=s.calls.find((c:any)=>c.url.endsWith('/corrections'));expect(saved.body).toMatchObject({mode:'push',status:'SICK_LEAVE',expected_binding_revision:5,expected_remote_checkin_id:'school-started-9'});expect(s.current.records[1].revision).toBe(1);expect(s.calls.some((c:any)=>c.url.endsWith('/external/sync'))).toBe(false);expect(s.errors).toEqual([]);
  }finally{release();}
});

test('LQ single student local success and school failure resumes the same intent without saving again',async({page})=>{
  const s=await correctionFixture(page,{pushState:'failed',pushMessage:'学校响应暂时超时',correct:(route:Route,body:any,s:any)=>{s.accept(body);s.gate={allowed:false,code:'checking'};return json(route,{...s.dto(),round:s.current});}}),d=s.dialog;await d.getByLabel('改签理由').fill('教师确认该生到课');await d.getByRole('button',{name:'保存并推送',exact:true}).click();await expect(d.getByRole('alert')).toContainText('本地已保存');await expect(d.getByRole('alert')).toContainText('学校响应暂时超时');await expect(d.getByLabel('改签理由')).toHaveValue('');await d.getByRole('button',{name:'重试推送',exact:true}).click();await expect(d.getByText('本地已保存，智慧课堂已确认该学生为出勤。',{exact:true})).toBeVisible();expect(s.saves).toBe(1);expect(s.calls.filter((c:any)=>c.url.endsWith('/push/check'))).toHaveLength(1);expect(s.calls.filter((c:any)=>c.url.endsWith('/corrections'))).toHaveLength(1);expect(s.calls.find((c:any)=>c.url.endsWith('/retry')).url).toBe('/api/classrooms/2/login-attendance/rounds/9/external/students/1/push/student-intent-1/retry');expect(s.errors).toEqual([]);
});

test('LQ single student queued task polls local status to confirmation while preserving a new draft',async({page})=>{
  const s=await correctionFixture(page,{pushState:'queued',correct:(route:Route,body:any,s:any)=>{s.accept(body);s.gate={allowed:false,code:'student_push_busy',message:'正在推送'};return json(route,{...s.dto(),round:s.current});},read:(route:Route,_body:any,s:any)=>{if(s.push?.state==='queued'){s.push={...s.push,state:'confirmed',is_current_decision:true};s.gate={allowed:false,code:'checking'};}return json(route,s.dto());}}),d=s.dialog;
  await d.getByLabel('改签理由').fill('后台确认此学生出勤');await d.getByRole('button',{name:'保存并推送',exact:true}).click();await expect(d.getByRole('button',{name:'正在推送',exact:true})).toBeDisabled();await expect(d.getByText(/正在后台推送当前学生/)).toBeVisible();await expect(d.getByRole('button',{name:'保存',exact:true})).toBeEnabled();await d.getByLabel('改签理由').fill('后台回读期间的新草稿');await expect(d.getByText('本地已保存，智慧课堂已确认该学生为出勤。',{exact:true})).toBeVisible({timeout:8000});await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeEnabled();await expect(d.getByLabel('改签理由')).toHaveValue('后台回读期间的新草稿');expect(s.calls.filter((c:any)=>c.url.endsWith('/corrections'))).toHaveLength(1);expect(s.calls.filter((c:any)=>c.url.endsWith('/push/check'))).toHaveLength(1);expect(s.calls.filter((c:any)=>c.url.endsWith('/push')).length).toBeGreaterThan(1);expect(s.errors).toEqual([]);
});

test('LQ single student local save automatically rechecks eligibility without replacing the next draft',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);
  const s=await correctionFixture(page,{correct:(route:Route,body:any,s:any)=>{s.accept(body);if(body.mode==='local')s.gate={allowed:false,code:'checking'};return json(route,{...s.dto(),round:s.current});},check:async(route:Route,_body:any,s:any)=>{await wait;s.gate=readyStudentGate();return json(route,s.dto());}}),d=s.dialog;
  try{await d.getByLabel('改签理由').fill('先保存出勤');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByText('本地已保存。',{exact:true})).toBeVisible();await expect.poll(()=>s.calls.filter((c:any)=>c.url.endsWith('/push/check')).length).toBe(1);await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await choose(page,d,'出勤状态','病假');await d.getByLabel('改签理由').fill('随后补充学校病假');release();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeEnabled();await expect(d.getByLabel('改签理由')).toHaveValue('随后补充学校病假');await d.getByRole('button',{name:'保存并推送',exact:true}).click();await expect(d.getByText('本地已保存，智慧课堂已确认该学生为病假。',{exact:true})).toBeVisible();const writes=s.calls.filter((c:any)=>c.url.endsWith('/corrections'));expect(writes.map((c:any)=>c.body.mode)).toEqual(['local','push']);expect(writes[1].body.revision).toBe(2);expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key);expect(s.errors).toEqual([]);}finally{release();}
});

for(const failsOnce of [false,true])test(`LQ single student correction refreshes resolved appeal without replacing the next draft${failsOnce?' after a read failure':''}`,async({page})=>{
  let release!:()=>void,reads=0;const wait=new Promise<void>(resolve=>release=resolve);
  const s=await correctionFixture(page,{pendingAppeal:{id:70,status:'pending'},appeal:async(route:Route)=>{reads++;if(reads===1)return json(route,{request:{id:70,status:'pending',status_label:'待处理',reason:'学生说明',can_decide:true}});if(reads===2){await wait;if(failsOnce)return json(route,{detail:'申诉读取暂不可用'},503);}return json(route,{request:{id:70,status:'cancelled',status_label:'已取消',reason:'教师已直接改签',can_decide:false}});}}),d=s.dialog;
  try{await d.getByLabel('处理说明').fill('旧申诉草稿');await d.getByLabel('改签理由').fill('教师直接核准出勤');await d.getByLabel('改签理由').evaluate((n:Element)=>(window as any).savedCorrectionNode=n);await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByText('本地已保存。',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'按所选状态确认更正',exact:true})).toHaveCount(0);await d.getByLabel('改签理由').fill('下一份草稿保留');release();if(failsOnce){await expect(d.locator('.la-appeal').getByRole('alert')).toContainText('改签已保存');await d.getByRole('button',{name:'重新核对申诉状态',exact:true}).click();}await expect(d.locator('.la-appeal')).toContainText('已取消');await expect(d.getByLabel('处理说明')).toHaveCount(0);await expect(d.getByLabel('改签理由')).toHaveValue('下一份草稿保留');expect(await d.getByLabel('改签理由').evaluate((n:Element)=>n===(window as any).savedCorrectionNode)).toBe(true);expect(s.saves).toBe(1);expect(s.calls.some((c:any)=>/\/70\/(approve|reject)$/.test(c.url))).toBe(false);expect(s.errors).toEqual([]);}finally{release();}
});

test('LQ single student saved correction clears a resolved appeal draft from parent close confirmation',async({page})=>{
  const s=await correctionFixture(page,{pendingAppeal:{id:70,status:'pending'},appeal:(route:Route,s:any)=>json(route,{request:{id:70,status:s.saves?'cancelled':'pending',status_label:s.saves?'已取消':'待处理',reason:'学生说明',can_decide:!s.saves}})}),d=s.dialog;
  await d.getByLabel('处理说明').fill('不再需要的申诉草稿');await d.getByLabel('改签理由').fill('直接核准本地出勤');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.locator('.la-appeal')).toContainText('已取消');await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await page.keyboard.press('Escape');await expect(s.parent).not.toBeVisible();await expect(page.getByRole('dialog',{name:'关闭并放弃未提交内容？',exact:true})).toHaveCount(0);expect(s.errors).toEqual([]);
});

for(const statusCode of [409,422,403])test(`LQ single student explicit ${statusCode} rejection preserves drafts and never claims an uncertain save`,async({page})=>{
  let attempts=0;const s=await correctionFixture(page,{correct:(route:Route,body:any,s:any)=>{attempts++;return attempts===1?json(route,{detail:{code:'rejected',message:'记录或权限已变化'}},statusCode):json(route,s.accept(body));}}),d=s.dialog;
  await d.getByLabel('改签理由').fill('保留未保存的说明');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByRole('alert')).toContainText('本次修改未保存');await expect(d.getByRole('alert')).not.toContainText('保存结果未确认');await expect(d.getByLabel('改签理由')).toHaveValue('保留未保存的说明');expect(s.saves).toBe(0);await d.getByRole('button',{name:'重新读取最新记录',exact:true}).click();await expect(d.getByLabel('改签理由')).toHaveValue('保留未保存的说明');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByText('本地已保存。',{exact:true})).toBeVisible();const writes=s.calls.filter((c:any)=>c.url.endsWith('/corrections'));expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key);expect(s.errors).toEqual([]);
});

for(const mode of ['local','push'])test(`LQ single student uncertain ${mode} response restores the original payload after rereading`,async({page})=>{
  let attempts=0,accepted:any;const s=await correctionFixture(page,{correct:async(route:Route,body:any,s:any)=>{attempts++;if(attempts===1){accepted=s.accept(body);return route.abort('failed');}return json(route,accepted);}}),d=s.dialog;
  await d.getByLabel('改签理由').fill('响应丢失后恢复同一改签');await d.getByRole('button',{name:mode==='local'?'保存':'保存并推送',exact:true}).click();await expect(d.getByRole('alert')).toContainText('保存结果未确认');await expect(d.getByLabel('改签理由')).toHaveValue('响应丢失后恢复同一改签');await d.getByRole('button',{name:'重新读取最新记录',exact:true}).click();await expect(d.getByLabel('改签理由')).toHaveValue('响应丢失后恢复同一改签');await d.getByRole('button',{name:mode==='local'?'保存':'确认保存并推送结果',exact:true}).click();await expect(d.getByLabel('改签理由')).toHaveValue('');const calls=s.calls.filter((c:any)=>c.url.endsWith('/corrections'));expect(calls).toHaveLength(2);expect(calls[1].body).toEqual(calls[0].body);expect(calls[1].body.revision).toBe(1);expect(s.saves).toBe(1);expect(s.errors).toEqual([]);
});

test('LQ single student unknown outcome permits readback-only recovery despite a blocked new-push gate',async({page})=>{
  const current={...round(2),state:'published'};current.records[0]={...current.records[0],status:'PRESENT',status_label:'出勤',revision:3};
  const s=await correctionFixture(page,{current,gate:{allowed:false,code:'alignment_required',message:'对齐关系已变化'},push:{id:'unknown-receipt',state:'outcome_unknown',message:'仅核对学校结果',local_revision:3,target_status:'PRESENT',can_retry:true,readback_only:true}}),d=s.dialog;
  await expect(d.getByRole('button',{name:'核对推送结果',exact:true})).toBeEnabled();await d.getByRole('button',{name:'核对推送结果',exact:true}).click();await expect(d.getByText('本地已保存，智慧课堂已确认该学生为出勤。',{exact:true})).toBeVisible();expect(s.calls.filter((c:any)=>c.method==='POST').map((c:any)=>c.url)).toEqual(['/api/classrooms/2/login-attendance/rounds/9/external/students/1/push/unknown-receipt/retry']);expect(s.saves).toBe(0);expect(s.errors).toEqual([]);
});

test('LQ single student old unknown readback preserves a newer local decision and unsaved draft',async({page})=>{
  const current={...round(2),state:'published'};current.records[0]={...current.records[0],status:'SICK_LEAVE',status_label:'病假',revision:5};
  const s=await correctionFixture(page,{current,gate:{allowed:false,code:'alignment_required',message:'对齐关系已变化'},push:{id:'old-unknown',state:'outcome_unknown',local_revision:2,target_status:'PRESENT',can_retry:true,readback_only:true,is_current_decision:false}}),d=s.dialog;
  await choose(page,d,'出勤状态','事假');await d.getByLabel('改签理由').fill('新草稿必须保留');await expect(d.getByRole('button',{name:'核对推送结果',exact:true})).toBeEnabled();await d.getByRole('button',{name:'核对推送结果',exact:true}).click();await expect(d.getByText('本地记录已更新，此前推送不代表当前状态。',{exact:true})).toBeVisible();await expect(d.getByLabel('改签理由')).toHaveValue('新草稿必须保留');expect(s.current.records[0].revision).toBe(5);expect(s.saves).toBe(0);expect(s.calls.filter((c:any)=>c.url.endsWith('/retry'))).toHaveLength(1);expect(s.errors).toEqual([]);
});

test('LQ single student local-only correction keeps the old unknown intent available for readback',async({page})=>{
  const current={...round(2),state:'published'};current.records[0]={...current.records[0],status:'PRESENT',status_label:'出勤',revision:2};
  const s=await correctionFixture(page,{current,gate:{allowed:false,code:'alignment_required',message:'对齐关系已变化'},push:{id:'old-unknown',state:'outcome_unknown',local_revision:2,target_status:'PRESENT',can_retry:true,readback_only:true,is_current_decision:true},correct:(route:Route,body:any,s:any)=>{const old=s.push;s.accept(body);s.push={...old,is_current_decision:false};return json(route,{...s.dto(),round:s.current});}}),d=s.dialog;
  await choose(page,d,'出勤状态','病假');await d.getByLabel('改签理由').fill('仅修正本地，保留旧结果核对');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByLabel('改签理由')).toHaveValue('');await expect(d.getByRole('button',{name:'核对推送结果',exact:true})).toBeEnabled();await expect(d.getByText(/上次推送目标为出勤/)).toBeVisible();await d.getByRole('button',{name:'核对推送结果',exact:true}).click();await expect(d.getByText('本地记录已更新，此前推送不代表当前状态。',{exact:true})).toBeVisible();expect(s.current.records[0]).toMatchObject({revision:3,status:'SICK_LEAVE'});expect(s.saves).toBe(1);expect(s.calls.filter((c:any)=>c.method==='POST').map((c:any)=>c.url.split('/').at(-1))).toEqual(['corrections','retry']);expect(s.errors).toEqual([]);
});

test('LQ single student pinned decision survives evidence revisions and automatically rechecks school readiness',async({page})=>{
  const current={...round(2),state:'published'};current.records[0]={...current.records[0],status:'PRESENT',status_label:'出勤',revision:8};
  const s=await correctionFixture(page,{current,push:{id:'pinned-decision',state:'failed',local_revision:2,target_status:'PRESENT',can_retry:true,readback_only:false,is_current_decision:true},retry:(route:Route,_body:any,s:any)=>{s.push={...s.push,state:'confirmed',can_retry:false};s.gate={allowed:false,code:'checking',message:'需要刷新学校状态'};return json(route,s.dto());}}),d=s.dialog;
  await expect(d.getByRole('button',{name:'重试推送',exact:true})).toBeEnabled();await d.getByRole('button',{name:'重试推送',exact:true}).click();await expect(d.getByText('本地已保存，智慧课堂已确认该学生为出勤。',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeEnabled();expect(s.calls.filter((c:any)=>c.url.endsWith('/push/check'))).toHaveLength(1);expect(s.saves).toBe(0);expect(s.errors).toEqual([]);
});

for(const code of ['alignment_required','school_not_started'])test(`LQ single student ${code} keeps local save available and blocks school writes`,async({page})=>{
  const gate={allowed:false,code,message:code==='alignment_required'?'请先对齐课程和课次':'学校尚未开始本次点名'};const s=await correctionFixture(page,{gate,checkedGate:gate}),d=s.dialog;
  await expect(d.getByText(gate.message,{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await expect(d.getByRole('button',{name:'保存',exact:true})).toBeEnabled();expect(s.calls.some((c:any)=>c.url.endsWith('/corrections'))).toBe(false);expect(s.errors).toEqual([]);
});

test('LQ single student busy save prevents duplicate submission and close, then returns focus',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);const s=await correctionFixture(page,{correct:async(route:Route,body:any,s:any)=>{await wait;return json(route,s.accept(body));}}),d=s.dialog;
  try{await d.getByLabel('改签理由').fill('等待保存完成');await d.getByRole('button',{name:'保存',exact:true}).click();await expect(d.getByRole('button',{name:'保存',exact:true})).toBeDisabled();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await page.keyboard.press('Escape');await expect(d).toBeVisible();expect(s.calls.filter((c:any)=>c.url.endsWith('/corrections'))).toHaveLength(1);release();await expect(d.getByLabel('改签理由')).toHaveValue('');await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await expect(s.card).toBeFocused();expect(s.errors).toEqual([]);}finally{release();}
});

test('LQ single student old eligibility cannot unlock a new student after close and reopen',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);const s=await correctionFixture(page,{gate:{allowed:false,code:'checking'},read:(route:Route,_body:any,s:any,url:string)=>json(route,{...s.dto(),gate:url.includes('/students/2/')?{allowed:false,code:'alignment_required',message:'第二位学生尚未对齐'}:{allowed:false,code:'checking'}}),check:async(route:Route,_body:any,s:any)=>{await wait;return json(route,{...s.dto(),gate:readyStudentGate()});}});
  try{await expect.poll(()=>s.calls.some((c:any)=>c.url.endsWith('/push/check'))).toBe(true);await page.keyboard.press('Escape');await expect(s.dialog).not.toBeVisible();await s.parent.locator('.la-student-open').nth(1).click();const d=page.getByRole('dialog',{name:'学生签到详情',exact:true});await expect(d.getByText('第二位学生尚未对齐',{exact:true})).toBeVisible();release();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeDisabled();await expect(d.getByRole('heading',{name:'合成同学2 · 本次签到'})).toBeVisible();expect(s.errors).toEqual([]);}finally{release();}
});

for(const dark of [false,true])for(const width of [390,1440])test(`LQ single student correction ${dark?'dark':'light'} ${width}px keeps both actions readable`,async({page})=>{
  await page.setViewportSize({width,height:980});const s=await correctionFixture(page,{dark}),d=s.dialog;
  await expect(d.getByRole('button',{name:'保存',exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'保存并推送',exact:true})).toBeEnabled();expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await expect.poll(()=>d.evaluate(n=>n.closest('[data-lq-layer-state]')?.getAttribute('data-lq-layer-state'))).toBe('open');await page.screenshot({path:`.codex-temp/evidence/attendance-student-push-20261112/student-${dark?'dark':'light'}-${width}.png`});expect(s.errors).toEqual([]);
});

function alignmentWorkflow(stage='course') {
  const localCourse={course_name:'计算机网络原理',course_code:'E030054B1',academic_year:'2026-2027',academic_term:1,student_count:42,session_count:32,total_hours:64,credits:4};
  const localSession={session_date:'2026-11-09',week:11,day_of_week:1,sections:[1,2],order_index:17};
  const roster={local_count:42,remote_count:43,matched_count:41,local_only_count:1,remote_only_count:2,difference_count:3,majority:true};
  const courseCandidates=[{id:'course-good',label:'计算机网络原理 · 计科2606',eligible:true,reasons:['相同名单占多数，差异 3 人可正常对齐。'],evidence:{roster,method:'roster'},target:{...localCourse,student_count:43}},
    {id:'course-other',label:'计算机网络原理 · 计科2607',eligible:false,reasons:['课程相同，但本班名单不对应。'],evidence:{roster:{...roster,matched_count:1,difference_count:83,majority:false}},target:{...localCourse,student_count:43}}];
  const sessionCandidates=[{id:'lesson-a',label:'11月9日 第1、2节',eligible:true,reasons:['与本地日期、星期、节次一致。'],evidence:{method:'schedule'},target:{...localSession,remote_checkin_id:'school-a'}},
    {id:'lesson-b',label:'11月9日 第3、4节',eligible:true,reasons:['同日有多次课堂，请核对节次。'],evidence:{method:'schedule'},target:{...localSession,sections:[3,4],order_index:18,remote_checkin_id:'school-b'}}];
  return {binding:{state:stage==='matched'?'matched':'unmatched',revision:stage==='matched'?6:5,diagnostics:[],target:stage==='matched'?{...localCourse,...localSession}:null},plan:null,can_sync:stage==='matched',
    alignment:{needs_review:stage!=='matched',course:{state:stage==='course'?'review':'matched',revision:stage==='course'?7:8,local:localCourse,selected_id:stage==='course'?null:'course-good',candidates:courseCandidates,message:'核对真实课程与学校名单'},session:{state:stage==='course'?'unmatched':stage==='matched'?'matched':'review',revision:5,local:localSession,selected_id:stage==='matched'?'lesson-a':null,candidates:stage==='course'?[]:sessionCandidates,message:'同日有两次课堂，需要确认本课节次'}}};
}

test('LQ alignment gates pushes by a real binding and preserves settlement requirements',async({page})=>{
  let view:any={...alignmentWorkflow(),can_sync:true};const current={...round(2),state:'published'};
  await fixture(page,async(route,url)=>url.endsWith('/external/workflow')?json(route,view):json(route,{session,policy:current.policy,round:current}),'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true}),align=d.getByRole('button',{name:'对齐课次',exact:true}),push=d.getByRole('button',{name:'同步与推送',exact:true});
  await expect(align).toHaveAttribute('data-lq-action-tone','warning');await expect(d.getByText('未对齐',{exact:true})).toBeVisible();await expect(push).toBeDisabled();await expect(push).toHaveAttribute('aria-disabled','true');
  view=alignmentWorkflow('matched');current.state='running';await page.evaluate(()=>(window as any).attendance.refresh());await expect(align).toHaveAttribute('data-lq-action-tone','success');await expect(push).toBeDisabled();
  current.state='published';await page.evaluate(()=>(window as any).attendance.refresh());await expect(push).toBeEnabled();await expect(push).toHaveAttribute('data-lq-disabled','false');
});

test('LQ alignment reviews course then lesson cards and submits only server candidate identities',async({page})=>{
  let view=alignmentWorkflow();const calls:any[]=[];const current={...round(2),state:'published'};
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/external/workflow'))return json(route,view);
    if(url.endsWith('/external/confirm')){calls.push(body);view=alignmentWorkflow(body.scope==='course'?'session':'matched');return json(route,view);}
    return json(route,{session,policy:current.policy,round:current});
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});
  await expect(review.getByText('E030054B1',{exact:true}).first()).toBeVisible();await expect(review.getByText('64 学时 · 4 学分',{exact:true}).first()).toBeVisible();await expect(review.getByText('差异 3 人（仅本地 1 人，仅学校 2 人）',{exact:true})).toBeVisible();
  await expect(review.getByRole('button',{name:'2 · 课次',exact:true})).toBeDisabled();await expect(review.getByRole('button',{name:'确认课程',exact:true})).toBeDisabled();await expect(review.getByRole('button',{name:'选择课程：计算机网络原理 · 计科2607',exact:true})).toBeDisabled();
  await review.getByRole('button',{name:'选择课程：计算机网络原理 · 计科2606',exact:true}).click();await review.getByRole('button',{name:'确认课程',exact:true}).click();
  await expect(review.getByRole('heading',{name:'本地课次',exact:true})).toBeVisible();await expect(review.getByText('第 11 周',{exact:true}).first()).toBeVisible();await expect(review.getByText('星期一',{exact:true}).first()).toBeVisible();await expect(review.getByText('第 17 次课',{exact:true}).first()).toBeVisible();
  await review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true}).click();await review.getByRole('button',{name:'确认课次',exact:true}).click();await expect(review).not.toBeVisible();await expect(d.getByRole('button',{name:'对齐课次',exact:true})).toBeFocused();await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeEnabled();
  expect(calls).toHaveLength(2);expect(calls[0]).toMatchObject({scope:'course',candidate_id:'course-good',expected_course_revision:7,expected_binding_revision:5});expect(calls[1]).toMatchObject({scope:'session',candidate_id:'lesson-a',expected_course_revision:8,expected_binding_revision:5});expect(Object.keys(calls[0]).sort()).toEqual(['candidate_id','expected_binding_revision','expected_course_revision','idempotency_key','scope']);expect(errors).toEqual([]);
});

test('LQ alignment retains selection and idempotency after uncertain response or revision conflict',async({page})=>{
  const view=alignmentWorkflow('session'),keys:string[]=[];const current={...round(2),state:'published'};
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/external/workflow'))return json(route,view);
    if(url.endsWith('/external/confirm')){keys.push(body.idempotency_key);if(keys.length===1)return route.abort('failed');return json(route,{detail:'学校课次已变化，请重新对齐；本地记录完整保留。'},409);}
    return json(route,{session,policy:current.policy,round:current});
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true}),choice=review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true}),save=review.getByRole('button',{name:'确认课次',exact:true});
  await choice.click();await save.click();await expect(review.getByRole('alert')).toBeVisible();await expect(choice).toHaveAttribute('aria-pressed','true');await choice.click();await save.click();await expect(review.getByRole('alert')).toContainText('请重新对齐');await expect(choice).toHaveAttribute('aria-pressed','true');expect(keys[0]).toBe(keys[1]);
  await page.keyboard.press('Escape');await expect(review).not.toBeVisible();await expect(d.getByRole('button',{name:'存疑',exact:true})).toBeFocused();expect(errors).toEqual([]);
});

test('LQ alignment persisted account codes expose the correct recovery and missing lesson metadata stays reviewable',async({page})=>{
  const view:any=alignmentWorkflow('session');view.alignment.course.error_code='missing_credential';view.alignment.session.local=null;const current={...round(2),state:'published'};
  const errors=await fixture(page,async(route,url)=>url.endsWith('/external/workflow')?json(route,view):json(route,{session,policy:current.policy,round:current}),'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).toBeVisible();await expect(d.getByRole('link',{name:'完成学校验证码'})).not.toBeVisible();
  view.alignment.course.error_code='captcha_required';await page.evaluate(()=>(window as any).attendance.refresh());await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).not.toBeVisible();await expect(d.getByRole('link',{name:'完成学校验证码'})).toBeVisible();
  await d.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});await expect(review.getByRole('heading',{name:'本地课次',exact:true})).toBeVisible();await expect(review.getByText('未提供',{exact:true}).first()).toBeVisible();await review.getByRole('button',{name:'暂不确认',exact:true}).click();await expect(review).not.toBeVisible();expect(errors).toEqual([]);
});

test('LQ alignment missing candidates show the persisted detailed cause without blocking local recording',async({page})=>{
  const view:any=alignmentWorkflow();view.alignment.needs_review=false;view.alignment.course.state='unmatched';view.alignment.course.candidates=[];view.alignment.course.message='学校当前学期为2025-2026，本地为2026-2027；请核对学校课表后重新对齐。';view.message='课程尚未对齐；本地记录不受影响。';
  const errors=await fixture(page,async(route,url)=>url.endsWith('/external/workflow')?json(route,view):json(route,{session,policy:{revision:0,networks:['192.0.2.0/24']},window:{starts_at_ms:Date.parse('2026-11-09T08:30:00+08:00'),ends_at_ms:Date.parse('2026-11-09T10:00:00+08:00')},round:null}),'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.getByText(view.alignment.course.message,{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'存疑',exact:true})).not.toBeVisible();await expect(d.getByRole('button',{name:'开始记录',exact:true})).toBeEnabled();await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeDisabled();expect(errors).toEqual([]);
});

test('LQ alignment busy confirmation blocks dismiss and duplicates then allows cancellation',async({page})=>{
  let release!:()=>void;const wait=new Promise<void>(resolve=>release=resolve);let calls=0;const current={...round(2),state:'published'};
  await fixture(page,async(route,url)=>{
    if(url.endsWith('/external/workflow'))return json(route,alignmentWorkflow('session'));
    if(url.endsWith('/external/confirm')){calls++;await wait;return json(route,{detail:'本课同步任务正在处理，请稍后重试'},409);}
    return json(route,{session,policy:current.policy,round:current});
  },'teacher','',undefined,true);
  try{await open(page,'teacher',false);await page.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});await review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true}).click();await review.getByRole('button',{name:'确认课次',exact:true}).click();await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeDisabled();await expect(review.getByRole('button',{name:'重新对齐',exact:true})).toBeDisabled();await page.keyboard.press('Escape');await expect(review).toBeVisible();expect(calls).toBe(1);release();await expect(review.getByRole('alert')).toContainText('正在处理');await review.getByRole('button',{name:'暂不确认',exact:true}).click();await expect(review).not.toBeVisible();}finally{release();}
});

test('LQ alignment refresh discards vanished candidates and never silently confirms a replacement',async({page})=>{
  let view:any=alignmentWorkflow('session'),confirmCalls=0;const current={...round(2),state:'published'};
  await fixture(page,async(route,url)=>{
    if(url.endsWith('/external/workflow'))return json(route,view);
    if(url.endsWith('/external/match')){view=alignmentWorkflow('session');view.alignment.session.candidates=[{...view.alignment.session.candidates[0],id:'new-lesson'}];return json(route,view);}
    if(url.endsWith('/external/confirm')){confirmCalls++;return json(route,view);}
    return json(route,{session,policy:current.policy,round:current});
  },'teacher','',undefined,true);
  await open(page,'teacher',false);await page.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});await review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true}).click();await review.getByRole('button',{name:'重新对齐',exact:true}).click();await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeDisabled();await expect(review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true})).toHaveAttribute('aria-pressed','false');await review.getByRole('button',{name:'暂不确认',exact:true}).click();expect(confirmCalls).toBe(0);
});

for(const stale of [false,true])test(`LQ alignment ${stale?'stale':'expired'} persisted candidates stay readable but cannot be confirmed`,async({page})=>{
  let view:any=alignmentWorkflow('session'),confirmCalls=0;const current={...round(2),state:'published'};
  view.alignment.session.selected_id='lesson-a';if(stale)view.alignment.session.state='stale';else view.alignment.session.expires_at_ms=1;
  const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/external/workflow'))return json(route,view);
    if(url.endsWith('/external/match')){view=alignmentWorkflow('session');return json(route,view);}
    if(url.endsWith('/external/confirm')){confirmCalls++;return json(route,view);}
    return json(route,{session,policy:current.policy,round:current});
  },'teacher','',undefined,true);
  await open(page,'teacher',false);await page.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true}),choice=review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true});
  await expect(review.getByRole('status')).toContainText(stale?'信息已变化，请重新对齐':'候选已过期，请重新对齐');await expect(choice).toBeVisible();await expect(choice).toBeDisabled();await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeDisabled();
  await review.getByRole('button',{name:'重新对齐',exact:true}).click();await expect(choice).toBeEnabled();await choice.click();await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeEnabled();expect(confirmCalls).toBe(0);expect(errors).toEqual([]);
});

test('LQ alignment rechecks candidate expiry at confirmation without a polling timer',async({page})=>{
  await page.clock.setFixedTime(new Date('2026-11-12T01:00:00Z'));const view:any=alignmentWorkflow('session');view.alignment.session.expires_at_ms=Date.parse('2026-11-12T01:01:00Z');let confirmCalls=0;const current={...round(2),state:'published'};
  const errors=await fixture(page,async(route,url)=>{if(url.endsWith('/external/workflow'))return json(route,view);if(url.endsWith('/external/confirm')){confirmCalls++;return json(route,view);}return json(route,{session,policy:current.policy,round:current});},'teacher','',undefined,true);
  await open(page,'teacher',false);await page.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});await review.getByRole('button',{name:'选择课次：11月9日 第1、2节',exact:true}).click();await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeEnabled();await page.clock.setFixedTime(new Date('2026-11-12T01:02:00Z'));await review.getByRole('button',{name:'确认课次',exact:true}).click();await expect(review.getByRole('status')).toContainText('候选已过期，请重新对齐');await expect(review.getByRole('button',{name:'确认课次',exact:true})).toBeDisabled();await expect(review.getByRole('button',{name:'重新对齐',exact:true})).toBeEnabled();expect(confirmCalls).toBe(0);expect(errors).toEqual([]);
});

for(const dark of [false,true])for(const width of [390,1440])test(`LQ alignment ${dark?'dark':'light'} ${width}px uses readable comparison cards`,async({page})=>{
  await page.setViewportSize({width,height:980});const current={...round(2),state:'published'};let view=alignmentWorkflow();
  const errors=await fixture(page,async(route,url)=>url.endsWith('/external/workflow')?json(route,view):json(route,{session,policy:current.policy,round:current}),'teacher','',undefined,true);
  if(dark)await page.evaluate(()=>{document.documentElement.dataset.appearance='dark';});await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});
  const tones:any={};const readTone=async(tone:string)=>d.getByRole('button',{name:'对齐课次',exact:true}).evaluate((node,expectedTone)=>{const style=getComputedStyle(node),probe=document.createElement('span');probe.style.color=`hsl(var(--ls-tone-${expectedTone}-fg))`;node.append(probe);const expectedColor=getComputedStyle(probe).color;probe.remove();return {color:style.color,background:style.backgroundImage,expectedColor};},tone);
  tones.warning=await readTone('warning');tones.disabled=await d.getByRole('button',{name:'同步与推送',exact:true}).evaluate(n=>({background:getComputedStyle(n).backgroundImage,color:getComputedStyle(n).color}));
  if(builtManifest){expect(tones.warning.color).toBe(tones.warning.expectedColor);expect(tones.warning.background).toContain('linear-gradient');expect(tones.disabled.background).toBe('none');}
  await page.screenshot({path:`.codex-temp/evidence/attendance-alignment-20261112/main-unmatched-${dark?'dark':'light'}-${width}.png`});
  await d.getByRole('button',{name:'存疑',exact:true}).click();const review=page.getByRole('dialog',{name:'存疑课次核对',exact:true});await expect(review.getByRole('button',{name:'选择课程：计算机网络原理 · 计科2606',exact:true})).toBeVisible();expect(await review.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);
  const columns=await review.locator('.la-alignment-column').evaluateAll(nodes=>nodes.map(n=>({x:n.getBoundingClientRect().x,y:n.getBoundingClientRect().y})));expect(width>640?columns[1].x>columns[0].x:columns[1].y>columns[0].y).toBe(true);
  await page.screenshot({path:`.codex-temp/evidence/attendance-alignment-20261112/alignment-${dark?'dark':'light'}-${width}.png`});await review.getByRole('button',{name:'暂不确认',exact:true}).click();await expect(review).not.toBeVisible();view=alignmentWorkflow('matched');await page.evaluate(()=>(window as any).attendance.refresh());tones.success=await readTone('success');
  if(builtManifest){expect(tones.success.color).toBe(tones.success.expectedColor);expect(tones.success.background).not.toBe(tones.warning.background);expect(tones.success.color).not.toBe(tones.warning.color);}
  await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeEnabled();await page.screenshot({path:`.codex-temp/evidence/attendance-alignment-20261112/main-matched-${dark?'dark':'light'}-${width}.png`});fs.writeFileSync(`.codex-temp/evidence/attendance-alignment-20261112/tones-${dark?'dark':'light'}-${width}.json`,JSON.stringify({graph:builtManifest?.revision||null,...tones},null,2));expect(errors).toEqual([]);
});

test('LQ streamlined attendance keeps unmatched recording local, then binds and submits a durable sync with stable retry key',async({page})=>{
  let current:any=null, matched=false, submitted=0;const calls:any[]=[];
  const workflow=()=>({binding:{state:matched?'matched':'unmatched',revision:matched?2:0,target:matched?{course_name:'合成课程',teaching_class_name:'合成班',session_date:'2026-11-09',section_text:'2、3节'}:null,message:matched?'已对应学校课次':'未找到同日期课次，本地点名不受影响',diagnostics:matched?[]:['学校课表尚未同步本次调课，请稍后重试。'],needs_location:false},can_sync:matched&&current?.state==='published',plan:submitted>1?{state:'pending',progress:{percent:10,completed:0,total:2,stage:'reading_remote'},message:'后台开始读取学校记录'}:null});
  const errors=await fixture(page,async(route,url,body)=>{
    calls.push({url,body});
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:{revision:4,networks:['192.0.2.0/24']},window:{starts_at_ms:Date.parse('2026-11-09T08:30:00+08:00'),ends_at_ms:Date.parse('2026-11-09T10:00:00+08:00')},round:current});
    if(url.endsWith('/external/workflow'))return json(route,workflow());
    if(url.endsWith('/external/match')){matched=current?.state==='published';return json(route,workflow());}
    if(url.endsWith('/sessions/12/rounds')){current={...round(2),state:'running'};return json(route,current);}
    if(url.endsWith('/rounds/9'))return json(route,current);
    if(url.endsWith('/external/sync')){submitted++;return submitted===1?json(route,{detail:'合成网络短暂故障'},503):json(route,workflow(),202);}
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});
  await expect(d.getByLabel('预计记录时长（分钟）')).toHaveValue('90');
  await expect(d.getByRole('switch',{name:'限定课堂网络'})).not.toBeChecked();
  await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeDisabled();
  await d.getByRole('button',{name:'对齐课次',exact:true}).click();await expect(d.getByText('未找到同日期课次，本地点名不受影响')).toBeVisible();
  await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.locator('[data-student-id]')).toHaveCount(2);
  expect(calls.find(x=>x.url.endsWith('/sessions/12/rounds')).body).toMatchObject({enforce_network:false,duration_minutes:90,policy_revision:4});
  current.state='published';await page.evaluate(()=>(window as any).attendance.refresh());await d.getByRole('button',{name:'对齐课次',exact:true}).click();
  await expect(d.getByText('已对齐',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeEnabled();
  await d.getByRole('button',{name:'同步与推送',exact:true}).click();await expect(d.getByRole('alert')).toContainText('合成网络短暂故障');
  await d.getByRole('button',{name:'同步与推送',exact:true}).click();await expect(d.getByRole('progressbar')).toHaveAttribute('value','10');
  const syncCalls=calls.filter(x=>x.url.endsWith('/external/sync'));expect(syncCalls[0].body).toEqual(syncCalls[1].body);expect(syncCalls[0].body.expected_binding_revision).toBe(2);
  await page.keyboard.press('Escape');await expect(d).not.toBeVisible();expect(errors).toEqual([]);
});

test('LQ compact student card opens a nested editable detail, preserves conflict draft and returns focus',async({page})=>{
  const current={...round(2),state:'published',records:round(2).records.map(r=>({...r,revision:1}))};let saves=0;const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/students/1'))return json(route,{record:current.records[0],history:[]});
    if(url.endsWith('/corrections')){saves++;if(saves===1)return json(route,{detail:'版本已变化，请重新读取'},409);current.records[0]={...current.records[0],status:body.status,status_label:'出勤',revision:2};return json(route,{local_saved:true,record:current.records[0],round:current,push:null});}
    return json(route,{detail:'unexpected '+url},500);
  });
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});const card=d.locator('.la-student-open').first();await card.click();const student=page.getByRole('dialog',{name:'学生签到详情',exact:true});
  await choose(page,student,'出勤状态','出勤');await student.getByLabel('改签理由').fill('教师现场核对出勤');await student.getByRole('button',{name:'保存',exact:true}).click();await expect(student.getByRole('alert')).toContainText('版本已变化');await expect(student.getByLabel('改签理由')).toHaveValue('教师现场核对出勤');
  await page.keyboard.press('Escape');const confirm=page.getByRole('dialog',{name:'放弃本次改签草稿？'});await expect(confirm).toBeVisible();await confirm.getByRole('button',{name:'取消',exact:true}).click();
  await student.getByRole('button',{name:'重新读取最新记录'}).click();await expect(student.getByLabel('改签理由')).toHaveValue('教师现场核对出勤');await student.getByRole('button',{name:'保存',exact:true}).click();await expect(student.getByLabel('改签理由')).toHaveValue('');await page.keyboard.press('Escape');await expect(student).not.toBeVisible();await expect(card).toBeFocused();await expect(card).toContainText('出勤');expect(errors).toEqual([]);
});

test('LQ school account errors keep recording available and only missing credentials expose account setup',async({page})=>{
  let code='missing_credential';const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:{revision:0,networks:['192.0.2.0/24']},window:{starts_at_ms:Date.parse('2026-11-09T08:30:00+08:00'),ends_at_ms:Date.parse('2026-11-09T10:00:00+08:00')},round:null});
    if(url.endsWith('/external/workflow'))return json(route,{binding:{state:'unmatched',revision:0,diagnostics:[]},can_sync:false,plan:null});
    if(url.endsWith('/external/match'))return json(route,{detail:{code,message:code==='missing_credential'?'请先配置学校账号':code==='captcha_required'?'学校要求先完成验证码':'正在自动恢复学校登录，请稍后重试'}},409);
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});
  await d.getByRole('button',{name:'对齐课次',exact:true}).click();await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).toBeVisible();await expect(d.getByRole('button',{name:'开始记录',exact:true})).toBeEnabled();
  code='captcha_required';await d.getByRole('button',{name:'对齐课次',exact:true}).click();await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).not.toBeVisible();await expect(d.getByRole('link',{name:'完成学校验证码'})).toHaveAttribute('href','https://edu.gxufl.edu.cn/login');
  code='needs_teacher_reauth';await d.getByRole('button',{name:'对齐课次',exact:true}).click();await expect(d.getByRole('link',{name:'完成学校验证码'})).not.toBeVisible();await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).not.toBeVisible();await expect(d.getByRole('button',{name:'开始记录',exact:true})).toBeEnabled();expect(errors).toEqual([]);
});

test('LQ new school checkin requests location only after dispatch preflight and denial never queues a remote command',async({page})=>{
  const current={...round(2),state:'published'};let syncRequests=0;
  await page.addInitScript(()=>Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:(_success:any,fail:any)=>fail({code:1})},configurable:true}));
  const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/external/workflow'))return json(route,{binding:{state:'matched',revision:1,needs_location:true,target:{course_name:'合成课程'},diagnostics:[]},can_sync:true,plan:null});
    if(url.endsWith('/external/sync')){syncRequests++;return json(route,{detail:{code:'location_required',message:'本次课尚无学校点名，请获取当前位置后继续'}},409);}
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'同步与推送',exact:true}).click();await expect(d.getByRole('alert')).toContainText('允许浏览器定位后可再次同步');expect(syncRequests).toBe(1);await expect(d.locator('[data-student-id]')).toHaveCount(2);expect(errors).toEqual([]);
});

test('LQ dispatch fresh remote record bypasses a stale location hint',async({page})=>{
  const current={...round(2),state:'published'};let syncCalls=0;
  await page.addInitScript(()=>{(window as any).geoCalls=0;Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:()=>{(window as any).geoCalls++;throw new Error('Unexpected location request');}},configurable:true});});
  const state={binding:{state:'matched',revision:4,needs_location:true,diagnostics:[]},can_sync:true,plan:null};
  const errors=await fixture(page,async(route,url,body)=>{if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});if(url.endsWith('/external/workflow'))return json(route,state);if(url.endsWith('/external/sync')){syncCalls++;expect(body.location).toBeUndefined();return json(route,{...state,can_sync:false,plan:{state:'pending',progress:{percent:5,stage:'reading_remote'}}},202);}return json(route,{},500);},'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'同步与推送',exact:true}).click();await expect(d.getByRole('progressbar')).toHaveAttribute('value','5');expect(syncCalls).toBe(1);expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(0);expect(errors).toEqual([]);
});

for(const rejected of [false,true])test(`LQ dispatch location handshake ${rejected?'stops after one retry':'keeps one intent through acceptance'}`,async({page})=>{
  const current={...round(2),state:'published'},calls:any[]=[];const state={binding:{state:'matched',revision:4,needs_location:false,diagnostics:[]},can_sync:true,plan:null};
  await page.addInitScript(()=>{(window as any).geoCalls=0;Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:(success:any)=>{(window as any).geoCalls++;success({coords:{latitude:22.3,longitude:108.4},timestamp:1794435600000});}},configurable:true});});
  const errors=await fixture(page,async(route,url,body)=>{if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});if(url.endsWith('/external/workflow'))return json(route,state);if(url.endsWith('/external/sync')){calls.push(body);if(calls.length===1||rejected)return json(route,{detail:{code:'location_required',message:'本次位置需要重新核验，请稍后重试'}},409);return json(route,{...state,can_sync:false,plan:{state:'pending',progress:{percent:5,stage:'reading_remote'}}},202);}return json(route,{},500);},'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'同步与推送',exact:true}).click();if(rejected)await expect(d.getByRole('alert')).toContainText('位置需要重新核验');else await expect(d.getByRole('progressbar')).toHaveAttribute('value','5');
  expect(calls).toHaveLength(2);expect(calls[0].location).toBeUndefined();expect(calls[1]).toMatchObject({idempotency_key:calls[0].idempotency_key,expected_binding_revision:4,location:{latitude:22.3,longitude:108.4,captured_at_ms:1794435600000}});expect(await page.evaluate(()=>(window as any).geoCalls)).toBe(1);expect(errors).toEqual([]);
});

test('LQ delayed school refresh cannot unlock sync while a local revision is being recalculated',async({page})=>{
  const current={...round(2),state:'published'};let releaseWorkflow!:()=>void,releaseRecalculation!:()=>void,recalculating=false;
  const workflowWait=new Promise<void>(resolve=>releaseWorkflow=resolve),recalculationWait=new Promise<void>(resolve=>releaseRecalculation=resolve);
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/external/workflow')){await workflowWait;return json(route,{binding:{state:'matched',revision:1,needs_location:false,diagnostics:[]},can_sync:true,plan:null});}
    if(url.endsWith('/rounds/9'))return json(route,{...current,revision:2});
    if(url.endsWith('/settlement')){if(!body)return json(route,{gate:{can_start:true,can_restart:false},task:null,round_revision:1});recalculating=true;await recalculationWait;return json(route,{gate:{can_start:true,can_restart:false},task:settlementTask(1,'completed'),round_revision:2,round:{...current,revision:2}},202);}
    return json(route,{detail:'unexpected '+url},500);
  },'teacher','',undefined,true,false,true);
  try {
    await open(page);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.getByRole('button',{name:'立即结算',exact:true}).click();await expect.poll(()=>recalculating).toBe(true);
    releaseWorkflow();await expect(d.getByText('已对齐',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'对齐课次',exact:true})).toBeDisabled();await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeDisabled();
    releaseRecalculation();await expect(d.getByRole('button',{name:'同步与推送',exact:true})).toBeEnabled();expect(errors).toEqual([]);
  } finally {releaseWorkflow();releaseRecalculation();}
});

test('LQ completed processing distinguishes protected school states and unresolved conflicts from confirmed attendance',async({page})=>{
  const current={...round(3),state:'published'};const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/external/workflow'))return json(route,{binding:{state:'matched',revision:1,diagnostics:[]},can_sync:true,plan:{state:'partial',progress:{percent:100,completed:3,total:3,stage:'partial'},last_run:{operations:[{student_number:'S001',state:'confirmed'},{student_number:'S002',state:'protected',reason:'学校病假已保留'},{student_number:'S003',state:'conflict',reason:'需要核对最新版本'}]}}});
    return json(route,{},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.getByRole('progressbar',{name:'部分完成 · 处理进度'})).toHaveAttribute('value','100');await expect(d.getByText('已回读确认 1 人 · 保留特殊状态 1 人 · 仍需核对 1 人')).toBeVisible();await d.getByText('查看核对说明',{exact:true}).click();await expect(d.getByText('S002：学校病假已保留')).toBeVisible();await expect(d.getByText('S003：需要核对最新版本')).toBeVisible();expect(errors).toEqual([]);
});

test('LQ uncertain sync retry reuses its key but a new explicit sync after accepted partial completion has a new key',async({page})=>{
  const current={...round(2),state:'published'},keys:string[]=[];const workflow=()=>({binding:{state:'matched',revision:1,needs_location:false,diagnostics:[]},can_sync:true,plan:keys.length>1?{state:'partial',progress:{percent:100,completed:1,total:2,stage:'partial'},message:'部分完成，可再次核对后继续'}:null});
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/external/workflow'))return json(route,workflow());
    if(url.endsWith('/external/sync')){keys.push(body.idempotency_key);return keys.length===1?route.abort('failed'):json(route,workflow(),202);}
    return json(route,{},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true}),sync=d.getByRole('button',{name:'同步与推送',exact:true});await sync.click();await expect(d.getByRole('alert')).toContainText('网络请求失败');await sync.click();await expect(d.getByText('部分完成，可再次核对后继续')).toBeVisible();await sync.click();expect(keys).toHaveLength(3);expect(keys[1]).toBe(keys[0]);expect(keys[2]).not.toBe(keys[1]);expect(errors).toEqual([]);
});

test('LQ persisted captcha task exposes school verification without requiring account setup',async({page})=>{
  const current={...round(2),state:'published'};const errors=await fixture(page,async(route,url)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/external/workflow'))return json(route,{binding:{state:'matched',revision:1,diagnostics:[]},can_sync:true,plan:{state:'captcha_required',message:'学校要求完成验证码后继续',progress:{percent:null,completed:0,total:2,stage:'captcha_required'}}});
    return json(route,{},500);
  },'teacher','',undefined,true);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.getByRole('link',{name:'完成学校验证码'})).toHaveAttribute('href','https://edu.gxufl.edu.cn/login');await expect(d.getByRole('button',{name:'配置智慧课堂账号'})).not.toBeVisible();await expect(d.getByRole('progressbar',{name:'学校验证码待完成 · 处理进度'})).not.toHaveAttribute('value');expect(errors).toEqual([]);
});

for (const dark of [false,true]) test(`LQ compact attendance ${dark?'dark':'light'} mobile retains cards and uncluttered workflow`,async({page})=>{
  const current={...round(40),state:'published'};await page.setViewportSize({width:390,height:844});
  const errors=await fixture(page,async(route,url)=>url.endsWith('/sessions/12')?json(route,{session,policy:current.policy,round:current}):json(route,{}));
  await page.evaluate(dark=>{document.documentElement.dataset.appearance=dark?'dark':'light';document.documentElement.classList.toggle('dark',dark);document.documentElement.style.colorScheme=dark?'dark':'light';},dark);
  await open(page,'teacher',false);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await expect(d.locator('[data-student-id]')).toHaveCount(40);await expect(d.getByText('查看核对说明',{exact:true})).not.toBeVisible();await expect(d.locator('.la-student-select').first()).not.toBeVisible();expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await page.screenshot({path:`.codex-temp/evidence/attendance-workflow-20261112/compact-${dark?'dark':'light'}-mobile.png`});await d.locator('.la-student-open').first().scrollIntoViewIfNeeded();await page.screenshot({path:`.codex-temp/evidence/attendance-workflow-20261112/compact-${dark?'dark':'light'}-roster.png`});expect(errors).toEqual([]);
});

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
  await dialog.locator('.la-student-open').first().click();
  const student = page.getByRole('dialog',{name:'学生签到详情',exact:true});
  await expect(student.getByText('0 分 0 秒', { exact: false })).toBeVisible();
  await expect(student.getByText('可靠记录为零', { exact: false })).toBeVisible();
  await page.keyboard.press('Escape');await expect(student).not.toBeVisible();
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
  await open(page); await page.getByRole('button', {name:'高级核对与历史任务',exact:true}).click(); const dialog=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});
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

test('LQ school auto push requires explicit exact slot and consent, preserves retry identity and waits for settlement', async ({ page }) => {
  const current = {...round(2), state:'settling'}, writes:any[]=[]; let saved:any=null;
  const target={course_name:'合成课程',course_code:'T001',teaching_class_name:'合成班',academic_year:'2026-2027',academic_term:1,session_date:'2026-11-09',week:11,day_of_week:1,section:'4,5'};
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/capabilities'))return json(route,{read_sources:true,read_checkins:true,readback:true,auto_sync:true,update_student:true});
    if(url.endsWith('/source-options'))return json(route,{items:[{course_name:'合成课程',course_code:'T001',teaching_class_name:'合成班',source_token:'synthetic-source'}]});
    if(url.endsWith('/slot-options'))return json(route,{options:[{label:'2026-11-09 第11周星期一 4、5节 · 合成班 · 已有点名99',slot_token:'synthetic-slot',remote_checkin_id:'99',is_stopped:true,can_create:false,target}]});
    if(url.endsWith('/auto-sync')&&route.request().method()==='PUT'){writes.push(body);if(writes.length===1)return json(route,{detail:'合成短暂故障'},503);saved={revision:1,enabled:true,state:'waiting_settlement',message:'正在等待补报结算',target,remote_checkin_id:'99',last_run:null};return json(route,{plan:saved});}
    if(url.endsWith('/auto-sync'))return json(route,{plan:saved});
    if(url.endsWith('/runs')||url.endsWith('/bindings'))return json(route,{items:[]});
    return json(route,{detail:'unexpected '+url},500);
  });
  await open(page);await page.getByRole('button',{name:'高级核对与历史任务',exact:true}).click();const d=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});
  await expect(d.getByText('本轮未启用学校自动推送',{exact:true})).toBeVisible();expect(writes).toHaveLength(0);
  await d.getByRole('button',{name:'读取学校授课班'}).click();await choose(page,d,'学校授课班','合成课程 · T001 · 合成班');
  await d.getByRole('button',{name:'读取本课学校课次'}).click();await choose(page,d,'学校准确课次与轮次','2026-11-09 第11周星期一 4、5节 · 合成班 · 已有点名99');
  await expect(d.getByRole('button',{name:'启用本轮结束后自动推送'})).toBeDisabled();
  await d.getByLabel('我知悉学校单会话限制，授权本轮后台使用已保存账号',{exact:true}).check();
  await d.getByRole('button',{name:'启用本轮结束后自动推送'}).click();await expect(d.getByRole('alert')).toContainText('合成短暂故障');
  await d.getByRole('button',{name:'启用本轮结束后自动推送'}).click();await expect(d.getByText('等待补报结算',{exact:true})).toBeVisible();
  expect(writes).toHaveLength(2);expect(writes[0].idempotency_key).toBe(writes[1].idempotency_key);expect(writes[1]).toMatchObject({source_token:'synthetic-source',slot_token:'synthetic-slot',allow_create:false,enabled:true,acknowledge_session_takeover:true});expect(writes[1].location).toBeUndefined();
  await page.screenshot({path:'.codex-temp/school-attendance-auto-sync-desktop.png',fullPage:true});
  await page.keyboard.press('Escape');await expect(d).not.toBeVisible();await expect(page.getByRole('button',{name:'高级核对与历史任务',exact:true})).toBeFocused();expect(saved.enabled).toBe(true);expect(errors).toEqual([]);
});

test('LQ school auto push denied geolocation keeps draft and permits an existing round, revision conflicts retain consent',async({page})=>{
  await page.addInitScript(()=>Object.defineProperty(navigator,'geolocation',{value:{getCurrentPosition:(_ok:any,fail:any)=>fail({code:1})}}));
  const current=round(2), requests:any[]=[];let version=7;
  const errors=await fixture(page,async(route,url,body)=>{
    if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});
    if(url.endsWith('/capabilities'))return json(route,{read_sources:true,read_checkins:true,readback:true,auto_sync:true,update_student:true});
    if(url.endsWith('/source-options'))return json(route,{items:[{course_name:'合成课程',course_code:'T001',teaching_class_name:'合成班',source_token:'synthetic-source'}]});
    if(url.endsWith('/slot-options'))return json(route,{options:[{label:'明确的新课次',slot_token:'create-slot',can_create:true},{label:'明确的已有点名',slot_token:'existing-slot',remote_checkin_id:'99',can_create:false}]});
    if(url.endsWith('/auto-sync/retry')){requests.push(body);if(body.expected_revision!==version)return json(route,{detail:'任务版本已变化'},409);return json(route,{plan:{revision:9,enabled:true,state:'pending',target:{}}});}
    if(url.endsWith('/auto-sync'))return json(route,{plan:{revision:version,enabled:true,state:'paused',target:{}}});
    if(url.endsWith('/runs')||url.endsWith('/bindings'))return json(route,{items:[]});return json(route,{detail:'unexpected '+url},500);
  });
  await open(page);await page.getByRole('button',{name:'高级核对与历史任务',exact:true}).click();const d=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});
  await d.getByRole('button',{name:'读取学校授课班'}).click();await choose(page,d,'学校授课班','合成课程 · T001 · 合成班');await d.getByRole('button',{name:'读取本课学校课次'}).click();await choose(page,d,'学校准确课次与轮次','明确的新课次');
  await d.getByRole('button',{name:'获取本次教室定位'}).click();await expect(d.getByRole('alert')).toContainText('未取得定位');
  await choose(page,d,'学校准确课次与轮次','明确的已有点名');await expect(d.getByRole('button',{name:'获取本次教室定位'})).toBeDisabled();
  const consent=d.getByLabel('我知悉学校单会话限制，授权本轮后台使用已保存账号',{exact:true});await consent.check();version=8;
  await d.getByRole('button',{name:'核对后继续推送'}).click();await expect(d.getByRole('alert')).toContainText('配置或任务已变化');await expect(consent).toBeChecked();
  await d.getByRole('button',{name:'刷新自动推送进度'}).click();await expect(consent).toBeChecked();await d.getByRole('button',{name:'核对后继续推送'}).click();await expect(d.getByText('已排队',{exact:true})).toBeVisible();expect(requests.map(x=>x.expected_revision)).toEqual([7,8]);
  await page.keyboard.press('Escape');await expect(page.getByRole('dialog',{name:'关闭并放弃同步配置草稿？'})).toBeVisible();await page.getByRole('dialog',{name:'关闭并放弃同步配置草稿？'}).getByRole('button',{name:'取消',exact:true}).click();await expect(consent).toBeChecked();expect(errors).toEqual([]);
});

test('LQ historical school sync exposes saved verification without remote mutations',async({page})=>{
  await page.setViewportSize({width:390,height:844});
  const current={...round(2),is_current:false,state:'void'},calls:string[]=[];
  const errors=await fixture(page,async(route,url)=>{calls.push(route.request().method()+' '+url);if(url.endsWith('/sessions/12'))return json(route,{session,policy:current.policy,round:current});if(url.endsWith('/capabilities'))return json(route,{readback:true,auto_sync:true});if(url.endsWith('/auto-sync'))return json(route,{plan:{revision:3,state:'void',enabled:false,target:{},last_run:{operations:[{student_number:'S001',target_status:'CHECKED',remote_status:'CHECKED',state:'confirmed',reason:'学校回读一致'}]}}});if(url.endsWith('/runs'))return json(route,{items:[{id:'student-only-run',mode:'student_push'},{id:'automatic-only-run',mode:'auto_sync'}]});if(url.endsWith('/bindings'))return json(route,{items:[]});return json(route,{detail:'unexpected'},500);});
  await open(page);await page.getByRole('button',{name:'查看学校同步记录'}).click();const d=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});await expect(d.getByText('学校回读一致',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'启用本轮结束后自动推送'})).toHaveCount(0);await expect(d.getByRole('button',{name:'重新授权后台连接'})).toHaveCount(0);await expect(d.getByLabel('学年',{exact:true})).not.toBeVisible();expect(await d.evaluate(n=>n.scrollWidth<=n.clientWidth+1)).toBe(true);await page.screenshot({path:'.codex-temp/evidence/attendance-workflow-20261112/school-history-mobile.png',fullPage:true});expect(calls.every(x=>x.startsWith('GET '))).toBe(true);expect(calls.some(x=>x.endsWith('/runs/automatic-only-run'))).toBe(false);expect(errors).toEqual([]);
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
  await page.screenshot({path:'.codex-temp/evidence/attendance-workflow-20261112/attendance-mobile.png',fullPage:true}); expect(errors).toEqual([]);
});

test('LQ historical integer-zero round is read-only and teacher appeal drafts survive detail switching and refresh', async ({ page }) => {
  const current = { ...round(2), is_current: 0 }; const errors = await fixture(page, async (route, url) => {
    if (url.endsWith('/sessions/12')) return json(route,{session,policy:current.policy,round:current});
    if (url.includes('/students/')) return json(route,{record:current.records[Number(url.split('/').at(-1))-1],history:[],pending_appeal:{id:70,status:'pending'}});
    if (url.endsWith('/api/approvals/70')) return json(route,{request:{id:70,status:'pending',can_decide:true,reason:'请核对合成签到'}});
    return json(route,{detail:'unexpected'},500);
  });
  await open(page); const dialog=page.getByRole('dialog',{name:'自动点名',exact:true}); await expect(dialog.locator('[data-student-id]')).toHaveCount(2);
  await expect(dialog.getByRole('button',{name:'对齐课次',exact:true})).not.toBeVisible();
  await expect(dialog.getByRole('button',{name:'结束本地点名',exact:true})).toHaveCount(0); await expect(dialog.getByRole('button',{name:'重新核算',exact:true})).toHaveCount(0);
  await dialog.getByRole('button',{name:'全班',exact:true}).click(); await expect(dialog.getByRole('button',{name:'应用到已选学生'})).toBeDisabled();
  current.is_current=1; await dialog.getByRole('button',{name:'刷新记录',exact:true}).click();
  await dialog.locator('.la-student-open').first().click(); const student=page.getByRole('dialog',{name:'学生签到详情',exact:true});await student.getByLabel('处理说明').fill('合成处理草稿，尚未提交');
  await page.keyboard.press('Escape');await dialog.locator('.la-student-open').last().click();await expect(student.getByLabel('处理说明')).toHaveValue('合成处理草稿，尚未提交');
  await student.getByRole('button',{name:'重新读取最新记录'}).click();await expect(student.getByLabel('处理说明')).toHaveValue('合成处理草稿，尚未提交'); expect(errors).toEqual([]);
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
  const d=page.getByRole('dialog',{name:'自动点名',exact:true,includeHidden:true}); await expect(d.locator('[data-student-id]')).toHaveCount(2);
  await expect(page.getByRole('dialog',{name:'学生签到详情',exact:true}).getByRole('heading',{name:'合成同学1 · 本次签到'})).toBeVisible();
  expect(urls.some(url=>url.endsWith('/rounds/9'))).toBeTruthy();expect(urls.some(url=>url.endsWith('/sessions/12'))).toBeFalsy();
  expect(await page.evaluate(()=>{const trigger=document.querySelector('#teachingSessionAutoAttendanceBtn');return Boolean((window as any).attendance)||trigger?.isConnected;})).toBeTruthy();expect(errors).toEqual([]);
});

test('LQ merged history deep link has no lesson dependency and gates manual edits separately from live operations',async({page})=>{
  const current={...round(2),id:42,session_id:null,is_current:0,can_edit:true,history_only:true,state:'published',context:{session_title:'已合并的原课次',session_date:'2026-09-13',course_name:'合成原课程'}};
  current.records.forEach(row=>(row as any).class_name='原合成班'); const calls:string[]=[];
  const markup=`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/login_attendance.css')}"></head><body><button id="cw-attendance-history">历史点名</button><script>history.replaceState(null,'','/?attendance_id=42&history_only=1');window.APP_CONFIG={classOfferingId:2,userInfo:{id:1,role:'teacher'},teachingPlan:{sessions:[]}};</script><script type="module">import {initClassroomPage} from '${asset('js/classroom_page.js')}';initClassroomPage();window.ready=true;</script></body></html>`;
  const errors=await fixture(page,async(route,url,body)=>{calls.push(url);if(url.endsWith('/rounds/42'))return json(route,current);if(url.endsWith('/records'))return json(route,{results:body.records.map((r:any)=>({student_id:r.student_id,result:'updated'})),round:current});return json(route,{detail:'must not load another lesson'},500);},'teacher','',markup);
  const d=page.getByRole('dialog',{name:'历史点名详情',exact:true}); await expect(d.getByRole('heading',{name:'已合并的原课次'})).toBeVisible(); await expect(d.getByText('2026-09-13 原合成班 · 原课次',{exact:false})).toBeVisible();
  await expect(d.getByRole('button',{name:'结束本地点名'})).toHaveCount(0);await expect(d.getByRole('button',{name:'重新核算'})).toHaveCount(0);await expect(d.getByRole('button',{name:'高级核对与历史任务',exact:true})).toHaveCount(0);
  await d.getByText('批量修改出勤状态',{exact:true}).click();await d.getByRole('button',{name:'全班',exact:true}).click();await d.getByLabel('修改理由').fill('教师核对原班级历史事实');await expect(d.getByRole('button',{name:'应用到已选学生'})).toBeEnabled();await d.getByRole('button',{name:'应用到已选学生'}).click();await expect(d.locator('.la-bulk-fields button[data-lq-component="button"]')).toBeDisabled();await expect(d.locator('.la-selection-count')).toHaveText('已选择 0 人');
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
  await open(page);await page.getByRole('button',{name:'高级核对与历史任务',exact:true}).click();const d=page.getByRole('dialog',{name:'智慧课堂核对',exact:true});await expect(d.getByRole('button',{name:'原学校账号重新验证后续接'})).toBeEnabled();await d.getByRole('button',{name:'原学校账号重新验证后续接'}).click();await expect.poll(()=>revision).toBe(7);expect(errors).toEqual([]);
});

test('LQ measured learning is healthy evidence and does not enter the collection review filter',async({page})=>{
  const current=round(3);current.records[1].evidence.collection.status='measured';current.records[1].evidence.learning.measured_seconds=45;
  (current.records[1].evidence as any).network_access={first_ip:'192.0.2.14',last_ip:'192.0.2.15',distinct_ips:['192.0.2.14','192.0.2.15'],source_types:['login'],observation_count:3};
  const errors=await fixture(page,async(route,url)=>url.includes('/students/')?json(route,{record:current.records[1],history:[]}):json(route,{session,policy:current.policy,round:current}));
  await open(page);const d=page.getByRole('dialog',{name:'自动点名',exact:true});await d.locator('.la-student-open').nth(1).click();const student=page.getByRole('dialog',{name:'学生签到详情',exact:true});await expect(student.getByText('已采集有效学习',{exact:false})).toBeVisible();await expect(student.getByText('平台接入 IP（本轮时间窗）：首次 192.0.2.14；末次 192.0.2.15。')).toBeVisible();await page.keyboard.press('Escape');
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
  await expect(d.getByRole('button',{name:'开始记录',exact:true})).toBeVisible();await d.getByRole('button',{name:'刷新记录',exact:true}).click();await expect(d.getByRole('button',{name:'开始记录',exact:true})).toBeVisible();
  await d.getByLabel('预计记录时长（分钟）').fill('90');await d.getByRole('button',{name:'开始记录',exact:true}).click();await expect(d.locator('.la-guidance')).toContainText('正在自动记录');expect(creates).toHaveLength(1);expect(creates[0].supersedes_id).toBeUndefined();
  await page.evaluate(()=>(window as any).attendance.destroy());await page.getByRole('button',{name:'自动点名',exact:true}).click();await expect(d.locator('[data-student-id]')).toHaveCount(2);
  await page.evaluate(async(url)=>{(window as any).attendance.destroy();const m=await import(url);(window as any).attendance=m.openLoginAttendance({classOfferingId:2,session:{id:12},userRole:'teacher',trigger:document.querySelector('#attendance'),attendanceId:9});},asset('js/login_attendance.js'));
  await expect(d.getByText('已作废',{exact:true})).toBeVisible();await expect(d.getByRole('button',{name:'开始记录',exact:true})).toHaveCount(0);expect(errors).toEqual([]);
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
