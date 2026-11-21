import { expect, test, type APIRequestContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import path from 'node:path';
import fs from 'node:fs/promises';
import { expectHealthUsesRuntimeDb, loginTeacher, loginStudent, readFixture, type P03Fixture } from '../fixtures/p03';

type Fixture = P03Fixture & { uiV3Synthetic: boolean; projectTopicsQa: { otherCourse: number } };
const fixture = () => readFixture() as Fixture;
const base = (oid: number) => `/api/classrooms/${oid}/project-topics`;
const topicPath = (url: string, key: string, path: string) =>
  `${url}/topics/${encodeURIComponent(key)}/files/${path.split('/').map(encodeURIComponent).join('/')}`;
const phoneSize = { width: 390, height: 844 };
test.use({ actionTimeout: 15_000 });

async function captureWorkspace(page: Page, info: TestInfo, name: string) {
  const surface = page.getByRole('dialog').filter({ has: page.locator('.pt-workspace') });
  await expect(surface).toBeVisible();
  const metrics = await surface.evaluate(el => {
    const body = el.querySelector('.lq-dialog__body')!;
    const content = el.querySelector('.pt-workspace')!;
    return { frameWidth: innerWidth, surface: el.getBoundingClientRect().toJSON(),
      bodyWidth: body.clientWidth, bodyScrollWidth: body.scrollWidth,
      contentWidth: content.clientWidth, contentScrollWidth: content.scrollWidth };
  });
  expect(metrics.surface.left).toBeGreaterThanOrEqual(-1);
  expect(metrics.surface.right).toBeLessThanOrEqual(metrics.frameWidth + 1);
  expect(metrics.bodyScrollWidth).toBeLessThanOrEqual(metrics.bodyWidth + 1);
  expect(metrics.contentScrollWidth).toBeLessThanOrEqual(metrics.contentWidth + 1);
  await expect(page.locator('#ai-chat-fab')).toBeHidden();
  if (metrics.frameWidth === phoneSize.width) {
    const corner = await surface.evaluate(el => {
      // The classroom assistant also docks here, underneath the open bottom sheet.
      const hit = document.elementFromPoint(innerWidth - 42, innerHeight - 42);
      return { inside: el.contains(hit), target: hit?.outerHTML.slice(0, 240),
        control: hit?.closest('button, a, [id]')?.outerHTML.slice(0, 240) };
    });
    expect(corner.inside, `手机弹层应独占右下操作区域 ${JSON.stringify(corner)}`).toBe(true);
  }
  const accessibility = await new AxeBuilder({ page }).include('.pt-dialog')
    .withRules(['button-name', 'link-name', 'label']).analyze();
  expect(accessibility.violations.map(v => ({ rule: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
  const png = info.outputPath(`${name}.png`);
  await page.screenshot({ path: png, animations: 'disabled' });
  await info.attach(name, { path: png, contentType: 'image/png' });
}

async function expectIconControl(control: Locator) {
  await expect(control).toHaveAccessibleName(/\S/);
  await expect(control.locator('svg[aria-hidden="true"]').first()).toBeVisible();
  if (await control.evaluate(() => matchMedia('(pointer: coarse)').matches)) {
    const bounds = await control.boundingBox();
    expect(bounds!.height).toBeGreaterThanOrEqual(44);
    expect(bounds!.width).toBeGreaterThanOrEqual(44);
  }
}

test.beforeEach(async ({ page }) => {
  const f = fixture();
  test.skip(!f.uiV3Synthetic || !f.projectTopicsQa, 'Requires a disposable project-topic classroom');
  await loginTeacher(page, f);
  await expectHealthUsesRuntimeDb(page, f);
});

async function publish(request: APIRequestContext, b: string, data: Record<string, unknown>) {
  const preflight = await request.post(`${b}/preview`, { data });
  await expect(preflight).toBeOK();
  const { preview } = await preflight.json();
  expect(preview.issues).toEqual([]);
  const payload = { ...data, preview_token: preview.preview_token, request_id: crypto.randomUUID() };
  const result = await request.post(`${b}/activities`, { data: payload });
  await expect(result).toBeOK();
  const replay = await request.post(`${b}/activities`, { data: payload });
  await expect(replay).toBeOK();
  const activity = (await result.json()).activity;
  expect((await replay.json()).activity.groups).toEqual(activity.groups);
  return activity;
}

test('teacher publishes a local ZIP, student opens own HTML and archive/withdraw update access', async ({ page, browser }, info) => {
  test.setTimeout(145_000);
  const f = fixture(), b = base(f.classOfferingId);
  await page.goto(`/classroom/${f.classOfferingId}#collaboration-panel`);
  const loadFailure = '项目列表暂时不可用';
  await page.route(url => url.pathname === `${b}/activities`, async route => {
    expect(route.request().method()).toBe('GET');
    await route.fulfill({ status:503, json:{detail:loadFailure} });
  }, {times:1});
  const launcher = page.locator('[data-project-topics-open]').first();
  await expect(launcher).toBeEnabled();
  await launcher.focus();
  await page.keyboard.press('Enter');
  const workspace = page.locator('.pt-workspace');
  const dialog = page.getByRole('dialog').filter({ has: workspace });
  await expect(workspace).toBeVisible();
  await expect(workspace.getByRole('alert')).toContainText(loadFailure);
  const retry = workspace.getByRole('button', {name:'重新加载'});
  await expectIconControl(retry);
  await retry.press('Enter');
  await expect(workspace.locator('summary[data-pt-import-toggle]')).toBeVisible();
  await expect(dialog.locator('.lq-dialog__head h2')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(workspace).not.toBeVisible();
  await expect(launcher).toBeFocused();
  await expect(page.locator('#ai-chat-fab')).toBeVisible();
  await page.keyboard.press('Enter');
  const importer = workspace.locator('summary[data-pt-import-toggle]');
  await expect(importer).toBeVisible();
  if (!await workspace.locator('[data-pt-form=import]').isVisible()) {
    await importer.focus();
    await page.keyboard.press('Enter');
  }
  await expect(workspace.locator('[data-pt-form=import]')).toBeVisible();
  await workspace.locator('input[name=zip]').setInputFiles(path.join(f.runtimeRoot, 'html-projects.zip'));
  const [upload] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/packages/import') && r.request().method() === 'POST'),
    workspace.locator('[data-pt-action=import]').click(),
  ]);
  expect(upload.ok(), await upload.text()).toBe(true);
  expect((await upload.json()).package.topics.filter((t: {readiness: string}) => t.readiness === 'complete')).toHaveLength(14);
  await expect(workspace.locator('[data-pt-totals]')).toContainText('14');
  await expectIconControl(workspace.locator('[data-pt-action=prepare]'));
  await workspace.locator('[data-pt-action=prepare]').click();
  const form = workspace.locator('[data-pt-form=publish]');
  const activityTitle = 'Python 期末大作业';
  await form.locator('[name=title]').fill(activityTitle);
  await form.locator('[name=min_members]').fill('4');
  await form.locator('[name=max_members]').fill('5');
  await form.locator('[name=group_count]').fill('13');
  const randomMode = form.locator('input[name=mode][value=random]');
  const manualMode = form.locator('input[name=mode][value=manual]');
  await expect(randomMode).toBeChecked();
  await expect(manualMode).toHaveAccessibleName(/指定/);
  await randomMode.press('ArrowRight');
  await expect(manualMode).toBeChecked();
  await expect(form.locator('[data-pt-bindings] select')).toHaveCount(13);
  await manualMode.press('ArrowLeft');
  await expect(randomMode).toBeChecked();
  await expect(form.locator('[data-pt-bindings] select')).toHaveCount(0);
  const topicPool = form.locator('details[data-pt-pool]');
  await expect(topicPool).not.toHaveAttribute('open');
  await topicPool.locator('summary').press('Enter');
  const primary = form.locator('input[name=topic_keys]').first();
  await primary.uncheck();
  await expect(topicPool.locator('[data-pt-pool-count]')).toContainText('12');
  await form.locator('[data-pt-action=preview]').click();
  await expect(form.locator('[data-pt-preview]')).toContainText('12');
  await expect(form.locator('[data-pt-action=publish]')).toHaveCount(0);
  await form.locator('[data-pt-action=save-draft]').click();
  await expect(form.locator('[data-pt-draft-status]')).toContainText('已保存');
  await dialog.locator('[data-pt-action=home]').click();
  await workspace.locator('[data-pt-action=prepare]').click();
  await expect(form.locator('[data-pt-draft-status]')).toContainText('已恢复');
  await topicPool.locator('summary').press('Enter');
  await expect(primary).not.toBeChecked();
  await primary.check();
  await topicPool.locator('summary').press('Enter');
  await form.locator('[data-pt-action=save-draft]').click();
  await expect(form.locator('[data-pt-draft-status]')).toContainText('已保存');
  await form.locator('[data-pt-action=preview]').click();
  const preflight = form.locator('[data-pt-preview]');
  await expect(preflight.locator('[data-pt-metric=students]')).toContainText('63');
  await expect(preflight.locator('[data-pt-metric=groups]')).toContainText('13');
  await expect(preflight.locator('[data-pt-metric=topics]')).toContainText('13');
  await expectIconControl(form.locator('[data-pt-action=publish]'));
  await preflight.scrollIntoViewIfNeeded();
  await captureWorkspace(page, info, 'teacher-preflight-desktop');
  const [published] = await Promise.all([page.waitForResponse(r => r.url().endsWith('/activities') && r.request().method() === 'POST'),
    form.locator('[data-pt-action=publish]').click()]);
  expect(published.ok(), await published.text()).toBe(true);
  const { activity } = await published.json(), url = `${b}/activities/${activity.id}`;
  await expect(dialog.getByText(activityTitle, { exact: true }).first()).toBeVisible();
  expect(activity.groups.map((g: any) => g.member_count).sort()).toEqual([4, 4, ...Array(11).fill(5)]);
  expect(new Set(activity.groups.map((g: any) => g.topic.key)).size).toBe(13);
  expect(activity.unassigned_count).toBe(0);
  const csv = await page.request.get(`${url}/export.csv`);
  await expect(csv).toBeOK();
  expect((await csv.text()).trim().split('\n')).toHaveLength(64);
  const workbook = await page.request.get(`${url}/export.xlsx`);
  await expect(workbook).toBeOK();
  expect((await workbook.body()).subarray(0, 2).toString()).toBe('PK');
  const cleared = await page.request.get(`${b}/packages/${activity.package_version_id}/dispatch-draft`);
  expect((await cleared.json()).draft.payload).toBeNull();
  await captureWorkspace(page, info, 'teacher-dispatch-desktop');
  const back = dialog.locator('[data-pt-action=home]');
  await expectIconControl(back);
  await back.press('Enter');
  const activityButton = workspace.locator('[data-pt-action=activity]').first();
  await expectIconControl(activityButton);
  await captureWorkspace(page, info, 'teacher-list-desktop');
  await workspace.locator('[data-pt-action=package]').first().click();
  await workspace.locator('[data-pt-action=prepare]').click();
  await expect(form.locator('select[name=scheme]')).toHaveValue(String(activity.scheme_id));
  await expect(form.locator('[data-pt-participants] [name=group_ids]:checked')).toHaveCount(13);

  const mobileOptions = { baseURL: new URL(page.url()).origin, viewport: phoneSize, screen: phoneSize,
    hasTouch: true, isMobile: true, deviceScaleFactor: 1 };
  const mobileTeacher = await browser.newContext({ ...mobileOptions, storageState: await page.context().storageState() });
  try {
    const phone = await mobileTeacher.newPage();
    await phone.goto(`/classroom/${f.classOfferingId}#collaboration-panel`);
    await phone.locator('[data-project-topics-open]').first().tap();
    const manage = phone.locator('[data-pt-action=activity]').first();
    await expectIconControl(manage);
    await manage.tap();
    await expect(phone.getByRole('dialog')).toContainText(activityTitle);
    await captureWorkspace(phone, info, 'teacher-dispatch-mobile');
  } finally { await mobileTeacher.close(); }

  const student = await browser.newContext(mobileOptions);
  try {
    const phone = await student.newPage();
    await loginStudent(phone, f);
    await phone.goto(`/classroom/${f.classOfferingId}#collaboration-panel`);
    await phone.locator('[data-project-topics-open]').first().click();
    const content = phone.locator('.pt-workspace');
    const { activity: own } = await (await student.request.get(url)).json();
    expect(own.groups).toBeUndefined();
    await expect(content).toContainText(own.my_topic.title);
    const other = activity.groups.find((g: any) => g.topic.key !== own.my_topic.key).topic;
    await expect(content).not.toContainText(other.title);
    const fileUrl = topicPath(url, own.my_topic.key, own.my_topic.entry);
    const download = content.locator(`a[download][href="${fileUrl}?download=1"]`);
    await expectIconControl(download);
    const readerLink = content.locator(`a[href="${fileUrl}"]`);
    await expectIconControl(readerLink);
    await expect(readerLink).toHaveAccessibleName(/打开任务书/);
    const attachment = topicPath(url, own.my_topic.key, own.my_topic.attachments[0]);
    const attachmentLink = content.locator(`a[download][href="${attachment}?download=1"]`);
    await expectIconControl(attachmentLink);
    const attached = await student.request.get(await attachmentLink.getAttribute('href') as string);
    await expect(attached).toBeOK();
    expect(await attached.text()).toContain(own.my_topic.key);
    await captureWorkspace(phone, info, 'student-topic-mobile');
    const [reader] = await Promise.all([student.waitForEvent('page'), readerLink.tap()]);
    const title = reader.frameLocator('iframe').getByRole('heading', { name: own.my_topic.title });
    await expect(title).toBeVisible();
    await expect(title).toHaveCSS('color', 'rgb(19, 97, 53)');
    const previous = reader.url();
    expect(previous).toContain('/activities/');
    const frameAddress = await reader.locator('iframe').getAttribute('src');
    expect(frameAddress).toContain('/render/');
    expect((await page.request.get(frameAddress!)).status()).toBe(403);
    expect((await student.request.get(topicPath(url, other.key, other.entry))).status()).toBe(404);
    await expect(await student.request.get(`${url}/files/${encodeURIComponent('公共说明.txt')}`)).toBeOK();
    const started = performance.now();
    const projections = await Promise.all(Array.from({ length: 12 }, () => student.request.get(url)));
    for (const response of projections) expect((await response.json()).activity.my_topic.key).toBe(own.my_topic.key);
    await info.attach('concurrent-student-reads', { body: JSON.stringify({requests:12, elapsedMs:Math.round(performance.now()-started)}), contentType:'application/json' });
    const archived = await page.request.post(`${url}/change`, { data: {action:'archive', revision:activity.revision, reason:'课堂归档验收'} });
    await expect(archived).toBeOK();
    expect((await (await student.request.get(url)).json()).activity.my_topic.key).toBe(own.my_topic.key);
    const change = await page.request.post(`${url}/change`, { data: {action:'withdraw', revision:(await archived.json()).activity.revision, reason:'课堂撤回验收'} });
    await expect(change).toBeOK();
    expect((await student.request.get(previous)).ok()).toBe(false);
    expect((await student.request.get(frameAddress!)).ok()).toBe(false);
    expect((await student.request.get(url)).ok()).toBe(false);
  } finally { await student.close(); }
});

test('PDF-only course reuses a package across classrooms and students can join from the project', async ({ page, browser }) => {
  test.setTimeout(95_000);
  const f = fixture(), oid = f.projectTopicsQa.otherCourse, b = base(oid);
  const zip = await fs.readFile(path.join(f.runtimeRoot, 'pdf-projects.zip'));
  const imported = await page.request.post(`${b}/packages/import`, { multipart: { files: {name:'field.zip', mimeType:'application/zip', buffer:zip} } });
  await expect(imported).toBeOK();
  const pkg = (await imported.json()).package;
  const payload = { title:'研习项目', package_version_id:pkg.id, new_scheme:{name:'研习组',group_count:6,min_members:4,max_members:4,allocation_mode:'all'},
    topic_keys:pkg.topics.map((t: any)=>t.key), mode:'random' };
  const a = await publish(page.request, b, payload);
  expect(a.groups).toHaveLength(6);
  expect(a.student_count).toBe(24);
  expect(a.groups.every((g: any)=>g.member_count===4)).toBe(true);
  const file = a.groups[0].topic;
  const pdf = await page.request.get(topicPath(`${b}/activities/${a.id}`, file.key, file.entry));
  await expect(pdf).toBeOK();
  expect((await pdf.body()).subarray(0,4).toString()).toBe('%PDF');
  const first = base(f.classOfferingId);
  const reuse = await publish(page.request, first, {...payload,
    new_scheme:{...payload.new_scheme, min_members:10, max_members:11, allocation_mode:'self'}});
  expect(reuse.package_version_id).toBe(pkg.id);
  expect(reuse.id).not.toBe(a.id);
  expect(reuse.student_count).toBe(63);
  expect(reuse.unassigned_count).toBe(63);
  const student = await browser.newContext({baseURL:new URL(page.url()).origin, viewport:phoneSize,
    screen:phoneSize, hasTouch:true, isMobile:true, deviceScaleFactor:1});
  try {
    const phone = await student.newPage();
    await loginStudent(phone, f);
    const ownUrl = `${first}/activities/${reuse.id}`;
    const before = await student.request.get(ownUrl);
    await expect(before).toBeOK();
    expect((await before.json()).activity.my_topic).toBeFalsy();
    expect((await student.request.get(`${b}/activities/${a.id}`)).status()).toBe(403);
    await phone.goto(`/classroom/${f.classOfferingId}#collaboration-panel`);
    await phone.locator('[data-project-topics-open]').first().tap();
    const workspace = phone.locator('.pt-workspace');
    const shortcut = workspace.locator('[data-pt-action=go-join]');
    await expectIconControl(shortcut);
    await shortcut.tap();
    const join = phone.locator(`[data-collab-scheme-random-join="${reuse.scheme_id}"]`);
    await expect(workspace).not.toBeVisible();
    await expect(join).toBeFocused();
    await join.press('Enter');
    const [joined] = await Promise.all([
      phone.waitForResponse(r => r.url().endsWith('/random-join') && r.request().method() === 'POST'),
      phone.locator('[data-collab-confirm-yes]').tap(),
    ]);
    expect(joined.ok()).toBe(true);
    const groupDetails = phone.locator('.collab-group-detail');
    await expect(groupDetails).toBeVisible();
    await groupDetails.locator(`[data-project-topics-open][data-pt-scheme="${reuse.scheme_id}"]`).tap();
    const { activity: after } = await (await student.request.get(ownUrl)).json();
    const group = reuse.groups.find((g: any) => g.id === after.my_group.id);
    expect(after.my_topic.key).toEqual(group.topic.key);
    await expect(workspace).toContainText(after.my_topic.title);
    const link = workspace.getByRole('link', {name:'打开任务书'});
    const pdf = await student.request.get((await link.getAttribute('href'))!);
    await expect(pdf).toBeOK();
    expect((await pdf.body()).subarray(0,4).toString()).toBe('%PDF');
  } finally { await student.close(); }
});
