import { test, expect, type Page, type TestInfo } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// No app, real database, credentials or external network. Exercise the actual
// immutable production graph against a deterministic in-memory API boundary.
type Node = { id: string; label: string; weight?: number; children?: Node[]; [key: string]: unknown };
type Tree = { title: string; summary: string; nodes: Node[]; [key: string]: unknown };
type State = { tree: Tree; completed_ids: string[]; version: number; [key: string]: any };
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
const leaves = (tree: Tree): Node[] => {
  const all: Node[] = [];
  const walk = (nodes: Node[]) => nodes.forEach(node => node.children?.length ? walk(node.children) : all.push(node));
  walk(tree.nodes); return all;
};
function initialState(canEdit = true): State {
  return recalculate({
    session_id: 21, version: 7, status: 'ready', has_tree: true, has_material: true,
    can_edit: canEdit, is_stale: canEdit, current_source_fingerprint: 'source-v2',
    source_fingerprint: 'source-v1', source_label: '网络原理学习文档', generated_at: '2026-10-20T12:00:00',
    completed_ids: ['a11'], tree: { title: '传输层', summary: '隔离合成知识树', nodes: [
      { id: 'a', label: '可靠传输', tone: 0, children: [
        { id: 'a1', label: '确认与重传', children: [
          { id: 'a11', label: '确认编号', weight: 15, detail: '明确下一段期望收到的数据。' },
          { id: 'a12', label: '超时重传', weight: 15 },
        ] },
        { id: 'a2', label: '滑动窗口', children: [
          { id: 'a21', label: '接收窗口', weight: 10 }, { id: 'a22', label: '零窗口探测', weight: 10 },
        ] },
      ] },
      { id: 'b', label: '连接管理', tone: 1, children: [
        { id: 'b1', label: '建立连接', children: [
          { id: 'b11', label: '三次握手', weight: 15 }, { id: 'b12', label: '初始序号', weight: 10 },
        ] },
        { id: 'b2', label: '关闭连接', children: [
          { id: 'b21', label: '四次挥手', weight: 15 }, { id: 'b22', label: '等待关闭', weight: 10 },
        ] },
      ] },
    ] },
  });
}
function recalculate(state: State): State {
  const nodes = leaves(state.tree), ids = new Set(state.completed_ids);
  state.completed_ids = nodes.filter(node => ids.has(node.id)).map(node => node.id);
  const total = nodes.reduce((sum, node) => sum + Number(node.weight || 0), 0);
  const done = nodes.reduce((sum, node) => sum + (ids.has(node.id) ? Number(node.weight || 0) : 0), 0);
  state.leaf_count = nodes.length; state.completed_count = state.completed_ids.length;
  state.progress = total ? Math.round(done / total * 100) : Math.round(state.completed_count / nodes.length * 100);
  state.lifecycle = !state.completed_count ? 'not_started' : state.completed_count === nodes.length ? 'completed' : 'in_progress';
  return state;
}

