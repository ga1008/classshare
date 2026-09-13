import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

const root = process.cwd();
const weights = { material: 45, task: 35, interaction: 15, consistency: 5 };
const settings = { weights, revision: 0, version: 'default-v1', can_update: true, source: 'default', cooldown_days: 7,
  rules: Object.entries(weights).map(([key, weight]) => ({ key, weight, label: ({ material: '学习材料', task: '作业考试', interaction: '互动求助', consistency: '稳定投入' } as Record<string,string>)[key] })), presets: [] };
function render(name: string, extra = {}) {
  return execFileSync(path.join(root, 'venv/Scripts/python.exe'), ['-c',
    'import json,sys;from jinja2 import Environment,FileSystemLoader;sys.stdin.reconfigure(encoding="utf-8");sys.stdout.reconfigure(encoding="utf-8");payload=json.load(sys.stdin);print(Environment(loader=FileSystemLoader("templates"),autoescape=True).get_template(payload["template"]).render(**payload["context"]))'],
  { cwd: root, input: JSON.stringify({ template: `partials/classroom_members/${name}.html`, context: { classroom: { id: 1, course_name: '动态Web程序设计', class_name: '合班课堂' }, weight_settings: settings, ...extra } }), encoding: 'utf-8' });
}
const shell = render('workspace');
const fragments = { settings: render('settings'), exams: render('exams'), overview: render('overview', { lo: { student_count: 3, active_student_count: 3, need_attention_count: 1, teacher_note_count: 0, distribution: [], summary_cards: [] } }),
  alerts: render('alerts', { lo: { alert_summary: { student_count: 1, total_count: 1, counts: { L1: 1 }, items: [{ id: 1, severity: 'L1', severity_label: '待关注', student_id: 1, student_name: '学生甲', title: '学习提醒', body: '合成测试预警' }] } } }) };

