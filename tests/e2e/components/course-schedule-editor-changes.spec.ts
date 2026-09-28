import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Real editor, native template and release CSS. Every request is intercepted:
// these tests never start an app, access a database or contact the academic portal.
const ORIGIN = 'http://schedule-editor.test';
const API = '/api/manage/academic/course-schedule/editor';
const LONG_REASON = '因跨学院联合教学安排调整，需保留全部原因、通知日期及后续补课安排。'.repeat(12)
  + '<img src=x onerror="window.__injected=true">';
const PROOF_NAME = '关于跨学院联合实践教学安排调整以及证明材料归档的完整通知'.repeat(5) + '.pdf';
const REMOTE_MESSAGE = '教务返回的完整诊断：场地冲突，请核对教学班、教室和各周课次。'.repeat(10);
const FIELDS = ['week', 'weekday', 'sections', 'classroom'];
type Draft = Record<string, any>;
type Call = { method: string; path: string; body: any };

function makeDraft(id: number, patch: Draft = {}): Draft {
  const original = { week: 2, weekday: 2, sections: [2, 3], room: '知新楼B310', room_id: 'ROOM-A', date: '2027-03-09' };
  const proposed = { week: 3, weekday: 3, sections: [6, 7], room: '大成楼C108', room_id: 'ROOM-B', date: '2027-03-17' };
  return { id, year: '2026-2027', term: '2', event_key: `event-${id}`, teaching_class_id: 'CLASS-A',
    teaching_class_name: '合成课程-0001', course_name: '同名合成课程', class_label: '合成教学班',
    original, proposed, original_label: '第2周 周二 第2-3节 知新楼B310',
    proposed_label: '第3周 周三 第6-7节 大成楼C108', status: 'draft', status_label: '本地草稿',
    change_kind: 'move', reason: LONG_REASON, proofs: [{ id: `proof-${id}`, name: PROOF_NAME, size: 1248 }],
    room_status: 'unknown', availability: {}, remote_ttk_id: '', remote_detail_id: '',
    remote_label: '', remote_message: '', remote_conflict: {}, ...patch };
}

function fixtureDrafts(): Draft[] {
  const roomOnly = makeDraft(102);
  roomOnly.proposed = { ...roomOnly.original, room: '大成楼C108', room_id: 'ROOM-B' };
  roomOnly.change_kind = 'room'; roomOnly.reason = '换教室'; roomOnly.proofs = [];
  const weekOnly = makeDraft(103, { teaching_class_id: 'CLASS-B', teaching_class_name: '合成课程-0002' });
  weekOnly.proposed = { ...weekOnly.original, week: 4, date: '2027-03-23' };
  const pushed = (id: number, remote: string) => makeDraft(id, { status: 'pushed', status_label: '已保存到教务',
    remote_ttk_id: remote, remote_detail_id: `DETAIL-${id}`, remote_label: '已保存，待提交', remote_message: REMOTE_MESSAGE });
  return [makeDraft(101), roomOnly, weekOnly, pushed(104, 'REMOTE-A'), pushed(105, 'REMOTE-A'), pushed(106, 'REMOTE-B')];
}

function makePayload(drafts: Draft[]) {
  const weeks = Array.from({ length: 8 }, (_, index) => ({ week_index: index + 1, label: `第${index + 1}周`,
    is_current: index === 1, lessons: [] as Draft[], lesson_count: 0, total_hours: 0 }));
  for (const draft of drafts) {
    const source = { event_key: draft.event_key, course_name: draft.course_name, teaching_class_id: draft.teaching_class_id,
      teaching_class_name: draft.teaching_class_name, class_label: draft.class_label, week_index: draft.original.week,
      weekday: draft.original.weekday, sections: draft.original.sections, classroom: draft.original.room,
      actual_date: draft.original.date, counts_towards_total: true, hours: 2, edit_draft: draft,
      class_offering_id: 501, session_id: draft.id };
    weeks[Math.max(0, Number(draft.original.week || 1) - 1)].lessons.push(source);
    weeks[Math.max(0, Number(draft.proposed.week || 1) - 1)].lessons.push({ ...source, event_key: `draft:${draft.id}`,
      source_event_key: draft.event_key, edit_ghost: true, edit_draft_id: draft.id, edit_draft: undefined,
      week_index: draft.proposed.week, weekday: draft.proposed.weekday, sections: draft.proposed.sections,
      classroom: draft.proposed.room, actual_date: draft.proposed.date, counts_towards_total: false });
  }
  return { editable: true, today: '2027-03-01', drafts, rules: { min_section: 2, max_section: 11, max_week: 8,
    pair_starts: [2, 4, 6, 8, 10], pair_unit: 2 }, calendar: { days: [], swaps: [], term_end: '2027-04-30' },
    zf_entry_url: `${ORIGIN}/academic-entry`, overview: { schedule_source: 'academic', weeks, section_range: { min: 1, max: 11 },
      selected_term: { year: '2026-2027', term: '2', label: '合成第二学期', week1_monday: '2027-03-01', focus_week: 2 },
      terms: [{ year: '2026-2027', term: '2', label: '合成第二学期' }] } };
}

