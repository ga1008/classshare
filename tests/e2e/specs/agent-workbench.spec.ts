import { expect, test } from '@playwright/test';
import { DatabaseSync } from 'node:sqlite';
import { loginStudent, loginTeacher, readFixture } from '../fixtures/p03';

const shots = '.codex-temp/ui-audit';
const iso = (offsetSeconds = 0) => new Date(Date.now() + offsetSeconds * 1000).toISOString();

function seed(dbPath: string, teacherId: number) {
  const db = new DatabaseSync(dbPath);
  db.exec(`DELETE FROM agent_task_events WHERE task_id IN (91001, 91002);
           DELETE FROM agent_tasks WHERE id IN (91001, 91002);`);
  const insertTask = db.prepare(`INSERT INTO agent_tasks (id, task_uuid, teacher_id, actor_role, actor_id, teacher_name, task_type, title,
      public_summary, private_instruction, context_snapshot_json, status, priority, runtime_provider, runtime_status, result_summary,
      result_detail_json, error_message, created_at, started_at, completed_at, updated_at, origin, attachments_json)
      VALUES (?, ?, ?, 'teacher', ?, 'QA P03 Super', 'general_teaching_task', ?, '教学事务', ?, '{}', ?, 0, 'openai-agents', ?, ?, ?, '', ?, ?, ?, ?, 'manual', '[]')`);
  const detail = {
    provider: 'openai-agents', model: 'deepseek-flash',
    deliverable_markdown: '## 已完成：清理过期草稿作业\n\n- 删除了 **3 份** 上学期未发布的草稿作业（已确认过期）。\n- 其余 12 份作业保持不变。\n\n| 作业 | 状态 |\n|---|---|\n| 第1周练习(草稿) | 已删除 |\n| 第2周练习(草稿) | 已删除 |',
    operations: [{ label: '删除 3 份过期草稿作业', ok: true, status: 'observed_http_result' }],
    artifacts: [{ name: '清理报告.md', path: 'outputs/清理报告.md', download_url: '/api/agent-tasks/91001/artifacts/outputs/清理报告.md' }],
    usage: { requests: 6, input_tokens: 18230, output_tokens: 2210 },
  };
  insertTask.run(91001, 'qa-agent-91001', teacherId, teacherId, '清理过期草稿作业', '帮我把上学期没发布的草稿作业都删掉，已经过期了。',
    'completed', 'completed', '已完成：清理过期草稿作业', JSON.stringify(detail), iso(-600), iso(-590), iso(-400), iso(-400));
  insertTask.run(91002, 'qa-agent-91002', teacherId, teacherId, '给未交作业学生发提醒', '给 3 班没交第 5 次作业的同学发提醒私信。',
    'queued', 'waiting_input', '', '{}', iso(-120), iso(-110), null, iso(-60));
  const insertEvent = db.prepare('INSERT INTO agent_task_events (task_id, event_type, message, detail_json, created_at) VALUES (?, ?, ?, ?, ?)');
  const events: [number, string, string, object][] = [
    [91001, 'queued', '任务已进入全平台队列。', {}],
    [91001, 'started', 'Agent 执行器已领取任务。', {}],
    [91001, 'thinking', '用户要删除上学期未发布的草稿作业……', { text: '用户要删除上学期未发布的草稿作业。先查出所有草稿，再按学期过滤，确认它们确实过期（学期已结束且从未发布），然后逐条删除。' }],
    [91001, 'decision', '先查出所有草稿作业并确认是否过期', { decision: '先查出所有草稿作业并确认是否过期', rationale: '删除不可逆，必须先核对学期与发布状态。', next_steps: ['列出草稿作业', '按学期筛选', '逐条删除过期项'] }],
    [91001, 'tool_call', '检索平台功能：作业 草稿', { call_id: 'c1', tool: 'find_capabilities', label: '检索平台功能：作业 草稿' }],
    [91001, 'tool_result', '返回 6 条结果', { call_id: 'c1', ok: true, summary: '返回 6 条结果' }],
    [91001, 'tool_call', '统计查询：assignment_overview', { call_id: 'c2', tool: 'platform_query', label: '统计查询：assignment_overview' }],
    [91001, 'tool_result', '返回 15 条结果', { call_id: 'c2', ok: true, summary: '返回 15 条结果' }],
    [91001, 'operation', '执行操作：删除 3 份过期草稿作业', { call_id: 'c3', label: '删除作业', intent: '删除 3 份上学期未发布的过期草稿作业', method: 'DELETE', path: '/api/assignments/{assignment_id}',
      safety_check: { user_requested: true, data_state: 'expired', reason: '上学期已结束且从未发布，属于过期草稿', target_count: 3 } }],
    [91001, 'operation_result', '平台已执行并返回结果（HTTP 200）', { call_id: 'c3', ok: true, status: 'observed_http_result', http_status: 200 }],
    [91001, 'guard', '该操作属于硬性拦截的高危操作（删除学生账号），Agent 不能执行。', { code: 'agent_hard_blocked', message: '该操作属于硬性拦截的高危操作（删除学生账号），Agent 不能执行。' }],
    [91001, 'artifact', '已生成文件：清理报告.md', { path: 'outputs/清理报告.md', name: '清理报告.md', size: 820 }],
    [91002, 'queued', '任务已进入全平台队列。', {}],
    [91002, 'started', 'Agent 执行器已领取任务。', {}],
    [91002, 'decision', '先确认提醒范围与措辞', { decision: '先确认提醒范围与措辞', rationale: '3 班有两个课堂都布置了第 5 次作业。' }],
    [91002, 'question_requested', '需要你确认：提醒范围', { question: { id: 'q-qa001', title: '提醒哪些学生、用什么语气？' } }],
  ];
  events.forEach(([taskId, type, message, payload], index) => insertEvent.run(taskId, type, message, JSON.stringify(payload), iso(-500 + index)));
  db.exec(`CREATE TABLE IF NOT EXISTS agent_run_states (task_id INTEGER PRIMARY KEY, history_json TEXT NOT NULL DEFAULT '[]',
      pending_question_json TEXT NOT NULL DEFAULT '', answer_json TEXT NOT NULL DEFAULT '', pause_requested INTEGER NOT NULL DEFAULT 0,
      pause_requested_by TEXT NOT NULL DEFAULT '', segments INTEGER NOT NULL DEFAULT 0, destructive_count INTEGER NOT NULL DEFAULT 0,
      questions_asked INTEGER NOT NULL DEFAULT 0, usage_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL DEFAULT '')`);
  const question = { id: 'q-qa001', title: '提醒哪些学生、用什么语气？', context: '3 班共有 7 名同学未交第 5 次作业，分布在两个课堂。',
    questions: [
      { id: 'q1', question: '提醒哪些同学？', options: [{ label: '两个课堂的全部 7 人', description: '覆盖所有未交同学' }, { label: '只提醒《程序设计》课堂的 4 人' }], multi_select: false },
      { id: 'q2', question: '私信语气？', options: [{ label: '温和提醒', description: '说明截止时间与补交方式' }, { label: '正式通知' }, { label: '附带作业要点' }], multi_select: false },
    ] };
  db.prepare('INSERT OR REPLACE INTO agent_run_states (task_id, history_json, pending_question_json, updated_at) VALUES (?, ?, ?, ?)')
    .run(91002, '[{"role":"user","content":"x"}]', JSON.stringify(question), iso());
  db.close();
}

