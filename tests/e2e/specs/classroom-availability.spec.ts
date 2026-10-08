import { test,expect,type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { loginTeacher,loginStudent } from '../fixtures/p03';
const runtime=path.resolve(process.env.CLASSROOM_AVAILABILITY_RUNTIME || '.codex-temp/classroom-availability-20261008-runtime');
const f=JSON.parse(fs.readFileSync(path.join(runtime,'fixture.json'),'utf8'));
if (!f.classroomAvailability?.synthetic || !runtime.startsWith(path.resolve('.codex-temp')+path.sep)) throw Error('Owned synthetic fixture required');
const graph=process.env.CLASSROOM_AVAILABILITY_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json','utf8')).revision;
const API='/api/manage/academic/course-schedule/editor';
function mode(mode:string,delay_ms=0){fs.writeFileSync(path.join(runtime,'availability-control.json'),JSON.stringify({mode,delay_ms}));}
function calls(){const file=path.join(runtime,'academic-requests.jsonl');return fs.existsSync(file)?fs.readFileSync(file,'utf8').trim().split(/\r?\n/).filter(Boolean).map(s=>JSON.parse(s)):[];}
async function isolate(page:Page){
  await page.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await expect.poll(async()=>{try{return (await (await page.request.get('/api/internal/health')).json()).database_path;}catch{return null;}},{timeout:30000}).toBe(f.databasePath);
}
async function openQuery(page:Page){
  await page.goto('/manage/academic/classrooms');
  await expect(page.locator('#freeRoomSubmitBtn')).toBeEnabled();
  await page.locator('#freeRoomSemesterSelect').selectOption(String(f.classroomAvailability.semesterId));
  await page.locator('#freeRoomCampusSelect').selectOption('1');
  await page.locator('#freeRoomNameInput').fill('B310');
  await page.locator('#freeRoomWeekRow [data-week="6"]').click();
  await page.locator('#freeRoomWeekdayRow [data-weekday="4"]').click();
  for(const n of [2,3])await expect(page.locator(`#freeRoomSectionRow [data-section="${n}"]`)).toHaveClass(/is-active/);
}
test.beforeEach(async({page})=>{mode('success');await isolate(page);await loginTeacher(page,f);});
test.afterEach(async({page},info)=>{
  const graphs=await page.evaluate(()=>[...new Set(performance.getEntriesByType('resource').map(e=>e.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(graphs).toEqual([graph]);await info.attach('upstream-synthetic-requests',{body:JSON.stringify(calls()),contentType:'application/json'});
});

test('classroom query: exact condition, empty/error/retry, late response and second page',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await openQuery(page);
  const sent=page.waitForRequest(r=>r.url().endsWith('/classrooms/free-query')&&r.method()==='POST');
  await page.locator('#freeRoomSubmitBtn').click();
  expect((await sent).postDataJSON()).toMatchObject({semester_id:f.classroomAvailability.semesterId,xqh_id:'1',cdmc:'B310',weeks:[6],weekday:[4],sections:[2,3]});
  await expect(page.locator('#freeRoomResultList')).toContainText('B310');
  expect(calls().filter(x=>x.method==='POST').at(-1).form).toMatchObject({xnm:'2026',xqm:'3',zcd:'32',xqj:'4',jcd:'6'});
  await page.screenshot({path:info.outputPath('exact-query-success.png'),fullPage:true});
  mode('empty');await page.locator('#freeRoomSubmitBtn').click();
  await expect(page.locator('#freeRoomResultEmpty')).toContainText('没有可用教室');await expect(page.locator('#freeRoomResultList .classroom-place-card')).toHaveCount(0);
  for(const state of ['login_error','remote_error','malformed']){
    mode(state);await page.locator('#freeRoomSubmitBtn').click();
    await expect(page.locator('#freeRoomResultEmpty')).toContainText(/失败|无法|异常|过期|未返回有效/);
    await expect(page.locator('#freeRoomSubmitBtn')).toBeEnabled();
  }
  mode('success',700);const slow=page.waitForResponse(r=>r.url().endsWith('/classrooms/free-query'));
  await page.locator('#freeRoomSubmitBtn').click();await page.locator('#freeRoomNameInput').fill('B312');await slow;
  await expect(page.locator('#freeRoomResultList .classroom-place-card')).toHaveCount(0);
  mode('success');await page.locator('#freeRoomSubmitBtn').click();await expect(page.locator('#freeRoomResultList')).toContainText('B312');
  mode('pages');await page.locator('#freeRoomNameInput').fill('');await page.locator('#freeRoomSubmitBtn').click();
  await expect(page.locator('#freeRoomResultList .classroom-place-card')).toHaveCount(100);
  await page.locator('#freeRoomPagination').getByRole('button',{name:'第 2 页',exact:true}).click();
  await expect(page.locator('#freeRoomResultList .classroom-place-card')).toHaveCount(25);
  await expect(page.locator('#freeRoomResultList')).toContainText('B310');
  await page.screenshot({path:info.outputPath('query-second-page.png'),fullPage:true});expect(errors).toEqual([]);
});

test('3D editor shares term/slot query, loads later rooms and rejects stale results',async({page},info)=>{
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/manage/academic/course-schedule/editor?year=2026-2027&term=1');
  await page.locator('[data-cse-week="6"]').click();await page.locator('[data-cse-lesson]').first().click();
  await expect(page.locator('[data-cse-drawer]')).toBeVisible();
  await page.locator('#cseWeekday').selectOption('4');await page.locator('#cseStart').selectOption('2');
  mode('pages');const sent=page.waitForRequest(r=>r.url().includes('/editor/free-rooms?'));
  await page.locator('[data-cse-free-rooms]').click();const requested=new URL((await sent).url());
  for(const [k,v]of Object.entries({year:'2026-2027',term:'1',week:'6',weekday:'4',sections:'2,3',page:'1',page_size:'40'}))expect(requested.searchParams.get(k)).toBe(v);
  await expect(page.locator('[data-cse-free-rooms-list] [data-cse-room]')).toHaveCount(40);
  await expect(page.locator('[data-cse-free-rooms-list]')).toContainText('当前教室该时段空闲');
  await page.locator('[data-cse-free-more]').click();await expect(page.locator('[data-cse-free-rooms-list] [data-cse-room]')).toHaveCount(80);
  await page.locator('[data-cse-free-rooms-list] [data-cse-room="136B312"]').click();await expect(page.locator('#cseRoom')).toHaveValue('136B312');
  await expect(page.locator('#cseWeekday')).toHaveValue('4');await expect(page.locator('#cseStart')).toHaveValue('2');
  mode('empty');await page.locator('[data-cse-free-rooms]').click();await expect(page.locator('[data-cse-free-rooms-list]')).toContainText('没有空闲教室');
  mode('remote_error');await page.locator('[data-cse-free-rooms]').click();await expect(page.locator('[data-cse-free-rooms-list]')).toContainText(/失败|无法/);
  await expect(page.locator('#cseRoom')).toHaveValue('136B312');
  mode('success',700);const slow=page.waitForResponse(r=>r.url().includes('/editor/free-rooms?'));
  await page.locator('[data-cse-free-rooms]').click();await page.locator('#cseStart').selectOption('6');await slow;
  await expect(page.locator('[data-cse-free-rooms-list] [data-cse-room]')).toHaveCount(0);
  mode('success');await page.locator('[data-cse-free-rooms]').click();await expect(page.locator('[data-cse-free-rooms-list] [data-cse-room="136B312"]')).toBeVisible();
  await page.screenshot({path:info.outputPath('editor-room-selection.png'),fullPage:true});
  mode('target_error');await page.locator('[data-cse-free-rooms]').click();
  await expect(page.locator('[data-cse-free-rooms-list] [data-cse-room]')).toHaveCount(40);
  await expect(page.locator('[data-cse-free-rooms-list]')).toContainText('占用未能确认');
  expect(errors).toEqual([]);
});

test('unscoped and empty room identities remain unknown; failed targeted check preserves candidates',async({page})=>{
  const url=`${API}/free-rooms?year=2026-2027&term=1&week=6&weekday=4&sections=2,3&page=1&page_size=40`;
  mode('success');
  for(const query of ['&room_id=&room=','&room_id=not-in-teacher-scope&room=']){
    const response=await page.request.get(url+query);expect(response.status()).toBe(200);
    const body=(await response.json()).result;expect(body.room_status).toBe('unknown');expect(body.items.length).toBe(2);
  }
  mode('target_error');const body=(await (await page.request.get(url+'&room_id=136B310')).json()).result;
  expect(body.room_status).toBe('unknown');expect(body.items.length).toBe(40);
  expect(body.room_status_message).toContain('未能确认');
  const target=calls().filter(x=>x.method==='POST'&&x.form.cd_id==='136B310').at(-1).form;
  expect(target).toMatchObject({cd_id:'136B310',cdmc:'',lh:'',cdlb_id:'',xqh_id:'1','queryModel.currentPage':'1'});
});

test('student cannot query teaching-system rooms; credential-less teacher receives clear error',async({page,browser})=>{
  const context=await browser.newContext({baseURL:'http://127.0.0.1:8360'});const student=await context.newPage();await isolate(student);await loginStudent(student,f);
  expect((await student.request.post('/api/manage/classrooms/free-query',{data:{weeks:[6],weekday:[4],sections:[2,3]}})).status()).toBe(403);
  expect((await student.request.get(`${API}/free-rooms?year=2026-2027&term=1&week=6&weekday=4&sections=2,3`)).status()).toBe(403);await context.close();
  await loginTeacher(page,f,f.otherTeacher);
  const denied=await page.request.post('/api/manage/classrooms/free-query',{data:{weeks:[6],weekday:[4],sections:[2,3]}});
  expect(denied.status()).toBe(400);expect((await denied.json()).detail).toContain('账号');
});