async function mountEditor(page: Page, options: {
  appearance?: 'light' | 'dark'; motion?: string; drafts?: Draft[];
  checkStatus?: string; verdicts?: Record<number, string>; productionAssets?: boolean;
} = {}) {
  let drafts = structuredClone(options.drafts || fixtureDrafts());
  const diagnostics = { errors: [] as string[], unexpected: [] as string[] };
  const calls: Call[] = [];
  const modules: string[] = [];
  const assets: string[] = [];
  const manifest = options.productionAssets ? JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')) : null;
  const entry = manifest ? `/static/${manifest.entries['js/course_schedule_editor.js']}` : '/static/js/course_schedule_editor.js';
  const stylesheet = manifest ? `/static/${manifest.entries['css/tailwind-app.css']}` : '/release.css';
  if (manifest && (!/^[a-f0-9]{64}$/.test(manifest.revision) || entry.includes('undefined') || stylesheet.includes('undefined'))) {
    throw new Error('Release manifest lacks the current editor or formal CSS entry');
  }
  const holds = new Map<string, Promise<void>>();
  const template = fs.readFileSync('templates/manage/course_schedule_editor.html', 'utf8');
  const start = template.indexOf('<section class="cse-root"');
  const end = template.indexOf('<script id="course-schedule-editor-boot"');
  if (start < 0 || end < start) throw new Error('Native editor template root changed; inspect the fixture contract');
  const nativeRoot = template.slice(start, end);
  page.on('pageerror', error => diagnostics.errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== ORIGIN) { diagnostics.unexpected.push(url.href); await route.abort(); return; }
    if (manifest && url.pathname.startsWith(`/static/assets/${manifest.revision}/`)) {
      const file = path.resolve('.' + url.pathname), allowed = path.resolve('static/assets', manifest.revision) + path.sep;
      if (file.startsWith(allowed) && fs.existsSync(file)) {
        const mime: Record<string, string> = { '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.svg': 'image/svg+xml', '.png': 'image/png' };
        if (mime[path.extname(file)]) {
          assets.push(url.pathname);
          if (file.endsWith('.js')) modules.push(url.pathname);
          await route.fulfill({ contentType: mime[path.extname(file)], body: fs.readFileSync(file) }); return;
        }
      }
    }
    if (!manifest && url.pathname === '/release.css') {
      await route.fulfill({ contentType: 'text/css', body: fs.readFileSync('static/css/tailwind-app.css') }); return;
    }
    if (!manifest && url.pathname.startsWith('/static/js/') && url.pathname.endsWith('.js')) {
      const file = path.resolve('.' + url.pathname), allowed = path.resolve('static/js') + path.sep;
      if (file.startsWith(allowed) && fs.existsSync(file)) {
        modules.push(url.pathname);
        if (holds.has(url.pathname)) await holds.get(url.pathname);
        await route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) }); return;
      }
    }
    if (url.pathname === '/') {
      const boot = JSON.stringify(makePayload(drafts)).replaceAll('<', '\\u003c');
      await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare"
        data-appearance="${options.appearance || 'light'}" data-lq-motion="${options.motion || 'standard'}">
        <meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <link rel="stylesheet" href="${stylesheet}"><style>body{margin:0;padding:16px;background:hsl(var(--ls-surface-0));color:hsl(var(--ls-ink))}</style>
        <button type="button" id="outside">编辑器外操作</button>${nativeRoot}
        <script id="course-schedule-editor-boot" type="application/json">${boot}</script>
        <script type="module" src="${entry}"></script></html>` }); return;
    }
    if (url.pathname.startsWith(API) || /^\/api\/classrooms\/501\/learning-materials$/.test(url.pathname)) {
      const body = request.postData() ? JSON.parse(request.postData()!) : null;
      calls.push({ method: request.method(), path: url.pathname, body });
      if (holds.has(url.pathname)) await holds.get(url.pathname);
      let response: any;
      if (url.pathname === `${API}/availability`) response = { availability: { coverage: {}, slots: [], busy: {} } };
      else if (url.pathname.endsWith('/learning-materials')) response = { entries: [] };
      else if (url.pathname === `${API}/resequence-preview`) response = { plans: [] };
      else if (url.pathname === `${API}/push/check`) {
        const status = options.checkStatus || 'success';
        const ids = body.draft_ids?.length ? body.draft_ids : drafts.filter(d => d.status !== 'pushed').map(d => d.id);
        const results = status === 'success' ? ids.map((id: number) => {
          const draft = drafts.find(d => d.id === id)!;
          const verdict = options.verdicts?.[id] || 'ok';
          const result = { draft_id: id, status: verdict, message: verdict === 'ok' ? '无冲突' : REMOTE_MESSAGE,
            hard: verdict === 'hard', conflict_num: verdict === 'hard' ? 8 : verdict === 'conflict' ? 4 : 0 };
          draft.availability = { zf_precheck: result }; return result;
        }) : [];
        response = { ...makePayload(drafts), result: { status, results, ok: results.filter((r: any) => r.status === 'ok').length,
          conflicts: results.filter((r: any) => r.status === 'conflict').length, hard: results.filter((r: any) => r.status === 'hard').length,
          failed: results.filter((r: any) => r.status === 'failed').length,
          message: status === 'missing_credential' ? '尚未配置教务账号' : status === 'success' ? '合成预检结果' : '合成服务暂不可用' } };
      } else if (url.pathname === `${API}/push`) {
        const ids = body.draft_ids as number[];
        const results = ids.map(id => {
          const draft = drafts.find(d => d.id === id)!;
          Object.assign(draft, { status: 'pushed', status_label: '已保存到教务', remote_ttk_id: 'REMOTE-SAVED', remote_detail_id: `DETAIL-${id}` });
          return { draft_id: id, status: 'pushed', message: '已保存到教务草稿，待提交' };
        });
        response = { ...makePayload(drafts), result: { status: 'success', pushed: ids.length, conflicts: 0, failed: 0, results,
          message: '已保存到教务草稿，请登录教务系统核对后提交申请。' } };
      } else if (/\/drafts\/\d+\/withdraw$/.test(url.pathname)) {
        const id = Number(url.pathname.split('/').at(-2));
        Object.assign(drafts.find(d => d.id === id)!, { status: 'draft', status_label: '本地草稿', remote_ttk_id: '', remote_detail_id: '' });
        response = { ...makePayload(drafts), result: { status: 'success', message: '已撤回，可继续修改' } };
      } else if (request.method() === 'DELETE' && /\/drafts\/\d+$/.test(url.pathname)) {
        drafts = drafts.filter(d => d.id !== Number(url.pathname.split('/').at(-1))); response = makePayload(drafts);
      } else if (url.pathname === API && request.method() === 'GET') response = makePayload(drafts);
      else { diagnostics.unexpected.push(`${request.method()} ${url.pathname}`); await route.abort(); return; }
      await route.fulfill({ json: response }); return;
    }
    diagnostics.unexpected.push(url.href); await route.abort();
  });
  await page.goto(ORIGIN);
  await expect(page.locator('[data-cse-drafts]')).toContainText('变更清单');
  function holdPath(endpoint: string) {
    let release!: () => void;
    holds.set(endpoint, new Promise<void>(resolve => { release = resolve; }));
    return () => { holds.delete(endpoint); release(); };
  }
  return { diagnostics, calls, modules, assets, manifest, hold: (endpoint: string) => holdPath(API + endpoint), holdPath };
}

const article = (page: Page, id: number) => page.locator(`article[data-cse-draft-id="${id}"]`);
const detail = (page: Page, id: number, kind: string) => article(page, id).locator(`[data-cse-detail="${kind}"]`);
const visibleDialog = (page: Page) => page.getByRole('dialog').filter({ visible: true });

for (const width of [390, 1440]) for (const appearance of ['light', 'dark'] as const) {
  test(`change comparison keeps aligned fields and bounded chips at ${width} ${appearance}`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 980 });
    const { diagnostics } = await mountEditor(page, { appearance });
    await expect(page.locator('article[data-cse-draft-id]')).toHaveCount(6);
    await page.locator('[data-cse-drafts]').scrollIntoViewIfNeeded();
    for (const id of [101, 102, 103]) {
      const comparison = article(page, id).locator('[data-cse-comparison]');
      await expect(comparison.locator('tbody tr')).toHaveCount(2);
      const metrics = await comparison.evaluate(table => ['original', 'proposed'].map(version =>
        [...table.querySelectorAll(`[data-cse-version="${version}"] [data-cse-field]:not([data-cse-field="version"])`)].map(cell => {
          const chip = cell.querySelector('.lq-chip')!;
          const box = cell.getBoundingClientRect(), chipBox = chip.getBoundingClientRect(), css = getComputedStyle(chip);
          return { field: (cell as HTMLElement).dataset.cseField, x: box.x, y: box.y, width: box.width,
            chipX: chipBox.x, chipWidth: chipBox.width,
            changed: cell.classList.contains('is-changed'), color: css.color, weight: Number(css.fontWeight), text: cell.textContent?.trim() };
        })));
      expect(metrics[0].map(cell => cell.field)).toEqual(FIELDS);
      expect(metrics[1].map(cell => cell.field)).toEqual(FIELDS);
      for (let i = 0; i < FIELDS.length; i++) {
        expect(metrics[0][i].changed).toBe(false);
        expect(metrics[0][i].text).toBeTruthy(); expect(metrics[1][i].text).toBeTruthy();
        expect(metrics[1][i].x).toBeCloseTo(metrics[0][i].x, 0);
        expect(metrics[1][i].width).toBeCloseTo(metrics[0][i].width, 0);
        expect(metrics[1][i].chipX).toBeCloseTo(metrics[0][i].chipX, 0);
        expect(metrics[1][i].chipWidth).toBeCloseTo(metrics[0][i].chipWidth, 0);
        expect(metrics[1][i].y).toBeGreaterThan(metrics[0][i].y);
        if (metrics[1][i].changed) {
          expect(metrics[1][i].weight).toBeGreaterThanOrEqual(700);
          expect(metrics[1][i].color).not.toBe(metrics[0][i].color);
        } else expect(metrics[1][i].color).toBe(metrics[0][i].color);
      }
      expect(metrics[1].filter(cell => cell.changed).map(cell => cell.field))
        .toEqual(id === 101 ? FIELDS : id === 102 ? ['classroom'] : ['week']);
    }
    for (const kind of ['reason', 'proofs']) {
      const widths = await page.locator(`article [data-cse-detail="${kind}"]`).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().width));
      expect(widths.length).toBeGreaterThan(2);
      expect(Math.max(...widths) - Math.min(...widths)).toBeLessThanOrEqual(1);
      expect(Math.max(...widths)).toBeLessThan(width - 32);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => Boolean((window as any).__injected))).toBe(false);
    await page.screenshot({ path: testInfo.outputPath(`editor-changes-${width}-${appearance}.png`), fullPage: true });
    expect(diagnostics).toEqual({ errors: [], unexpected: [] });
  });
}

test('same class drafts group without mixing another class or existing remote application', async ({ page }) => {
  const { diagnostics } = await mountEditor(page);
  const groups = await page.locator('[data-cse-change-group]').evaluateAll(nodes => nodes.map(group =>
    [...group.querySelectorAll('article[data-cse-draft-id]')].map(row => Number((row as HTMLElement).dataset.cseDraftId)).sort()));
  expect(groups.map(group => group.join(',')).sort()).toEqual(['101,102', '103', '104,105', '106']);
  await expect(article(page, 104).locator('[data-cse-discard]')).toHaveCount(0);
  await expect(article(page, 104).locator('[data-cse-withdraw="104"]')).toHaveCount(1);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('immutable release graph renders comparisons, grouping and lazy details without source fallbacks', async ({ page }, testInfo) => {
  const { diagnostics, assets, modules, manifest } = await mountEditor(page, { productionAssets: true });
  await expect(page.locator('[data-cse-comparison]')).toHaveCount(6);
  await expect(article(page, 101).locator('tbody tr')).toHaveCount(2);
  await expect(page.locator('[data-cse-change-group]')).toHaveCount(4);
  expect(modules.some(file => file.endsWith('/lq/dialogs.js'))).toBe(false);
  await detail(page, 101, 'reason').click();
  await expect(visibleDialog(page)).toContainText(LONG_REASON);
  expect(modules.some(file => file.endsWith('/lq/dialogs.js'))).toBe(true);
  await page.keyboard.press('Escape');
  expect(assets).toContain(`/static/${manifest.entries['js/course_schedule_editor.js']}`);
  expect(assets).toContain(`/static/${manifest.entries['css/tailwind-app.css']}`);
  expect(assets.every(file => file.startsWith(`/static/assets/${manifest.revision}/`))).toBe(true);
  expect(JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8')).revision).toBe(manifest.revision);
  await testInfo.attach('release-asset-graph', { contentType: 'application/json', body: JSON.stringify({
    revision: manifest.revision, assets, sourceFallbacks: false, diagnostics,
  }, null, 2) });
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('only confirmed pushed details join a remote group; stale IDs and failed checks remain local', async ({ page }) => {
  const drafts = fixtureDrafts().concat([
    makeDraft(107, { status: 'conflict', remote_ttk_id: 'REMOTE-A', remote_detail_id: '' }),
    makeDraft(108, { status: 'draft', remote_ttk_id: 'REMOTE-A', remote_detail_id: 'STALE-DETAIL' }),
    makeDraft(109, { status: 'pushed', remote_ttk_id: 'REMOTE-A', remote_detail_id: '' }),
  ]);
  const { diagnostics } = await mountEditor(page, { drafts });
  const groups = await page.locator('[data-cse-change-group]').evaluateAll(nodes => nodes.map(group =>
    [...group.querySelectorAll('article[data-cse-draft-id]')].map(row => Number((row as HTMLElement).dataset.cseDraftId)).sort().join(',')));
  expect(groups.sort()).toEqual(['101,102,107,108', '103', '104,105', '106', '109']);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('320px long unbroken classrooms stay accessible inside the comparison scroller', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 980 });
  const room = '跨学院教室RoomWithoutSpaces'.repeat(7);
  const draft = makeDraft(101);
  draft.original.room = `${room}-原教室`;
  draft.proposed.room = `${room}-新教室`;
  const { diagnostics } = await mountEditor(page, { drafts: [draft] });
  const scroller = article(page, 101).locator('.lq-table__scroll');
  await scroller.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await expect(article(page, 101).locator('[data-cse-version="original"] [data-cse-field="classroom"] .lq-chip__label')).toHaveText(draft.original.room);
  await expect(article(page, 101).locator('[data-cse-version="proposed"] [data-cse-field="classroom"] .lq-chip__label')).toHaveText(draft.proposed.room);
  const geometry = await scroller.evaluate(node => {
    const box = node.getBoundingClientRect();
    const labels = [...node.querySelectorAll('[data-cse-field="classroom"] .lq-chip__label')];
    return { x: box.x, right: box.right, overflow: getComputedStyle(node).overflowX,
      client: node.clientWidth, scroll: node.scrollWidth,
      labels: labels.map(label => ({ client: label.clientWidth, scroll: label.scrollWidth })) };
  });
  expect(geometry.x).toBeGreaterThanOrEqual(0); expect(geometry.right).toBeLessThanOrEqual(320);
  expect(geometry.overflow).toMatch(/auto|scroll/); expect(geometry.scroll).toBeGreaterThan(geometry.client);
  // No text is lost inside an ellipsized chip: the native table region owns horizontal scrolling.
  for (const label of geometry.labels) expect(label.scroll).toBeLessThanOrEqual(label.client + 1);
  await scroller.focus();
  await page.keyboard.press('ArrowRight');
  await expect.poll(() => scroller.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  await scroller.evaluate(node => { node.scrollLeft = node.scrollWidth; });
  const end = await scroller.evaluate(node => ({ right: node.getBoundingClientRect().right,
    cellRight: node.querySelector('[data-cse-version="proposed"] [data-cse-field="classroom"]')!.getBoundingClientRect().right }));
  expect(end.cellRight).toBeLessThanOrEqual(end.right + 1);
  await page.locator('[data-cse-drafts]').screenshot({ path: testInfo.outputPath('long-classroom-320.png') });
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('reason, proof and remote details remain complete, escaped, keyboard accessible and dismissible', async ({ page }) => {
  const { diagnostics } = await mountEditor(page);
  await article(page, 101).locator('[data-cse-locate="101"]').click();
  await expect(page.locator('[data-cse-drawer]')).toBeVisible();
  await detail(page, 101, 'reason').focus(); await page.keyboard.press('Enter');
  await expect(visibleDialog(page)).toContainText(LONG_REASON);
  await expect(visibleDialog(page).locator('img')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(visibleDialog(page)).toHaveCount(0);
  await expect(detail(page, 101, 'reason')).toBeFocused();
  await expect(page.locator('[data-cse-drawer]')).toBeVisible();
  await detail(page, 101, 'proofs').click();
  const proof = visibleDialog(page).getByRole('link', { name: PROOF_NAME });
  await expect(proof).toHaveAttribute('href', `${API}/drafts/101/proofs/proof-101`);
  await expect(proof).toHaveAttribute('target', '_blank');
  await expect(proof).toHaveAttribute('rel', /noopener/);
  await page.locator('#outside').click();
  await expect(visibleDialog(page)).toHaveCount(0);
  await expect(article(page, 104)).not.toContainText(REMOTE_MESSAGE);
  await detail(page, 104, 'remote').click();
  await expect(visibleDialog(page)).toContainText(REMOTE_MESSAGE);
  await page.keyboard.press('Escape');
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('320px dark proof popover wraps complete filenames and stays within the viewport', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 320, height: 740 });
  const draft = makeDraft(101, { proofs: [{ id: 'proof-101', filename: 'MeetingNoticeWithoutSpaces'.repeat(6) + '.pdf', size: 1248 }] });
  const { diagnostics } = await mountEditor(page, { appearance: 'dark', drafts: [draft] });
  await detail(page, 101, 'proofs').click();
  const dialog = visibleDialog(page);
  await expect(dialog).toBeVisible();
  await expect.poll(() => dialog.evaluate(node => node.getAnimations({ subtree: true })
    .filter(animation => ['running', 'pending'].includes(animation.playState)).length)).toBe(0);
  const link = dialog.getByRole('link', { name: draft.proofs[0].filename });
  await expect(link).toHaveAttribute('href', `${API}/drafts/101/proofs/proof-101`);
  const geometry = await dialog.evaluate(node => {
    const box = node.getBoundingClientRect(), label = node.querySelector('a .lq-btn__label')!;
    return { x: box.x, y: box.y, right: box.right, bottom: box.bottom,
      labelWidth: label.clientWidth, labelScrollWidth: label.scrollWidth,
      labelHeight: label.clientHeight, labelScrollHeight: label.scrollHeight };
  });
  expect(geometry.x).toBeGreaterThanOrEqual(0); expect(geometry.y).toBeGreaterThanOrEqual(0);
  expect(geometry.right).toBeLessThanOrEqual(320); expect(geometry.bottom).toBeLessThanOrEqual(740);
  expect(geometry.labelScrollWidth).toBeLessThanOrEqual(geometry.labelWidth + 1);
  expect(geometry.labelScrollHeight).toBeLessThanOrEqual(geometry.labelHeight + 1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath('proof-popover-320-dark.png') });
  await page.keyboard.press('Escape');
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('overflow labels move only while needed and release their transform when interaction ends', async ({ page }) => {
  const { diagnostics } = await mountEditor(page);
  const long = detail(page, 101, 'reason'), short = detail(page, 102, 'reason');
  const longText = long.locator('[data-lq-overflow-text]');
  await expect(longText).toHaveCSS('transform', 'none');
  const originalWidth = (await long.boundingBox())!.width;
  await long.hover();
  await expect(long).toHaveAttribute('data-lq-overflow-active', '');
  await expect.poll(() => longText.evaluate(text => new DOMMatrixReadOnly(getComputedStyle(text).transform).m41)).toBeLessThan(-1);
  expect((await long.boundingBox())!.width).toBeCloseTo(originalWidth, 0);
  await short.hover();
  await expect(longText).toHaveCSS('transform', 'none');
  await expect(short).not.toHaveAttribute('data-lq-overflow-active', '');
  await expect(short.locator('[data-lq-overflow-text]')).toHaveCSS('transform', 'none');
  await long.hover();
  await expect(long).toHaveAttribute('data-lq-overflow-active', '');
  await page.evaluate(() => document.documentElement.dataset.lqMotion = 'off');
  await expect.poll(() => longText.evaluate(text => new DOMMatrixReadOnly(getComputedStyle(text).transform).m41)).toBe(0);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

for (const mode of ['off', 'reduced'] as const) {
  test(`long labels remain still but open full details with ${mode} motion`, async ({ page }) => {
    if (mode === 'reduced') await page.emulateMedia({ reducedMotion: 'reduce' });
    const { diagnostics } = await mountEditor(page, { motion: mode === 'off' ? 'off' : 'standard' });
    const trigger = detail(page, 101, 'reason');
    await trigger.hover();
    await expect(trigger).not.toHaveAttribute('data-lq-overflow-active', '');
    await expect(trigger.locator('[data-lq-overflow-text]')).toHaveCSS('transform', 'none');
    expect(await trigger.evaluate(node => node.getAnimations({ subtree: true }).filter(animation =>
      animation.effect instanceof KeyframeEffect && (animation.effect.target as HTMLElement)?.matches('[data-lq-overflow-text]')).length)).toBe(0);
    await trigger.click();
    await expect(visibleDialog(page)).toContainText(LONG_REASON);
    await page.keyboard.press('Escape');
    expect(diagnostics).toEqual({ errors: [], unexpected: [] });
  });
}

test('a pending precheck refresh disposes an open detail layer without stale content or duplicate checks', async ({ page }) => {
  const { diagnostics, calls, hold } = await mountEditor(page, { drafts: [makeDraft(101)] });
  const release = hold('/push/check');
  const check = article(page, 101).locator('[data-cse-precheck="101"]');
  await check.click();
  await expect(check).toBeDisabled();
  await expect.poll(() => calls.filter(call => call.path === `${API}/push/check`).length).toBe(1);
  await detail(page, 101, 'reason').click();
  await expect(visibleDialog(page)).toContainText(LONG_REASON);
  release();
  await expect(visibleDialog(page)).toHaveCount(0);
  await expect(article(page, 101).locator('[data-cse-precheck="101"]')).toBeEnabled();
  await detail(page, 101, 'reason').click();
  await expect(visibleDialog(page)).toHaveCount(1);
  await expect(visibleDialog(page)).toContainText(LONG_REASON);
  await page.keyboard.press('Escape');
  expect(calls.filter(call => call.path === `${API}/push/check`)).toHaveLength(1);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('a delayed details module cannot reopen a stale trigger after the list renders', async ({ page }) => {
  const { diagnostics, modules, holdPath } = await mountEditor(page, { drafts: [makeDraft(101)] });
  const module = '/static/js/lq/dialogs.js';
  const release = holdPath(module);
  await detail(page, 101, 'reason').click();
  await expect.poll(() => modules.includes(module)).toBe(true);
  await article(page, 101).locator('[data-cse-locate="101"]').click();
  await expect(page.locator('[data-cse-drawer]')).toBeVisible();
  release();
  await page.evaluate(async () => { await (window as any).LQ.load('dialogs'); await new Promise(requestAnimationFrame); });
  await expect(visibleDialog(page)).toHaveCount(0);
  await detail(page, 101, 'reason').click();
  await expect(visibleDialog(page)).toHaveCount(1);
  await page.keyboard.press('Escape');
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('failed precheck retains the explicit save fallback and missing proofs retain upload guidance', async ({ page }) => {
  const draft = makeDraft(101, { proofs: [], reason: '' });
  const { diagnostics, calls } = await mountEditor(page, { drafts: [draft], checkStatus: 'failed' });
  await detail(page, 101, 'proofs').click();
  await expect(visibleDialog(page)).toContainText('暂无证明材料');
  await expect(visibleDialog(page)).toContainText('上传');
  await page.keyboard.press('Escape');
  await detail(page, 101, 'reason').click();
  await expect(visibleDialog(page)).toContainText('尚未填写');
  await page.keyboard.press('Escape');
  await page.locator('[data-cse-push]').click();
  await expect(page.locator('[data-cse-push-confirm]')).toHaveText('直接尝试保存');
  await expect(page.locator('[data-cse-push-confirm]')).toBeEnabled();
  await page.locator('[data-cse-push-confirm]').click();
  await expect(page.locator('[data-cse-push-cancel]')).toHaveText('完成');
  expect(calls.filter(call => call.path === `${API}/push`).map(call => call.body.draft_ids)).toEqual([[101]]);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('locate preserves target week, selected draft and locked pushed fields', async ({ page }) => {
  const { diagnostics } = await mountEditor(page);
  await article(page, 101).locator('[data-cse-locate="101"]').click();
  await expect(page.locator('.cse-week.is-active')).toHaveAttribute('data-cse-week', '3');
  await expect(page.locator('[data-cse-drawer]')).toBeVisible();
  await expect(page.locator('[data-cse-drawer] [data-cse-field="week"]')).toHaveValue('3');
  await expect(page.locator('[data-cse-drawer] [data-cse-field="reason"]')).toHaveValue(LONG_REASON);
  await article(page, 104).locator('[data-cse-locate="104"]').click();
  await expect(page.locator('[data-cse-drawer] [data-cse-field="reason"]')).toBeDisabled();
  await expect(page.locator('[data-cse-drawer] [data-cse-discard]')).toHaveCount(0);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('cancel does not write, discard and withdraw keep their individual draft IDs', async ({ page }) => {
  const { diagnostics, calls } = await mountEditor(page);
  await article(page, 101).locator('[data-cse-discard="101"]').click();
  await visibleDialog(page).getByRole('button', { name: '取消', exact: true }).click();
  expect(calls.filter(call => call.method !== 'GET')).toEqual([]);
  await article(page, 101).locator('[data-cse-discard="101"]').click();
  await visibleDialog(page).getByRole('button', { name: '撤销', exact: true }).click();
  await expect(article(page, 101)).toHaveCount(0);
  await expect(article(page, 102)).toHaveCount(1);
  await article(page, 104).locator('[data-cse-withdraw="104"]').click();
  await visibleDialog(page).getByRole('button', { name: '撤回', exact: true }).click();
  await expect(article(page, 104).locator('[data-cse-discard="104"]')).toHaveCount(1);
  await expect(article(page, 105).locator('[data-cse-withdraw="105"]')).toHaveCount(1);
  expect(calls.filter(call => call.method !== 'GET').map(call => [call.method, call.path]))
    .toEqual([['DELETE', `${API}/drafts/101`], ['POST', `${API}/drafts/104/withdraw`]]);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('pending precheck and push keep explicit state and save only clean IDs once', async ({ page }) => {
  const { diagnostics, calls, hold } = await mountEditor(page, { drafts: fixtureDrafts().slice(0, 3),
    verdicts: { 101: 'ok', 102: 'conflict', 103: 'hard' } });
  const releaseCheck = hold('/push/check');
  await page.locator('[data-cse-push]').click();
  await expect(page.locator('[data-cse-push-confirm]')).toBeDisabled();
  await expect.poll(() => calls.filter(call => call.path === `${API}/push/check`).length).toBe(1);
  releaseCheck();
  await expect(page.locator('[data-cse-push-confirm]')).toBeEnabled();
  await expect(page.locator('[data-cse-push-item="103"]')).toContainText('不能保存');
  const releasePush = hold('/push');
  await page.locator('[data-cse-push-confirm]').click();
  await expect(page.locator('[data-cse-push-confirm]')).toBeDisabled();
  await expect.poll(() => calls.filter(call => call.path === `${API}/push`).length).toBe(1);
  expect(calls.find(call => call.path === `${API}/push`)!.body).toMatchObject({ draft_ids: [101], force: false });
  releasePush();
  await expect(page.locator('[data-cse-push-dialog]')).toContainText('待提交');
  await expect(page.locator('[data-cse-push-cancel]')).toHaveText('完成');
  await page.locator('[data-cse-push-cancel]').click();
  await expect(article(page, 101).locator('[data-cse-withdraw]')).toHaveCount(1);
  await expect(article(page, 102).locator('[data-cse-discard]')).toHaveCount(1);
  expect(calls.filter(call => call.path === `${API}/push`)).toHaveLength(1);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});

test('missing credential explains the blocked save without losing local drafts', async ({ page }) => {
  const { diagnostics, calls } = await mountEditor(page, { drafts: [makeDraft(101)], checkStatus: 'missing_credential' });
  await page.locator('[data-cse-push]').click();
  await expect(page.locator('[data-cse-push-dialog]')).toContainText('未配置教务账号');
  await expect(page.locator('[data-cse-push-confirm]')).toBeDisabled();
  await page.locator('[data-cse-push-cancel]').click();
  await expect(article(page, 101)).toHaveCount(1);
  expect(calls.some(call => call.path === `${API}/push`)).toBe(false);
  expect(diagnostics).toEqual({ errors: [], unexpected: [] });
});
