/* Focused real-HTTP conflict acceptance; no route interception or Office.
 * Uses tools/career_frontend_http_probe.py on a fresh loopback database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('playwright');
const base = process.argv[2] || 'http://127.0.0.1:8772';
const output = path.resolve(process.argv[3] || '.codex-temp/resume-conflict-final-qa');
if (new URL(base).hostname !== '127.0.0.1') throw new Error('Loopback only');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage(), errors = [], writes = [], downloads = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('download', download => downloads.push(download));
  page.on('request', request => { if (request.method() === 'PUT' && /\/api\/resume\/resumes\/\d+$/.test(request.url())) writes.push(request.postDataJSON()); });
  async function json(route, options = {}) {
    const response = await context.request.fetch(base + route, options);
    assert.equal(response.ok(), true, `${route}: ${response.status()} ${await response.text()}`);
    return response.json();
  }
  async function capture(name) { await page.screenshot({ path: path.join(output, name + '.png'), fullPage: true }); }
  try {
    const start = await json('/__qa__/health'); assert.equal(start.fixed_code, true);
    const palette = await json('/api/resume/builder/palette');
    const career = await json('/api/career-path/initialize', { method: 'POST' });
    const direction = career.network.nodes[0];
    const initial = { title: '冲突验收草稿', target_position: '英语教学助理', template_key: 'classic', draft: true,
      layout: { personal_fields: ['name', 'phone', 'email'], blocks: [{ type: 'experience', ids: [palette.experience[0].id] }, { type: 'tech_stack' }] },
      source_context: { career_tag: direction.tag, direction_id: direction.direction_id, recommendation_revision: career.result_version }, client_id: crypto.randomUUID() };
    const id = (await json('/api/resume/resumes', { method: 'POST', data: initial })).id;
    await page.goto(base + '/resume/builder?edit=' + id);
    await page.getByRole('button', { name: '编辑本份文字' }).click();
    await page.locator('[data-field="phone"]').fill('13900001234');
    await page.locator('#rzSnapshotSummary').fill('本人核对后的摘要 <script>不应执行</script>');
    await page.locator('#rzSnapshotCapabilities').fill('教学沟通：英语教学、课堂反馈');
    await page.getByRole('button', { name: '应用到当前草稿' }).click();
    await page.locator('#rzResumeTitle').fill('必须保留的本地草稿');
    await json('/api/resume/resumes/' + id, { method: 'PUT', data: { ...initial, revision: 1, title: '服务器中的更新版本' } });
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    const modal = page.getByRole('dialog', { name: '这份资料有了新版本' }); await modal.waitFor();
    const summary = await modal.locator('textarea').inputValue();
    assert.match(summary, /标题：必须保留的本地草稿/);
    assert.match(summary, /目标岗位：英语教学助理/);
    assert.match(summary, /本份职业摘要：本人核对后的摘要/);
    assert.match(summary, /教学沟通：英语教学、课堂反馈/);
    assert.match(summary, /电话：13900001234/);
    assert.doesNotMatch(summary, /template_key|client_id|source_context|content_overrides|"layout"/);
    assert.match(await modal.innerText(), /当前草稿仍保留在这个页面中/);
    assert.equal(await page.locator('#rzResumeTitle').inputValue(), '必须保留的本地草稿');
    assert.equal(await modal.locator('script').count(), 0); assert.equal(downloads.length, 0);
    await capture('conflict-chinese-1440');
    await page.setViewportSize({ width: 360, height: 800 });
    assert.equal(await modal.evaluate(element => element.scrollWidth > element.clientWidth), false);
    for (const button of await modal.getByRole('button').all()) assert.equal(await button.isVisible(), true);
    await capture('conflict-chinese-360');
    checks.push('real 409 keeps input; Chinese text summary includes edited fields/capabilities, no technical keys or executable markup, no automatic download');
    const filePromise = page.waitForEvent('download');
    await modal.getByRole('button', { name: '下载草稿备份', exact: true }).click();
    const file = await filePromise, backupPath = path.join(output, 'complete-draft-backup.json');
    await file.saveAs(backupPath);
    const backup = JSON.parse(fs.readFileSync(backupPath, 'utf8'));
    assert.deepEqual(backup, writes[0]);
    assert.equal(backup.source_context.direction_id, direction.direction_id);
    assert.equal(backup.revision, 1);
    checks.push('explicit backup download is parseable and byte-for-field equivalent to the complete rejected payload, including hidden provenance/layout/revision');
    await modal.getByRole('button', { name: '继续查看当前输入', exact: true }).click();
    assert.equal(await page.locator('#rzResumeTitle').inputValue(), '必须保留的本地草稿');
    await page.getByRole('button', { name: '保存草稿', exact: true }).click(); await modal.waitFor();
    await modal.getByRole('button', { name: '我已保留输入，载入最新版本', exact: true }).click();
    await page.waitForFunction(() => document.querySelector('#rzResumeTitle').value === '服务器中的更新版本');
    assert.equal((await json('/api/resume/resumes/' + id)).resume.revision, 2);
    checks.push('return-to-edit retains local input; explicit load-latest restores the server revision without overwriting it');
    // Verify the complete backup can be saved as a separate draft through the
    // existing create API. This is a data recovery check, not a new UI importer.
    const restoredPayload = { ...backup, client_id: crypto.randomUUID(), draft: true }; delete restoredPayload.revision;
    const restoredId = (await json('/api/resume/resumes', { method: 'POST', data: restoredPayload })).id;
    assert.notEqual(restoredId, id);
    const restored = (await json('/api/resume/resumes/' + restoredId)).resume;
    assert.equal(restored.title, backup.title); assert.equal(restored.optimized_summary_md, backup.optimized_summary_md);
    assert.deepEqual(restored.tech_stack, backup.tech_stack); assert.deepEqual(restored.content_overrides, backup.content_overrides);
    assert.equal(restored.content_snapshot.personal.phone, '13900001234');
    assert.equal((await json('/api/resume/resumes/' + id)).resume.title, '服务器中的更新版本');
    await page.goto(base + '/resume/builder?edit=' + restoredId);
    await page.waitForFunction(() => document.querySelector('#rzResumeTitle').value === '必须保留的本地草稿');
    await page.getByRole('button', { name: '编辑本份文字' }).click();
    assert.equal(await page.locator('#rzSnapshotSummary').inputValue(), backup.optimized_summary_md);
    assert.match(await page.locator('#rzSnapshotCapabilities').inputValue(), /教学沟通：英语教学、课堂反馈/);
    await capture('backup-restored-draft');
    checks.push('downloaded backup recreates a separate editable draft through the existing API, preserving text/capabilities/material edits and the original server version');
    const finish = await json('/__qa__/health'); assert.equal(finish.fixed_code, true); assert.equal(finish.source_fingerprint, start.source_fingerprint);
    assert.deepEqual(errors, []);
    const report = { ok: true, checks, pageErrors: errors, source_fingerprint: finish.source_fingerprint, fixed_code: finish.fixed_code,
      source_manifest: finish.startup_manifest, test_sha256: crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),
      backup_path: backupPath, original_resume_id: id, restored_resume_id: restoredId, office_used: false,
      recovery_scope: 'Existing API data recovery verified; this change does not add a JSON import UI.' };
    fs.writeFileSync(path.join(output, 'conflict-http-qa.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report, null, 2));
  } catch (error) { await capture('conflict-failure'); console.error(error); throw error; }
  finally { await browser.close(); }
})().catch(() => { process.exitCode = 1; });
