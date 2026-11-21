import { expect, test, type Locator, type Page } from '@playwright/test';
import { expectHealthUsesRuntimeDb, loginStudent, loginTeacher, readFixture, type P03Fixture } from '../fixtures/p03';

type Fixture = P03Fixture & { uiV3Synthetic?: boolean; groupingQa?: { exemptId: number } };
const fixture = () => readFixture() as Fixture;

async function openClassroom(page: Page, f: Fixture) {
  await page.goto(`/classroom/${f.classOfferingId}#collaboration-panel`);
  await expect(page.locator('[data-collab-content]')).toBeVisible();
}

async function createForm(page: Page, name: string) {
  await page.locator('[data-collab-create-primary]').click();
  const form = page.locator('[data-collab-scheme-form]');
  await expect(form.locator('[name=name]')).toBeFocused();
  await form.locator('[name=name]').fill(name);
  await form.locator('[name=min_members]').fill('4');
  await form.locator('[name=max_members]').fill('5');
  await form.locator('[name=group_count]').fill('13');
  return form;
}

async function selectItem(page: Page, form: Locator, field: string, label: string) {
  const control = form.locator('label').filter({ has: page.locator(`select[name="${field}"]`) });
  await control.locator('.lq-dropdown__trigger').click();
  await page.getByRole('option', { name: label, exact: true }).click();
}

function namedScheme(page: Page, name: string) {
  return page.locator('[data-collab-scheme]').filter({ has: page.getByText(name, { exact: true }) });
}

test.beforeEach(async ({ page }) => {
  const f = fixture();
  test.skip(!f.uiV3Synthetic || !f.groupingQa?.exemptId, 'Requires the 63-student synthetic grouping fixture');
  await loginTeacher(page, f);
  await expectHealthUsesRuntimeDb(page, f);
  await openClassroom(page, f);
});