async function fixture(page: Page) {
  const state = { requests: [] as string[], saves: [] as Record<string, unknown>[], revision: 0, conflict: false, overviewFailure: false, examStatus: 0, alertWrites: 0 };
  await page.route('http://members.test/**', async route => {
    const url = new URL(route.request().url()), file = url.pathname;
    if (file === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh"><meta charset="utf-8"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/classroom_members.css"><body class="classroom-page role-teacher"><button data-learning-modal-open>成员入口</button><textarea id="chat-draft">未发送聊天草稿</textarea>${shell}<script type="module">window.APP_CONFIG={classOfferingId:1};const m=await import('/static/js/learning_progress.js');m.initLearningProgress(window.APP_CONFIG);window.ready=true;</script></body></html>` });
    if (file === '/static/js/ui.js') return route.fulfill({ contentType: 'application/javascript', body: `export const showToast=(message)=>{window.lastToast=message};export const escapeHtml=value=>String(value??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));` });
    if (file === '/static/js/learning_certificate_reveal.js') return route.fulfill({ contentType: 'application/javascript', body: 'export function initLearningCertificateReveal(){}' });
    if (file === '/static/js/attendance_reports.js') return route.fulfill({ contentType: 'application/javascript', body: `export function initClassroomAttendancePanel(container){window.attendanceInitialized=(window.attendanceInitialized||0)+1;container.innerHTML='<p>原件已缓存</p>';return {activate(){window.attendanceActive=true},deactivate(){window.attendanceActive=false}}}` });
    if (file.startsWith('/static/')) {
      const local = path.resolve(root, `.${file}`);
      if (!local.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(local)) return route.fulfill({ status: 404 });
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(local) });
    }
    state.requests.push(file);
    if (file === '/api/classrooms/1/members') {
      const query = url.searchParams.get('q') || '';
      const all = [1,2,3].map(id => ({ id, name: `学生${id}`, student_id_number: `000${id}`, class_id: id < 3 ? 10 : 11, class_name: id < 3 ? '甲班' : '乙班', score: id * 10, progress_percent: id * 10, needs_attention: id === 1 }));
      const rows = all.filter(row => !query || row.student_id_number.includes(query));
      return route.fulfill({ json: { items: rows, total: rows.length, student_count: 3, attention_count: 1, page: 1, pages: 1, classes: [{ id:10,name:'甲班',student_count:2 },{ id:11,name:'乙班',student_count:1 }] } });
    }
    if (file.startsWith('/api/classrooms/1/member-panels/')) {
      const key = file.split('/').at(-1) as keyof typeof fragments;
      if (key === 'overview' && state.overviewFailure) return route.fulfill({ status: 503, json: { detail: '学情暂不可用' } });
      return route.fulfill({ contentType: 'text/html', body: fragments[key] });
    }
    if (file === '/api/classroom/1/retake-students') return route.fulfill({ json: { items: [] } });
    if (file === '/api/manage/classrooms/1/exam-roster') {
      state.examStatus++;
      return route.fulfill({ json: { status: 'success', student_count: 3, course_name: 'Course', course_code: 'C1', match_summary: {}, students: [], default_export: { exam_datetime_local: '2026-06-01T09:00', exam_location: 'B101', chief_invigilator: `默认监考${state.examStatus}` } } });
    }
    if (file === '/api/manage/classrooms/teaching-places') return route.fulfill({ json: { items: [] } });
    if (file === '/api/classrooms/1/learning/weights/preview') return route.fulfill({ json: { old_average: 40,new_average:42,average_delta_label:'+2',affected_count:3,student_count:3,students_preview:[] } });
    if (file === '/api/classrooms/1/learning/weights') {
      const payload = route.request().postDataJSON(); state.saves.push(payload);
      if (state.conflict) return route.fulfill({ status: 409, json: { detail: { message: '设置已更新', weight_settings: { ...settings, revision:2,can_update:false,cooldown_remaining_days:7 } } } });
      state.revision++;
      return route.fulfill({ json: { updated: true, message: '已保存', weight_settings: { ...settings,weights:payload.weights,revision:state.revision,version:`weights-r${state.revision}`,can_update:false,cooldown_remaining_days:7 } } });
    }
    if (file === '/api/classrooms/1/learning/alerts/1/actions') {
      state.alertWrites++;
      await new Promise(resolve => setTimeout(resolve, 100));
      return route.fulfill({ json: { status:'success',summary:{total_count:1},side_effect:{type:'support_note'} } });
    }
    if (file.startsWith('/manage/students/')) return route.fulfill({ contentType:'text/html', body: '<p>合成成员详情</p>' });
    return route.fulfill({ status:404,json:{detail:`Unhandled ${file}`} });
  });
  await page.goto('http://members.test/');
  await page.waitForFunction(() => (window as any).ready);
  return state;
}

test('lazy tabs keep complete roster, one dialog and detail return state', async ({ page }) => {
  const state = await fixture(page);
  expect(state.requests).toEqual([]);
  await page.getByText('成员入口', { exact:true }).click();
  await expect(page.locator('[data-learning-roster-item]')).toHaveCount(3);
  expect(state.requests).toEqual(['/api/classrooms/1/members']);
  await expect(page.getByRole('tab')).toHaveCount(6);
  await page.locator('[data-learning-roster-search]').fill('0003');
  await expect(page.locator('[data-learning-roster-item]')).toHaveCount(1);
  await expect(page.locator('.member-roster-class-head')).toHaveText('乙班');
  const row = page.locator('[data-learning-roster-item]');
  await row.click();
  await expect(page.frameLocator('[data-student-insight-frame]').getByText('合成成员详情')).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await page.locator('[data-member-detail-back]').click();
  await expect(row).toBeFocused();
  await expect(page.locator('[data-learning-roster-search]')).toHaveValue('0003');
  await page.locator('#learning-modal-close').click();
  await expect(page.getByText('成员入口', { exact:true })).toBeFocused();
});

test('weight and exam drafts survive tabs; CAS save is local and close protects drafts', async ({ page }) => {
  const state = await fixture(page);
  await page.getByText('成员入口', { exact:true }).click();
  await page.getByRole('tab',{name:'课堂设置'}).click();
  const material = page.locator('[data-weight-key=material] [data-weight-number]');
  const task = page.locator('[data-weight-key=task] [data-weight-number]');
  await material.fill('40'); await task.fill('40');
  await page.getByRole('tab',{name:'考试名单'}).click();
  const chief = page.locator('[name=chief_invigilator]');
  await expect(chief).toHaveValue('默认监考1');
  await chief.fill('自定监考');
  await page.locator('[data-exam-roster-refresh]').click();
  await expect.poll(() => state.examStatus).toBe(2);
  await expect(chief).toHaveValue('自定监考');
  await page.getByRole('tab',{name:'课堂设置'}).click();
  await expect(material).toHaveValue('40');
  await page.locator('#learning-modal-close').click();
  await expect(page.locator('[data-member-draft-prompt]')).toBeVisible();
  await page.locator('[data-member-draft-edit]').click();
  await page.locator('[data-weight-save]').click();
  await expect(page.locator('[data-weight-version]')).toContainText('weights-r1');
  expect(state.saves[0].expected_revision).toBe(0);
  await expect(page.locator('#chat-draft')).toHaveValue('未发送聊天草稿');
  await expect(page.locator('#learning-progress-modal')).toBeVisible();
  await page.getByRole('tab',{name:'考试名单'}).click();
  await expect(chief).toHaveValue('自定监考');
  await page.locator('[data-exam-roster-cancel]').click();
  await expect(chief).toHaveValue('默认监考1');
});

test('conflicting save keeps edited values until explicit adoption', async ({ page }) => {
  const state = await fixture(page); state.conflict = true;
  await page.getByText('成员入口', { exact:true }).click();
  await page.getByRole('tab',{name:'课堂设置'}).click();
  await page.locator('[data-weight-key=material] [data-weight-number]').fill('40');
  await page.locator('[data-weight-key=task] [data-weight-number]').fill('40');
  await page.locator('[data-weight-save]').click();
  await expect(page.locator('[data-weight-status]')).toContainText('当前输入已保留');
  await expect(page.locator('[data-weight-key=material] [data-weight-number]')).toHaveValue('40');
  await page.locator('[data-weight-cancel]').click();
  await expect(page.locator('[data-weight-key=material] [data-weight-number]')).toHaveValue('45');
  await expect(page.locator('[data-weight-save]')).toBeDisabled();
});

test('overview failure leaves attendance usable and repeated support clicks send once', async ({ page }) => {
  const state = await fixture(page); state.overviewFailure=true;
  await page.getByText('成员入口', { exact:true }).click();
  await page.getByRole('tab',{name:'学情概览'}).click();
  await expect(page.locator('[data-member-panel=overview]')).toContainText('学情暂不可用');
  await page.getByRole('tab',{name:'签到统计'}).click();
  await expect(page.getByText('原件已缓存')).toBeVisible();
  await page.getByRole('tab',{name:'预警与支持'}).click();
  const note=page.locator('[data-cultivation-alert-action=support_note]');
  await note.evaluate(button=>{(button as HTMLButtonElement).click();(button as HTMLButtonElement).click();});
  await expect(note).toHaveText('已备注');
  expect(state.alertWrites).toBe(1);
  await page.getByRole('tab',{name:'签到统计'}).click();
  expect(await page.evaluate(()=>(window as any).attendanceInitialized)).toBe(1);
  await page.locator('#learning-modal-close').click();
  await expect.poll(()=>page.evaluate(()=>(window as any).attendanceActive)).toBe(false);
});

test('mobile tabs remain keyboard reachable with no page overflow', async ({ page }, testInfo) => {
  await page.setViewportSize({width:390,height:844});
  await fixture(page);
  await page.getByText('成员入口', { exact:true }).click();
  const first=page.getByRole('tab',{name:'成员',exact:true});
  await first.focus(); await page.keyboard.press('End'); await page.keyboard.press('Enter');
  await expect(page.getByRole('tab',{name:'课堂设置'})).toHaveAttribute('aria-selected','true');
  await expect(page.locator('[data-weight-save]')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('member-settings-mobile.png')});
  await page.getByRole('tab',{name:'成员',exact:true}).click();
  await page.screenshot({path:testInfo.outputPath('members-mobile.png')});
});