test('agent workbench renders every step kind, question options, and admin queue', async ({ page }) => {
  const fx = readFixture();
  await loginTeacher(page, fx, fx.superTeacher);
  await page.goto('/dashboard');
  await page.locator('#ai-chat-fab').click();
  await page.locator('[data-ai-mode-select="agent"]').click();
  await expect(page.locator('.awb-welcome')).toBeVisible();
  await page.screenshot({ path: `${shots}/agent-01-welcome.png` });

  seed(fx.databasePath, fx.superTeacher.id);
  await page.locator('[data-awb-drawer-toggle="history"]').click();
  await page.locator('.awb-drawer [data-awb-open="91001"]').click();
  await expect(page.locator('.awb-result')).toBeVisible();
  for (const kind of ['thinking', 'decision', 'tool', 'operation', 'guard', 'artifact']) {
    await expect(page.locator(`.awb-step--${kind}`).first()).toBeVisible();
  }
  await page.locator('.awb-scroll').evaluate((node) => { node.scrollTop = 0; });
  await page.screenshot({ path: `${shots}/agent-02-timeline-top.png` });
  await page.locator('.awb-scroll').evaluate((node) => { node.scrollTop = node.scrollHeight; });
  await page.screenshot({ path: `${shots}/agent-03-result.png` });
  await page.locator('#ai-chat-btn-fullscreen').click();
  await page.locator('.awb-scroll').evaluate((node) => { node.scrollTop = 0; });
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${shots}/agent-03b-fullscreen.png` });
  await page.locator('#ai-chat-btn-fullscreen').click();

  await page.locator('[data-awb-drawer-toggle="history"]').click();
  await page.locator('.awb-drawer [data-awb-open="91002"]').click();
  await expect(page.locator('.awb-question')).toBeVisible();
  const first = page.locator('[data-awb-qid="q1"] [data-awb-option]').first();
  await first.click();
  await expect(first).toHaveAttribute('aria-pressed', 'true');
  await page.locator('[data-awb-qid="q2"] [data-awb-custom]').click();
  await page.locator('[data-awb-qid="q2"] [data-awb-custom-input]').fill('语气温和，并提醒周五前补交');
  await page.screenshot({ path: `${shots}/agent-04-question.png` });
  await page.locator('[data-awb-answer-submit]').click();
  await expect.poll(() => {
    const db = new DatabaseSync(fx.databasePath);
    const row = db.prepare('SELECT runtime_status FROM agent_tasks WHERE id = 91002').get() as { runtime_status: string };
    db.close();
    return row.runtime_status;
  }).toBe('resume_pending');
  await expect(page.locator('.awb-question')).toHaveCount(0);
  await page.screenshot({ path: `${shots}/agent-05-answered.png` });

  await page.locator('[data-awb-drawer-toggle="admin"]').click();
  await expect(page.locator('.awb-admin__row').first()).toBeVisible();
  await page.screenshot({ path: `${shots}/agent-06-admin.png` });
  await page.locator('[data-awb-queue-action="pause"]').click();
  await expect(page.locator('[data-awb-queue-action="resume"]')).toBeVisible();
  await page.screenshot({ path: `${shots}/agent-07-admin-paused.png` });
  await page.locator('[data-awb-queue-action="resume"]').click();
  await expect(page.locator('[data-awb-queue-action="pause"]')).toBeVisible();
});

test('students only get the AI chat', async ({ page }) => {
  const fx = readFixture();
  await loginStudent(page, fx);
  await page.goto('/dashboard');
  await page.locator('#ai-chat-fab').click();
  await expect(page.locator('#ai-chat-textarea')).toBeVisible();
  await expect(page.locator('[data-ai-mode-select="agent"]')).toHaveCount(0);
  const status = (await page.request.get('/api/agent-tasks/bootstrap')).status();
  expect(status).toBe(403);
  await page.screenshot({ path: `${shots}/agent-08-student-chat.png` });
});