test('balanced whole-class allocation survives a lost reply, reopen and archive', async ({ page, browser }, info) => {
  test.setTimeout(120_000);
  const f = fixture();
  const name = `期末合成甲-${Date.now()}`;
  await page.locator('[data-collab-scheme-tab="history"]').click();
  const form = await createForm(page, name);
  await expect(form.locator('[name=allocation_mode]')).toHaveValue('all');
  await expect(form.locator('[data-collab-scheme-roster]')).toContainText('63 人');
  await page.locator('[data-collab-refresh]').click();
  await expect(form.locator('[name=name]')).toHaveValue(name);
  const endpoint = `**/api/collaboration/classrooms/${f.classOfferingId}/schemes`;
  const keys: string[] = [];
  await page.route(endpoint, async route => {
    keys.push(route.request().postDataJSON().request_id);
    if (keys.length !== 1) { await route.continue(); return; }
    const committed = await route.fetch();
    expect(committed.ok()).toBe(true);
    await route.fulfill({ status: 503, json: { detail: '合成验证：已保存后的响应丢失' } });
  });
  await form.evaluate(node => {
    (node as HTMLFormElement).requestSubmit();
    (node as HTMLFormElement).requestSubmit();
  });
  await expect(page.getByText('合成验证：已保存后的响应丢失', { exact: true }).first()).toBeVisible();
  expect(keys).toHaveLength(1);
  await expect(form.locator('[name=name]')).toHaveValue(name);
  await form.getByRole('button', { name: '开始分组' }).click();
  const card = namedScheme(page, name);
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('已分组 63/63');
  expect(keys[0]).toBeTruthy();
  expect(keys).toEqual([keys[0], keys[0]]);
  await page.reload();
  await expect(card).toContainText('已分组 63/63');
  const distributions = await card.locator('.collab-scheme-group__count').allTextContents();
  expect(distributions.filter(value => value === '4/5')).toHaveLength(2);
  expect(distributions.filter(value => value === '5/5')).toHaveLength(11);
  const members = await card.locator('[data-collab-member-info]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-collab-member-info')));
  expect(new Set(members).size).toBe(63);
  expect(members).not.toContain(String(f.groupingQa!.exemptId));
  const student = await browser.newContext({ baseURL: new URL(page.url()).origin, viewport: { width: 390, height: 844 }, hasTouch: true });
  try {
    const sp = await student.newPage();
    await loginStudent(sp, f);
    await openClassroom(sp, f);
    const ownCard = namedScheme(sp, name);
    const groupTopics = ownCard.getByRole('button', { name: '本组题目' });
    await groupTopics.click();
    const topics = sp.getByRole('dialog', { name: '本组题目' });
    await expect(topics).toContainText('还没有已发布的项目');
    await expect(sp.locator('.collab-overlay__card')).toHaveCount(0);
    await sp.keyboard.press('Escape');
    await expect(groupTopics).toBeFocused();
    await ownCard.getByRole('button', { name: '查看我的小组' }).click();
    await expect(sp.locator('[data-group-chat-form]')).toBeVisible();
    const detailTopics = sp.locator('.collab-overlay__card').getByRole('button', { name: '本组题目' });
    await detailTopics.click();
    await expect(topics).toContainText('还没有已发布的项目');
    await sp.keyboard.press('Escape');
    await expect(detailTopics).toBeFocused();
    await sp.keyboard.press('Escape');
    await card.getByRole('button', { name: '分组大屏' }).click();
    await expect(page.locator('[data-collab-board-group]')).toHaveCount(13);
    await info.attach('teacher-board', { body: await page.getByRole('dialog', { name: '分组大屏' }).screenshot(), contentType: 'image/png' });
    await page.keyboard.press('Escape');
    page.once('dialog', dialog => dialog.accept());
    await card.getByRole('button', { name: '结束方案' }).click();
    await expect(card).toHaveCount(0);
    await page.locator('[data-collab-scheme-tab="history"]').click();
    await expect(card).toContainText('已归档 63 人');
    await card.getByRole('button', { name: '分组大屏' }).click();
    await expect(page.locator('.collab-board__pool')).toHaveCount(0);
    await expect(page.locator('[data-collab-board-remove], [data-collab-board-assign], [data-collab-pool-student]')).toHaveCount(0);
    await sp.reload();
    await sp.locator('[data-collab-scheme-tab="history"]').click();
    await ownCard.getByRole('button', { name: '查看我的小组' }).click();
    await expect(sp.locator('[data-group-chat-messages]')).toBeVisible();
    await expect(sp.locator('[data-group-chat-form], [data-collab-goal-form], [data-collab-nominate]')).toHaveCount(0);
  } finally {
    await student.close();
  }
});

