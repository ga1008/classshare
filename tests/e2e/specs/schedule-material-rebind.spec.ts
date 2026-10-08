import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { loginTeacher, loginStudent } from '../fixtures/p03';

const runtime = path.resolve(process.env.SCHEDULE_MATERIAL_RUNTIME || '.codex-temp/schedule-material-runtime');
const f = JSON.parse(fs.readFileSync(path.join(runtime, 'fixture.json'), 'utf8'));
if (!f.uiV3Synthetic || !f.scheduleMaterial?.synthetic || !runtime.startsWith(path.resolve('.codex-temp') + path.sep)) throw new Error('Owned synthetic fixture required');
const s = f.scheduleMaterial;
const graph = process.env.SCHEDULE_MATERIAL_GRAPH || JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision;
function action(name: string) {
  const output = execFileSync(process.env.SCHEDULE_MATERIAL_PYTHON || 'python', ['tests/e2e/scripts/prepare_schedule_material_runtime.py', '--runtime-root', runtime, '--action', name], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, timeout: 40_000 });
  return JSON.parse(output.trim().split(/\r?\n/).at(-1)!);
}
async function isolate(page: Page) {
  await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
  await expect.poll(async () => { try { return (await (await page.request.get('/api/internal/health')).json()).database_path; } catch { return 'starting'; } },
    { timeout: 30_000 }).toBe(f.databasePath);
}
async function overview(page: Page, student = false) {
  const r = await page.request.get(student ? '/api/dashboard/course-schedule/overview' : '/api/manage/academic/course-schedule/overview');
  expect(r.status()).toBe(200); const data = await r.json(); expect(data.status).toBe('success'); return data.overview;
}
async function materials(page: Page, sid: number, oid = f.classOfferingId) {
  const r = await page.request.get(`/api/classrooms/${oid}/learning-materials?session_id=${sid}&generate_blurbs=false`);
  expect(r.status()).toBe(200); return (await r.json()).materials;
}
async function sync(page: Page) {
  const r = await page.request.post(`/api/materials/${s.rootMaterialId}/repository/command`, { data: { action: 'update' }, timeout: 60_000 });
  const data = await r.json(); expect(r.status(), JSON.stringify(data)).toBe(200);
  expect(data.learning_bindings.total_assignments).toBe(14); expect(data.learning_bindings.target_classroom_count).toBe(2); return data;
}
async function currentGraph(page: Page) {
  const graphs = await page.evaluate(() => [...new Set(performance.getEntriesByType('resource').map(e => e.name.match(/\/static\/assets\/([a-f0-9]{64})\//)?.[1]).filter(Boolean))]);
  expect(graphs).toEqual([graph]);
}
async function expandAt(page: Page, week: number, student = false) {
  if (student) {
    const label = page.locator('[data-student-week-label]');
    await expect(label).toContainText(/第\s*\d+\s*周/);
    for (let i=0;i<20;i++) {
      const current=Number((await label.innerText()).match(/第\s*(\d+)\s*周/)?.[1]);
      if (current===week) break;
      await page.locator(current>week ? '[data-student-week-prev]' : '[data-student-week-next]').click();
    }
    await page.locator('[data-student-schedule-expand]').click();
  } else {
    const slider = page.locator('[data-csd-slider]').first();
    await expect(slider).toBeAttached();
    await slider.fill(String(week)); await slider.dispatchEvent('input');
    await page.locator('.cs-card.is-active').click();
  }
  await expect(page.getByRole('dialog', { name: '整周课表' })).toBeVisible();
  await expect(page.locator('[data-csd-expand-title]')).toContainText(`第${week}周`);
}

test('real Git update, unchanged projection, approved ordinal move, manual bundle, student access and permissions', async ({ page, browser }, info) => {
  const errors: string[] = []; page.on('pageerror', e => errors.push(e.message));
  await isolate(page); await loginTeacher(page, f);
  const third = s.materialIds['lesson_3/lesson_3.html'];
  expect((await materials(page, s.oldThird)).map((x: any) => x.material_id)).toContain(third);
  // Real local bare Git: fetch/pull updates blobs and binds every assigned classroom.
  action('bump'); await sync(page); const unchanged = await sync(page);
  await info.attach('git-update-no-new-commit', { body: JSON.stringify(unchanged), contentType: 'application/json' });
  const extra = s.manualMaterialId;
  const added = await page.request.post(`/api/classrooms/${f.classOfferingId}/learning-materials`, { data: { session_id: s.oldThird, material_id: extra } });
  expect(added.status()).toBe(200);
  action('pending'); await page.reload();
  const pending = await overview(page); const pendingPair = pending.weeks.flatMap((w: any) => w.lessons).filter((x: any) => x.adjustment?.request_id === 'QA-MOVE-3-17');
  expect(pendingPair).toHaveLength(2); expect(pendingPair.map((x: any) => x.session_id)).toEqual([s.oldThird, s.oldThird]);
  await expandAt(page, s.originalSlot.week);
  const change = page.locator('.cs-expand [data-csd-change]').first(); await expect(change).toBeVisible();
  await change.click(); await expect(page.locator('[data-csd-expand-title]')).toContainText('第17周');
  await expect(page.locator('.cs-expand .cs-change-line')).not.toHaveCount(0);
  await page.screenshot({ path: info.outputPath('pending-cross-week-desktop.png') });
  await page.locator('[data-csd-expand-close]').click();
  const approved = action('approved'); expect(approved.updated_session_ids).toContain(s.oldThird);
  await page.reload(); const after = await overview(page);
  const active = after.weeks.flatMap((w: any) => w.lessons).filter((x: any) => x.class_offering_id === f.classOfferingId && x.counts_towards_total !== false);
  expect(active).toHaveLength(32);
  expect(active.map((x: any) => x.session_no).sort((a: number,b: number) => a-b)).toEqual(Array.from({ length: 32 }, (_,i) => i+1));
  expect(active.find((x: any) => x.session_id === s.oldThird)).toMatchObject({ session_no: 32, actual_date: s.targetSlot.date });
  expect(active.find((x: any) => x.session_id === s.newThird)).toMatchObject({ session_no: 3 });
  expect(after.approved_changes).toHaveLength(1);
  expect((await materials(page, s.newThird)).map((x: any) => x.material_id)).toEqual(expect.arrayContaining([third,extra]));
  expect((await materials(page, s.oldThird)).map((x: any) => x.material_id)).not.toContain(third);
  await sync(page); action('approved'); // Both publication and no-commit Git projection are idempotent.
  expect((await materials(page, s.newThird)).map((x: any) => x.material_id)).toEqual(expect.arrayContaining([third,extra]));
  await page.reload(); await expandAt(page, s.originalSlot.week);
  const history = page.locator('.cs-expand .cs-lesson--history'); await expect(history).toHaveCount(1);
  await history.locator('[data-csd-change]').click(); await expect(page.locator('[data-csd-expand-title]')).toContainText('第17周');
  await expect(page.locator('.cs-expand .is-counterpart-focus')).toContainText('32');
  await page.screenshot({ path: info.outputPath('approved-cross-week-desktop.png') }); await currentGraph(page);
  await page.locator('[data-csd-expand-close]').click();
  await page.goto(`/classroom/${f.classOfferingId}?session_id=${s.newThird}`);
  await expect(page.locator(`[data-session-id="${s.newThird}"]`).first()).toContainText('3');
  await page.screenshot({ path: info.outputPath('classroom-reordered-desktop.png') });

  const context = await browser.newContext({ baseURL: 'http://127.0.0.1:8358', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const student = await context.newPage(); await isolate(student); await loginStudent(student, f);
  const studentData = await overview(student, true);
  expect(studentData.weeks.flatMap((w: any) => w.lessons).filter((x: any) => x.class_offering_id === f.classOfferingId)).toHaveLength(32);
  expect(studentData.approved_changes).toHaveLength(1);
  await expandAt(student, s.originalSlot.week, true);
  await student.locator('.cs-expand .cs-lesson--history [data-csd-change]').click();
  await expect(student.locator('[data-csd-expand-title]')).toContainText('第17周');
  await student.screenshot({ path: info.outputPath('approved-cross-week-student-touch.png') });
  await student.locator('[data-csd-expand-close]').click();
  const reads = await materials(student, s.newThird); expect(reads.map((x: any) => x.material_id)).toContain(third);
  const item = reads.find((x: any) => x.material_id === third);
  await student.goto(item.open_url); await expect(student.frameLocator('#render-shell-frame').locator('body')).toContainText('教材第3课');
  await student.screenshot({ path: info.outputPath('student-third-textbook-touch.png') });
  expect((await student.request.post(`/api/materials/${s.rootMaterialId}/repository/command`, { data: { action: 'update' } })).status()).toBe(403);
  expect((await student.request.post(`/api/classrooms/${f.classOfferingId}/learning-materials`, { data: { session_id: s.newThird, material_id: extra } })).status()).toBe(403);
  await context.close();

  // A deliberate unbind remains suppressed across later unchanged Git pulls.
  expect((await page.request.delete(`/api/classrooms/${f.classOfferingId}/learning-materials`, { data: { session_id: s.newThird, material_id: third } })).status()).toBe(200);
  const suppressed = await page.request.post(`/api/materials/${s.rootMaterialId}/repository/command`, { data: { action: 'update' } });
  expect(suppressed.status()).toBe(200); expect((await suppressed.json()).learning_bindings.suppressed_count).toBe(1);
  expect((await materials(page, s.newThird)).map((x: any) => x.material_id)).toContain(extra);
  expect((await materials(page, s.newThird)).map((x: any) => x.material_id)).not.toContain(third);
  const other = await browser.newContext({ baseURL: 'http://127.0.0.1:8358' }); const otherPage = await other.newPage();
  await isolate(otherPage); await loginTeacher(otherPage, f, f.otherTeacher);
  expect([403,404]).toContain((await otherPage.request.post(`/api/materials/${s.rootMaterialId}/repository/command`, { data: { action: 'update' } })).status());
  expect([403,404]).toContain((await otherPage.request.get(`/api/classrooms/${f.classOfferingId}/learning-materials?session_id=${s.newThird}&generate_blurbs=false`)).status());
  await other.close(); expect(errors).toEqual([]);
});
