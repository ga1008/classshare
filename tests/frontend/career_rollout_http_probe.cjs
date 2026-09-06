/* Real loopback HTTP + full Jinja/static rollout acceptance; no interception or Office.
 * Start career_frontend_http_probe with allowlist student PK 1 and no major scopes. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8773';
const output = path.resolve(process.argv[3] || '.codex-temp/career-rollout-http-qa');
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback only');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const errors = [], checks = [], traffic = [];
  async function student(id) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.addCookies([{ name: 'qa_student', value: String(id), url: base }]);
    const page = await context.newPage();
    page.on('pageerror', error => errors.push({ student: id, message: error.message }));
    page.on('response', response => { if (response.url().startsWith(base + '/api/')) traffic.push({ student: id, url: response.url().replace(base, ''), status: response.status() }); });
    return { context, page };
  }
  const excluded = await student(2), included = await student(1);
  async function json(who, route, options = {}, status = 200) {
    const response = await who.context.request.fetch(base + route, options);
    assert.equal(response.status(), status, `${route}: ${response.status()} ${await response.text()}`);
    if (status === 403) assert.equal(response.headers()['retry-after'], undefined);
    return response.json();
  }
  async function capture(who, name) { await who.page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); }
  try {
    const startup = await json(excluded, '/__qa__/health'); assert.equal(startup.fixed_code, true);
    const page = excluded.page;
    await page.goto(base + '/career-path');
    await page.getByRole('button', { name: /快速测评/ }).waitFor();
    assert.equal(await page.locator('script[src*="career_tools_client"]').count(), 1);
    const capability = page.getByText(/AI 增强正在分批开放/);
    await capability.waitFor(); assert.equal(await capability.count(), 1);
    let state = await json(excluded, '/api/career-path/state');
    assert.equal(state.ai_availability.allowed, false); assert.ok(state.network.nodes.length);
    assert.equal((await json(excluded, '/__qa__/health')).jobs.length, 0);
    const questions = (await json(excluded, '/api/career-path/questions?mode=quick')).questions;
    await page.getByRole('button', { name: /快速测评/ }).click();
    for (const question of questions) {
      await page.getByText(question.title, { exact: true }).waitFor();
      await page.locator('#career-opts button').first().click();
      if (question.kind === 'multi') await page.locator('#career-confirm').click();
    }
    await page.getByRole('button', { name: '提交并查看方向' }).click();
    await page.locator('.career-direction').first().waitFor();
    state = await json(excluded, '/api/career-path/state');
    assert.equal(state.session_status, 'ready'); assert.ok(state.rankings.length);
    assert.equal(state.tasks.personalization.can_retry, false);
    await capture(excluded, 'rollout-excluded-career-1440');
    await page.setViewportSize({ width: 360, height: 800 });
    assert.equal(await page.locator('html').evaluate(el => el.scrollWidth > el.clientWidth), false);
    assert.equal(await capability.evaluate(el => getComputedStyle(el).color), 'rgb(205, 214, 232)');
    await capture(excluded, 'rollout-excluded-career-360');
    const lastAction = page.locator('.career-direction').last().getByRole('button').last();
    await lastAction.focus(); await lastAction.scrollIntoViewIfNeeded();
    const actionBox = await lastAction.boundingBox(), statusBox = await page.locator('#career-task-status').boundingBox();
    assert.ok(actionBox.y + actionBox.height <= statusBox.y, 'Last card action must stay above the fixed status');
    assert.equal(await lastAction.evaluate(el => document.activeElement === el), true);
    await capture(excluded, 'rollout-excluded-career-last-card-360');
    await page.setViewportSize({ width: 1440, height: 1000 });
    checks.push('excluded student sees one clear capability status, completes real quick quiz and receives baseline recommendations at 1440/360 without AI jobs');

    await page.goto(base + '/resume/profile/personal');
    await page.locator('[name="name"]').waitFor();
    await page.getByText(/AI 增强正在分批开放/).waitFor();
    await page.locator('[name="expected_position"]').fill('保留的手工目标岗位');
    const [denied] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/personal/suggest') && r.request().method() === 'POST'), page.getByRole('button', { name: '✨ AI 优化建议', exact: true }).click()]);
    assert.equal(denied.status(), 403); assert.equal((await denied.json()).detail.code, 'rollout_limited');
    assert.equal(await page.locator('[name="expected_position"]').inputValue(), '保留的手工目标岗位');
    assert.equal(await page.getByRole('dialog', { name: '资料暂不可用' }).count(), 0);
    const [personalSaved] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/resume/personal') && r.request().method() === 'POST'), page.locator('#rzPersonalForm button[type=submit]').click()]);
    assert.equal(personalSaved.status(), 200);
    assert.equal((await json(excluded, '/api/resume/personal')).info.expected_position, '保留的手工目标岗位');
    checks.push('excluded personal AI returns non-retryable 403; browser retains typed input and normal revision save succeeds');

    await page.goto(base + '/resume/builder?auto=1&target=' + encodeURIComponent('英语教师'));
    await page.locator('#rzZones .rz-chip').first().waitFor();
    await page.getByText(/AI 增强正在分批开放/).waitFor();
    await page.locator('#rzResumeTitle').fill('名单外仍可保存发布的手工简历');
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await page.waitForURL(/edit=\d+/);
    const rid = Number(new URL(page.url()).searchParams.get('edit'));
    await capture(excluded, 'rollout-excluded-resume-draft');
    await page.getByRole('button', { name: '生成文件', exact: true }).click();
    await page.waitForURL(base + '/resume/list');
    const beforeDrain = await json(excluded, '/__qa__/health');
    assert.deepEqual([...new Set(beforeDrain.jobs.map(job => job.task_type))], ['resume_render']);
    assert.ok((await json(excluded, '/__qa__/drain', { method: 'POST' })).applied > 0);
    await page.reload(); await page.getByRole('button', { name: '预览文件', exact: true }).click();
    await page.locator('iframe').waitFor();
    const document = (await json(excluded, '/api/resume/resumes/' + rid)).resume;
    assert.equal(document.render_revision, document.revision);
    assert.match(await page.locator('iframe').getAttribute('src'), new RegExp('revision=' + document.revision));
    const preview = await excluded.context.request.get(base + '/api/resume/resumes/' + rid + '/preview?revision=' + document.revision);
    assert.equal(preview.status(), 200); assert.match(await preview.text(), /合成学生2/);
    await capture(excluded, 'rollout-excluded-pinned-preview');
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '历史版本', exact: true }).click();
    await page.getByText('版本 ' + document.revision, { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    checks.push('excluded student saves/publishes with real render-lane worker, opens pinned HTML preview and history through full Jinja pages; no Office');

    await included.page.goto(base + '/career-path');
    await included.page.getByRole('button', { name: /快速测评/ }).waitFor();
    const admitted = await json(included, '/api/career-path/state');
    assert.equal(admitted.ai_availability.allowed, true);
    assert.equal(await included.page.getByText(/AI 增强正在分批开放/).count(), 0);
    const queued = await json(included, '/__qa__/health');
    assert.equal(queued.jobs.filter(job => job.task_type === 'career_major_network_generate').length, 1);
    await page.goto(base + '/career-path'); await page.locator('.career-direction').first().waitFor();
    const denial = await json(excluded, '/api/career-path/retry', { method: 'POST', data: { target: 'network' } }, 403);
    assert.equal(denial.detail.code, 'rollout_limited'); assert.equal(denial.detail.retryable, false);
    assert.deepEqual((await json(excluded, '/__qa__/health')).jobs, queued.jobs);
    await included.page.goto(base + '/resume/profile/personal');
    await included.page.getByRole('button', { name: '✨ AI 优化建议', exact: true }).click();
    await included.page.getByRole('button', { name: '取消建议任务', exact: true }).waitFor();
    await included.page.keyboard.press('Escape');
    await json(included, '/__qa__/drain', { method: 'POST' });
    await included.page.getByRole('button', { name: '查看上次建议', exact: true }).click();
    await included.page.getByRole('button', { name: '查看并核对建议', exact: true }).click();
    await included.page.getByRole('dialog', { name: '核对个人资料建议' }).waitFor();
    await capture(included, 'rollout-included-ai-review');
    checks.push('included student queues shared major AI and personal suggestion; excluded refresh/retry cannot add jobs; real durable result remains manually reviewable');
    const finish = await json(excluded, '/__qa__/health');
    assert.equal(finish.fixed_code, true, JSON.stringify(finish.changed_files));
    assert.equal(finish.source_fingerprint, startup.source_fingerprint);
    assert.equal(finish.jobs.every(job => job.status === 'succeeded'), true);
    assert.deepEqual(errors, []);
    const report = { ok: true, checks, pageErrors: errors, traffic, office_used: false, database: finish.database,
      source_fingerprint: finish.source_fingerprint, fixed_code: finish.fixed_code, source_manifest: finish.startup_manifest,
      test_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'), jobs: finish.jobs };
    fs.writeFileSync(path.join(output, 'rollout-http-qa.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ ...report, traffic: `See report (${traffic.length} browser API responses)` }, null, 2));
  } catch (error) {
    await capture(excluded, 'rollout-failure-excluded'); await capture(included, 'rollout-failure-included');
    console.error(JSON.stringify({ error: error.message, pageErrors: errors, traffic }, null, 2)); throw error;
  } finally { await browser.close(); }
})();