test.describe('touch classroom', () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  test('student-join mode can be completed by phone without moving an assigned student', async ({ page, browser }, info) => {
    test.setTimeout(120_000);
    const f = fixture();
    const name = `期末合成乙-${Date.now()}`;
    const form = await createForm(page, name);
    await selectItem(page, form, 'allocation_mode', '学生自行随机加入');
    await form.getByRole('button', { name: '开始分组' }).click();
    const card = namedScheme(page, name);
    await expect(card).toContainText('已分组 0/63');
    const student = await browser.newContext({ baseURL: new URL(page.url()).origin });
    let joinedId: number;
    try {
      const sp = await student.newPage();
      await loginStudent(sp, f);
      await openClassroom(sp, f);
      const studentCard = namedScheme(sp, name);
      const join = studentCard.getByRole('button', { name: '随机入组' });
      await expect(join).toBeVisible();
      const pause = card.getByRole('button', { name: '暂停入组' });
      const resume = card.getByRole('button', { name: '恢复入组' });
      await pause.focus();
      await pause.press('Enter');
      await expect(resume).toBeFocused();
      await sp.locator('[data-collab-refresh]').click();
      await expect(studentCard).toContainText('入组已暂停');
      await expect(join).toHaveCount(0);
      await resume.press('Enter');
      await expect(pause).toBeFocused();
      await sp.locator('[data-collab-refresh]').click();
      await join.click();
      const response = sp.waitForResponse(r => r.url().endsWith('/random-join') && r.request().method() === 'POST');
      await sp.locator('[data-collab-confirm-yes]').click();
      const joined = await response;
      expect(joined.ok()).toBe(true);
      joinedId = (await joined.json()).result.group_id;
    } finally { await student.close(); }
    await page.locator('[data-collab-refresh]').click();
    await expect(card).toContainText('已分组 1/63');
    await card.getByRole('button', { name: '分组大屏' }).click();
    const board = page.getByRole('dialog', { name: '分组大屏' });
    const manual = board.locator('[data-collab-board-assign]');
    const exempt = manual.locator(`[name=student_id] optgroup option[value="${f.groupingQa!.exemptId}"]`);
    await expect(exempt).toHaveCount(1);
    const target = manual.locator('[name=group_id] option').nth(1);
    const gid = await target.getAttribute('value');
    const studentOption = manual.locator('[name=student_id] optgroup[label="待分配"] option').first();
    const manualId = await studentOption.getAttribute('value');
    await selectItem(page, manual, 'student_id', (await studentOption.textContent())!);
    await selectItem(page, manual, 'group_id', (await target.textContent())!);
    await manual.getByRole('button', { name: '分配到该组' }).click();
    const group = board.locator(`[data-collab-board-group="${gid}"]`);
    const member = group.locator(`[data-student-id="${manualId}"]`);
    await expect(member).toHaveCount(1);
    await board.locator('[data-collab-fill]').click();
    await expect(card).toContainText('已分组 63/63');
    await expect(board.locator('.collab-board__pool h4')).toContainText('可手动加入的重修／免修学生（1）');
    await expect(board.locator('[data-collab-fill]')).toHaveCount(0);
    await expect(member).toHaveCount(1);
    await expect(board.locator(`[data-collab-board-group="${joinedId}"] [data-student-id="${f.student.id}"]`)).toHaveCount(1);
    await expect(board.locator(`[data-collab-board-remove][data-student-id="${f.groupingQa!.exemptId}"]`)).toHaveCount(0);
    await selectItem(page, manual, 'student_id', (await exempt.textContent())!);
    await selectItem(page, manual, 'group_id', (await target.textContent())!);
    await manual.getByRole('button', { name: '分配到该组' }).click();
    await expect(card).toContainText('已分组 64/64');
    await expect(board.locator('.collab-board__pool')).toHaveCount(0);
    const finalSizes = await card.locator('.collab-scheme-group__count').allTextContents();
    expect(finalSizes.filter(value => value === '4/5')).toHaveLength(1);
    expect(finalSizes.filter(value => value === '5/5')).toHaveLength(12);
    await expect(page.locator('#lq-toasts .lq-toast:visible')).toHaveCount(0, { timeout: 15_000 });
    const heading = board.locator('.collab-board__head strong');
    const close = board.getByRole('button', { name: '关闭', exact: true });
    await expect(heading).toBeInViewport({ ratio: 1 });
    await expect(close).toBeInViewport({ ratio: 1 });
    expect(await heading.evaluate(el => {
      const box = el.getBoundingClientRect();
      return el.contains(document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2));
    })).toBe(true);
    await close.click({ trial: true });
    await info.attach('mobile-board', { body: await board.screenshot(), contentType: 'image/png' });
    await page.setViewportSize({ width: 320, height: 740 });
    expect(await board.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true);
    expect(await board.locator('.collab-board__stats span').evaluateAll(labels =>
      labels.every(label => label.getClientRects().length === 1))).toBe(true);
    await page.keyboard.press('Escape');
    await expect(card.getByRole('button', { name: '分组大屏' })).toBeFocused();
  });
});