async function mount(page: Page, { canEdit = true, appearance = 'light' } = {}) {
  const graph = JSON.parse(fs.readFileSync('static/assets/manifest.json', 'utf8'));
  // A stale build must fail rather than silently testing a previous implementation.
  for (const source of Object.keys(graph.sourceHashes).filter(source => source.startsWith('js/knowledge_tree/') || ['js/session_knowledge_tree.js', 'css/session_knowledge_tree.css', 'css/tailwind-app.css'].includes(source))) {
    expect(createHash('sha256').update(fs.readFileSync(path.join('static', source))).digest('hex'), `${source} must match production graph`).toBe(graph.sourceHashes[source]);
  }
  const asset = (source: string) => '/static/' + graph.entries[source];
  const fixture = {
    state: initialState(canEdit), errors: [] as string[], unexpected: [] as string[], writes: [] as { method: string; path: string; body: any }[],
    failNext: 0, failAfterWrite: false, saveWait: null as Promise<void> | null, requests: [] as string[], graph: graph.revision,
  };
  await page.addInitScript(() => {
    (window as any).ktLongTasks = [];
    if (PerformanceObserver.supportedEntryTypes.includes('longtask')) {
      new PerformanceObserver(list => (window as any).ktLongTasks.push(...list.getEntries().map(entry => entry.duration))).observe({ type: 'longtask', buffered: true });
    }
  });
  page.on('pageerror', error => fixture.errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    fixture.requests.push(`${route.request().method()} ${url.pathname}`);
    if (url.origin !== 'https://knowledge-tree.test') { fixture.unexpected.push(url.href); await route.abort(); return; }
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static/assets', graph.revision) + path.sep) || !fs.existsSync(file)) {
        fixture.unexpected.push(url.pathname); await route.abort(); return;
      }
      const ext = path.extname(file);
      await route.fulfill({ contentType: ext === '.css' ? 'text/css' : ext === '.js' ? 'text/javascript' : ext === '.woff2' ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file) });
      return;
    }
    if (url.pathname.startsWith('/api/classrooms/')) {
      const method = route.request().method();
      if (method !== 'GET') {
        const body = route.request().postDataJSON();
        fixture.writes.push({ method, path: url.pathname, body });
        if (!canEdit) { await route.fulfill({ status: 403, json: { detail: '无编辑权限' } }); return; }
        if (fixture.saveWait) await fixture.saveWait;
        if (fixture.failNext) { const status = fixture.failNext; fixture.failNext = 0; await route.fulfill({ status, json: { detail: status === 409 ? '知识树已被其他窗口修改，请保留草稿后重新加载。' : '暂时无法保存，请重试。' } }); return; }
        if (url.pathname.endsWith('/draft')) {
          if (body.expected_version !== fixture.state.version) { await route.fulfill({ status: 409, json: { detail: '知识树版本冲突' } }); return; }
          fixture.state = recalculate({ ...fixture.state, tree: copy(body.tree), completed_ids: copy(body.completed_ids), version: fixture.state.version + 1 });
        } else if (url.pathname.includes('/leaves/')) {
          const id = url.pathname.split('/').at(-1)!;
          const done = new Set(fixture.state.completed_ids); if (body.done) done.add(id); else done.delete(id);
          fixture.state = recalculate({ ...fixture.state, completed_ids: [...done], version: fixture.state.version + 1 });
        } else if (url.pathname.endsWith('/bulk')) {
          fixture.state = recalculate({ ...fixture.state, completed_ids: body.done ? leaves(fixture.state.tree).map(node => node.id) : [], version: fixture.state.version + 1 });
        } else if (url.pathname.endsWith('/generate')) {
          fixture.state = { ...fixture.state, status: 'generating', version: fixture.state.version + 1 };
        } else { fixture.unexpected.push(`${method} ${url.pathname}`); await route.fulfill({ status: 500, json: { detail: 'Unexpected fixture write' } }); return; }
        if (fixture.failAfterWrite) { fixture.failAfterWrite = false; await route.fulfill({ status: 500, json: { detail: '已提交但响应丢失' } }); return; }
      }
      await route.fulfill({ json: copy(fixture.state) }); return;
    }
    if (url.pathname !== '/') { fixture.unexpected.push(url.pathname); await route.abort(); return; }
    await route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-lq-motion="off"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="${asset('css/tailwind-app.css')}"><link rel="stylesheet" href="${asset('css/session_knowledge_tree.css')}">
      <body><button type="button" id="open">打开知识树</button><script type="module">
      import {openKnowledgeTree} from '${asset('js/session_knowledge_tree.js')}';
      window.changes=[];window.closes=0;
      document.getElementById('open').onclick=()=>{window.treeController=openKnowledgeTree({classOfferingId:12,session:{id:21,title:'网络原理 第9次课'},canEdit:${canEdit},trigger:document.getElementById('open'),onChange:(id,state)=>window.changes.push(state),onClose:()=>window.closes++})};
      window.ready=true;</script></body></html>` });
  });
  await page.goto('https://knowledge-tree.test/');
  await page.waitForFunction(() => (window as any).ready);
  await page.locator('#open').click();
  await expect(page.locator('.kt-node')).toHaveCount(15);
  await expect(page.locator('.kt-edges path')).toHaveCount(14);
  await expect(page.locator('.kt-empty')).toBeHidden();
  return fixture;
}
const node = (page: Page, id: string) => page.locator(`.kt-node[data-node-id="${id}"]`);
async function rename(page: Page, id: string, label: string) {
  await node(page, id).locator('.kt-node__label').click();
  await page.locator('[data-kt-edit]').click();
  await page.locator('[data-kt-field="label"]').fill(label);
  await page.locator('[data-kt-edit-save]').click();
  await expect(node(page, id).locator('.kt-node__label')).toHaveText(label);
}
async function undo(page: Page) {
  await page.locator('.kt-viewport').focus();
  await page.keyboard.press('Control+z');
}
async function close(page: Page) {
  await page.getByRole('button', { name: '关闭知识树', exact: true }).click();
}
function clean(fixture: Awaited<ReturnType<typeof mount>>) {
  expect(fixture.errors).toEqual([]); expect(fixture.unexpected).toEqual([]);
}
async function diagnostics(page: Page, fixture: Awaited<ReturnType<typeof mount>>, testInfo: TestInfo) {
  const sampled = await page.evaluate(() => {
    const hosts = [...document.querySelectorAll<HTMLElement>('body *')].filter(element => {
      const style = getComputedStyle(element), rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.backdropFilter !== 'none';
    }).map(element => ({ className: element.className, filter: getComputedStyle(element).backdropFilter }));
    const durations = (window as any).ktLongTasks as number[];
    return { visibleBackdropHosts: hosts, longTasks: { count: durations.length, maximumMs: Math.max(0, ...durations), totalMs: durations.reduce((a, b) => a + b, 0) } };
  });
  const report = { graph: fixture.graph, ...sampled, requests: { total: fixture.requests.length, api: fixture.requests.filter(item => item.includes('/api/')), writes: fixture.writes.length }, errors: fixture.errors, unexpected: fixture.unexpected };
  fs.writeFileSync(testInfo.outputPath('runtime-diagnostics.json'), JSON.stringify(report, null, 2));
  await testInfo.attach('runtime-diagnostics', { body: JSON.stringify(report, null, 2), contentType: 'application/json' });
}

test('knowledge tree update notice dismisses per source version and leaves regeneration available', async ({ page }) => {
  const fixture = await mount(page);
  await expect(page.locator('.kt-notice')).toContainText('学习文档已更新');
  await page.locator('[data-kt-dismiss-update]').click();
  await expect(page.locator('.kt-notice')).toBeHidden();
  await expect(page.locator('[data-kt-generate]')).toBeEnabled();
  await close(page); await expect(page.locator('.kt-dialog')).toBeHidden();
  await page.locator('#open').click(); await expect(page.locator('.kt-node')).toHaveCount(15);
  await expect(page.locator('.kt-notice')).toBeHidden();
  fixture.state.current_source_fingerprint = 'source-v3';
  await page.evaluate(() => (window as any).treeController.refresh());
  await expect(page.locator('.kt-notice')).toContainText('学习文档已更新');
  expect(fixture.writes).toHaveLength(0); clean(fixture);
});

test('knowledge tree ordinary wheel zoom keeps the pointer anchored and blank left drag pans', async ({ page }) => {
  const fixture = await mount(page);
  const viewport = page.locator('.kt-viewport');
  const rect = (await viewport.boundingBox())!;
  const pointer = { x: rect.x + rect.width * 0.55, y: rect.y + rect.height * 0.55 };
  const spot = () => page.locator('.kt-canvas').evaluate((canvas, p) => {
    const rect = canvas.getBoundingClientRect(), zoom = new DOMMatrixReadOnly(getComputedStyle(canvas).transform).a;
    return { x: (p.x - rect.left) / zoom, y: (p.y - rect.top) / zoom, zoom };
  }, pointer);
  const before = await spot();
  await page.mouse.move(pointer.x, pointer.y); await page.mouse.wheel(0, -120);
  await expect.poll(async () => (await spot()).zoom).toBeGreaterThan(before.zoom);
  const after = await spot();
  expect(Math.abs(after.x - before.x)).toBeLessThan(2);
  expect(Math.abs(after.y - before.y)).toBeLessThan(2);
  const modified = await viewport.evaluate(view => {
    const event = new WheelEvent('wheel', { deltaY: -120, ctrlKey: true, bubbles: true, cancelable: true });
    view.dispatchEvent(event); return event.defaultPrevented;
  });
  expect(modified).toBe(false);
  await page.evaluate(() => new Promise(requestAnimationFrame));
  expect((await spot()).zoom).toBe(after.zoom);
  // Put the origin away from scroll boundaries, then use an actual blank hit.
  const blank = await viewport.evaluate(view => {
    for (let y = 20; y < view.clientHeight - 30; y += 30) for (let x = 20; x < view.clientWidth - 30; x += 30) {
      const r = view.getBoundingClientRect(), hit = document.elementFromPoint(r.left + x, r.top + y);
      if (hit && view.contains(hit) && !hit.closest('.kt-node, .kt-fold')) return { x: r.left + x, y: r.top + y };
    }
    throw new Error('No blank canvas hit');
  });
  const origin = await page.locator('.kt-canvas').boundingBox();
  await page.mouse.move(blank.x, blank.y); await page.mouse.down();
  await expect(viewport).toHaveClass(/is-panning/);
  await page.mouse.move(blank.x + 45, blank.y + 35, { steps: 5 });
  await page.mouse.up(); await expect(viewport).not.toHaveClass(/is-panning/);
  const moved = await page.locator('.kt-canvas').boundingBox();
  expect(Math.abs(moved!.x - origin!.x) + Math.abs(moved!.y - origin!.y)).toBeGreaterThan(10);
  clean(fixture);
});

test('knowledge tree edits, add and delete are local, undoable and saved once with expected version', async ({ page }, testInfo) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '确认编号已修改');
  expect(fixture.writes).toHaveLength(0);
  await undo(page); await expect(node(page, 'a11')).toContainText('确认编号');
  await expect(node(page, 'a11')).not.toContainText('已修改');
  await node(page, 'a1').click(); await page.locator('[data-kt-add-child]').click();
  await expect(page.locator('.kt-node')).toHaveCount(16);
  await page.locator('[data-kt-edit-cancel]').click();
  await undo(page); await expect(page.locator('.kt-node:not(.is-leaving)')).toHaveCount(15);
  await node(page, 'a1').click(); await page.locator('[data-kt-delete]').click();
  await page.getByRole('button', { name: '删除', exact: true }).last().click();
  await expect(node(page, 'a11')).toHaveCount(0);
  await undo(page); await expect(node(page, 'a11')).toHaveCount(1);
  await expect(node(page, 'a11')).toHaveAttribute('data-done', 'true');
  await rename(page, 'a11', '确认序号已保存');
  const canvasBox = (await page.locator('.kt-viewport').boundingBox())!;
  const sideBox = (await page.locator('.kt-side').boundingBox())!;
  expect(sideBox.x).toBeGreaterThan(canvasBox.x + canvasBox.width);
  expect(Math.abs(sideBox.y - canvasBox.y)).toBeLessThan(2);
  await page.screenshot({ path: testInfo.outputPath('knowledge-tree-teacher-draft.png') });
  await diagnostics(page, fixture, testInfo);
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save]')).toBeDisabled();
  expect(fixture.writes).toHaveLength(1);
  expect(fixture.writes[0]).toMatchObject({ method: 'PUT', body: { expected_version: 7 } });
  expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('确认序号已保存');
  await close(page); await page.locator('#open').click();
  await expect(node(page, 'a11')).toContainText('确认序号已保存');
  clean(fixture);
});

for (const status of [500, 409]) test(`knowledge tree ${status} keeps the draft and undo history without an implicit overwrite`, async ({ page }) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '失败后保留的草稿');
  fixture.failNext = status;
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save-status]')).toContainText(status === 409 ? /冲突|其他|更新/ : /失败|重试/);
  await expect(node(page, 'a11')).toContainText('失败后保留的草稿');
  expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('确认编号');
  await expect(page.locator('[data-kt-undo]')).toBeEnabled();
  expect(fixture.writes).toHaveLength(1);
  if (status === 500) {
    await page.locator('[data-kt-save]').click();
    await expect(page.locator('[data-kt-save]')).toBeDisabled();
    expect(fixture.writes).toHaveLength(2);
    expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('失败后保留的草稿');
  } else {
    await undo(page); await expect(node(page, 'a11')).not.toContainText('失败后');
  }
  clean(fixture);
});

test('knowledge tree pending text joins explicit save and immediate progress does not erase a draft', async ({ page }) => {
  const fixture = await mount(page);
  await node(page, 'a12').locator('.kt-node__check').click();
  await expect(node(page, 'a12')).toHaveAttribute('data-done', 'true');
  await expect.poll(() => fixture.writes.length).toBe(1);
  await rename(page, 'a11', '已有本地修改');
  await node(page, 'a21').locator('.kt-node__check').click();
  await expect(node(page, 'a21')).toHaveAttribute('data-done', 'true');
  expect(fixture.writes).toHaveLength(1);
  await node(page, 'a11').locator('.kt-node__label').click();
  await page.locator('[data-kt-edit]').click();
  await page.locator('[data-kt-field="label"]').fill('顶部保存收录未应用表单');
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save]')).toBeDisabled();
  expect(fixture.writes).toHaveLength(2);
  expect(fixture.writes[1].body.expected_version).toBe(8);
  expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('顶部保存收录未应用表单');
  expect(fixture.state.completed_ids).toEqual(expect.arrayContaining(['a11', 'a12', 'a21']));
  clean(fixture);
});

test('knowledge tree a saved edit remains undoable and undo is saved against the new version', async ({ page }) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '保存过的名称');
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save-status]')).toContainText('已保存');
  await undo(page);
  await expect(node(page, 'a11')).toContainText('确认编号');
  await expect(page.locator('[data-kt-save]')).toBeEnabled();
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save-status]')).toContainText('已保存');
  expect(fixture.writes).toHaveLength(2);
  expect(fixture.writes[1].body.expected_version).toBe(8);
  expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('确认编号');
  clean(fixture);
});

test('knowledge tree native text undo and IME do not consume structural history', async ({ page }) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '结构修改');
  await page.locator('[data-kt-edit]').click();
  const input = page.locator('[data-kt-field="label"]');
  await input.focus(); await page.keyboard.press('End'); await page.keyboard.type(' ABC');
  await page.keyboard.press('Control+z');
  // Chromium can group native keystrokes as a single undo or several edits.
  await expect(input).not.toHaveValue('结构修改 ABC');
  await expect(input).toHaveValue(/^结构修改/);
  await input.dispatchEvent('compositionstart');
  await input.dispatchEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, bubbles: true });
  await expect(input).toBeVisible();
  await input.dispatchEvent('compositionend');
  await page.locator('[data-kt-edit-cancel]').click();
  await undo(page); await expect(node(page, 'a11')).toContainText('确认编号');
  expect(fixture.writes).toHaveLength(0); clean(fixture);
});

test('knowledge tree dirty close can continue, discard, or save and return focus', async ({ page }) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '待保存'); await close(page);
  await expect(page.getByRole('heading', { name: '保存知识树修改？' })).toBeVisible();
  await page.getByRole('button', { name: '继续编辑', exact: true }).click();
  await expect(node(page, 'a11')).toContainText('待保存');
  await close(page); await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page.locator('.kt-dialog')).toBeHidden(); await expect(page.locator('#open')).toBeFocused();
  expect(fixture.writes).toHaveLength(0);
  await page.locator('#open').click(); await expect(node(page, 'a11')).toContainText('确认编号');
  await rename(page, 'a11', '关闭前保存'); await close(page);
  await page.getByRole('button', { name: '保存并关闭', exact: true }).click();
  await expect(page.locator('.kt-dialog')).toBeHidden(); await expect(page.locator('#open')).toBeFocused();
  expect(fixture.writes).toHaveLength(1);
  expect(leaves(fixture.state.tree).find(node => node.id === 'a11')!.label).toBe('关闭前保存');
  clean(fixture);
});

test('knowledge tree saving blocks duplicate submits and closing until the response', async ({ page }) => {
  const fixture = await mount(page);
  await rename(page, 'a11', '等待保存');
  let release!: () => void; fixture.saveWait = new Promise(resolve => { release = resolve; });
  await page.locator('[data-kt-save]').click();
  await expect(page.locator('[data-kt-save]')).toBeDisabled();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await close(page); await expect(page.locator('.kt-dialog')).toBeVisible();
  release(); fixture.saveWait = null;
  await expect(page.locator('[data-kt-save-status]')).toContainText(/已保存|没有未保存/);
  expect(fixture.writes).toHaveLength(1); clean(fixture);
});

test('knowledge tree generating request locks edits and duplicate submissions before its delayed response', async ({ page }) => {
  const fixture = await mount(page);
  let release!: () => void; fixture.saveWait = new Promise(resolve => { release = resolve; });
  await page.locator('[data-kt-generate]').click();
  await page.getByRole('button', { name: '重新生成', exact: true }).last().click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await expect(page.locator('[data-kt-generate]')).toBeDisabled();
  await node(page, 'a11').locator('.kt-node__label').click();
  await expect(page.locator('[data-kt-edit]')).toBeDisabled();
  await node(page, 'a11').focus(); await page.keyboard.press('F2');
  await expect(page.locator('[data-kt-edit-form]')).toHaveCount(0);
  await page.locator('[data-kt-generate]').dispatchEvent('click');
  expect(fixture.writes).toHaveLength(1);
  await close(page); await expect(page.locator('.kt-dialog')).toBeVisible();
  release(); fixture.saveWait = null;
  await expect(page.locator('.kt-notice')).toContainText('AI 正在');
  await close(page); await expect(page.locator('.kt-dialog')).toBeHidden();
  fixture.state = { ...fixture.state, status: 'ready', version: fixture.state.version + 1 };
  await page.evaluate(() => (window as any).treeController.refresh());
  await expect(page.locator('.kt-dialog')).toBeHidden();
  expect(fixture.writes).toHaveLength(1); clean(fixture);
});

test('knowledge tree reads confirmed progress back after a committed write loses its response', async ({ page }) => {
  const fixture = await mount(page); fixture.failAfterWrite = true;
  await node(page, 'a12').locator('.kt-node__check').click();
  await expect.poll(() => fixture.writes.length).toBe(1);
  await expect.poll(() => fixture.requests.filter(item => item.startsWith('GET /api/')).length).toBe(2);
  await expect(node(page, 'a12')).toHaveAttribute('data-done', 'true');
  await expect(page.locator('.kt-progress__title')).toContainText('30%');
  expect(fixture.state.completed_ids).toContain('a12');
  clean(fixture);
});

for (const appearance of ['light', 'dark']) test(`knowledge tree narrow touch layout and student read-only ${appearance}`, async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const fixture = await mount(page, { canEdit: false, appearance });
  await expect(page.locator('[data-kt-save], [data-kt-undo], [data-kt-generate], [data-kt-bulk]')).toHaveCount(0);
  await node(page, 'a').tap();
  await expect(page.locator('.kt-side')).toBeVisible();
  await expect(page.locator('[data-kt-edit], [data-kt-delete]')).toHaveCount(0);
  await page.locator('.kt-viewport').focus(); await page.keyboard.press('Control+z'); await page.keyboard.press('Delete');
  expect(fixture.writes).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`knowledge-tree-student-${appearance}.png`) });
  await diagnostics(page, fixture, testInfo);
  clean(fixture); await context.close();
});

test('knowledge tree narrow teacher can edit, undo and save without clipping header actions', async ({ browser }, testInfo) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, reducedMotion: 'reduce' });
  const page = await context.newPage(); const fixture = await mount(page, { appearance: 'dark' });
  await rename(page, 'a11', '触屏编辑');
  const title = (await page.locator('.kt-dialog > .lq-dialog__head h2').boundingBox())!;
  const dismiss = (await page.getByRole('button', { name: '关闭知识树', exact: true }).boundingBox())!;
  expect(title.width).toBeGreaterThanOrEqual(120);
  expect(Math.abs((title.y + title.height / 2) - (dismiss.y + dismiss.height / 2))).toBeLessThan(30);
  for (const selector of ['[data-kt-save]', '[data-kt-undo]', '[data-kt-generate]']) {
    const box = (await page.locator(selector).boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0); expect(box.x + box.width).toBeLessThanOrEqual(391);
  }
  await page.locator('[data-kt-undo]').tap(); await expect(node(page, 'a11')).toContainText('确认编号');
  await rename(page, 'a11', '触屏保存'); await page.locator('[data-kt-save]').tap();
  await expect(page.locator('[data-kt-save-status]')).toContainText('已保存');
  expect(fixture.writes).toHaveLength(1);
  await page.screenshot({ path: testInfo.outputPath('knowledge-tree-teacher-touch-dark.png') });
  await diagnostics(page, fixture, testInfo);
  clean(fixture); await context.close();
});