test('archived roster binds to a new assignment and retains its students and their peer reviews', async ({ page, browser }, info) => {
  const f = fixture();
  const { assignmentId } = f.groupingQa as { exemptId: number; assignmentId: string | number };
  expect(assignmentId, 'The isolated fixture must provide a fresh assignment').toBeTruthy();
  const createUrl = `/api/collaboration/classrooms/${f.classOfferingId}/schemes`;
  const assignmentUrl = `/api/assignments/${assignmentId}`;
  const create = await page.request.post(createUrl, { data: {
    name: '合成期末作业名单', allocation_mode: 'all', group_count: 13, min_members: 4, max_members: 5,
  } });
  await expect(create).toBeOK();
  type Group = { id: number; members: { student_id: number }[] };
  const { scheme } = await create.json() as { scheme: { id: number; groups: Group[] } };
  const membership = (groups: Group[]) => groups.flatMap(group => group.members.map(member => [member.student_id, group.id]))
    .sort(([left], [right]) => left - right);
  const expectedMembers = membership(scheme.groups);
  expect(new Set(expectedMembers.map(([studentId]) => studentId)).size).toBe(63);
  const readers = 12;
  const start = performance.now();
  const snapshots = await Promise.all(Array.from({ length: readers }, async () => {
    const response = await page.request.get(`/api/collaboration/classrooms/${f.classOfferingId}/snapshot`);
    await expect(response).toBeOK();
    return (await response.json()).snapshot;
  }));
  const elapsedMs = Math.round(performance.now() - start);
  for (const snapshot of snapshots) {
    const entry = snapshot.schemes.find((item: { id: number }) => item.id === scheme.id);
    expect(entry).toMatchObject({ grouped_count: 63, ungrouped_count: 0, group_count: 13 });
    expect(membership(entry.groups)).toEqual(expectedMembers);
  }
  await info.attach('concurrent-roster-reads', {
    body: JSON.stringify({ concurrentRequests: readers, students: 63, groups: 13, elapsedMs }),
    contentType: 'application/json',
  });
  const mine = scheme.groups.find(group => group.members.some(member => member.student_id === f.student.id))!;
  expect(mine).toBeDefined();
  const peerIds = mine.members.map(member => member.student_id).filter(id => id !== f.student.id).sort((a, b) => a - b);
  expect(peerIds.length).toBeGreaterThanOrEqual(3);
  await expect(await page.request.post(`/api/collaboration/schemes/${scheme.id}/close`)).toBeOK();
  const configUrl = `${assignmentUrl}/group-config`;
  const available = await page.request.get(configUrl);
  await expect(available).toBeOK();
  expect(await available.json()).toMatchObject({
    binding: null, lock_reason: '', schemes: expect.arrayContaining([
      expect.objectContaining({ id: scheme.id, is_history: true, assigned_count: 63 }),
    ]),
  });
  await expect(await page.request.post(configUrl, { data: { scheme_id: scheme.id } })).toBeOK();

  const student = await browser.newContext({ baseURL: new URL(page.url()).origin });
  try {
    await loginStudent(await student.newPage(), f);
    const peerUrl = `${assignmentUrl}/peer-eval`;
    const read = await student.request.get(peerUrl);
    await expect(read).toBeOK();
    const group = await read.json();
    expect(group).toMatchObject({ in_group: true, group: { id: mine.id }, my_ratings: {} });
    expect(group.peers.map((member: { student_id: number }) => member.student_id).sort((a: number, b: number) => a - b)).toEqual(peerIds);
    const rating = await student.request.post(peerUrl, { data: {
      ratings: [{ reviewee_student_id: peerIds[0], points: 19 }],
    } });
    await expect(rating).toBeOK();
    expect(await rating.json()).toMatchObject({ saved: 1, group_id: mine.id, peer_count: peerIds.length });

    const other = await page.request.post(createUrl, { data: { name: '合成备选名单', allocation_mode: 'self' } });
    await expect(other).toBeOK();
    const change = await page.request.post(configUrl, { data: { scheme_id: (await other.json()).scheme.id } });
    expect(change.status()).toBe(409);
    const locked = await page.request.get(configUrl);
    await expect(locked).toBeOK();
    expect(await locked.json()).toMatchObject({ binding: { scheme_id: scheme.id }, lock_reason: expect.stringContaining('锁定') });
    const reread = await student.request.get(peerUrl);
    await expect(reread).toBeOK();
    expect(await reread.json()).toMatchObject({
      group: { id: mine.id },
      my_ratings: Object.fromEntries(peerIds.map((id, index) => [id, index === 0 ? 19 : 16])),
    });
  } finally {
    await student.close();
  }
});
