import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { collectComponentDom } from '../fixtures/lq-component-audit';

test('component DOM audit preserves hidden controls and rejects declarations without shared structure', async ({ page }) => {
  await page.setContent(`<button id="bare" data-lq-component="button">Bare</button>
    <button id="owned" data-lq-component="button" class="lq-btn domain-save">Save</button>
    <section hidden><select id="hidden" data-lq-component="select" class="lq-select"></select></section>
    <div id="empty-layer" data-lq-component="layer" class="lq-domain-region"></div>
    <div id="layer" data-lq-component="layer" class="lq-domain-region"><section data-lq-component="surface" class="lq-surface"></section></div>
    <textarea id="content" data-lq-component="content-slot" class="lq-domain-content-slot"></textarea>`);
  const [frame] = await collectComponentDom(page, [{ file: 'templates/example.html', line: 7, kind: 'button', id: 'owned', class: 'lq-btn domain-save', suggestedOwner: 'domain/example', auditId: 'example' }]);
  expect(frame.status).toBe('measured');
  const records = 'records' in frame ? frame.records : [];
  expect(records.find(row => row.id === 'bare')?.ownership).toBe('declared-missing-canonical');
  expect(records.find(row => row.id === 'owned')?.sourceMapping.status).toBe('candidate-pending');
  expect(records.find(row => row.id === 'hidden')?.hidden).toBe(true);
  expect(records.find(row => row.id === 'empty-layer')?.structure).toBe('layer-content-surface-missing');
  expect(records.find(row => row.id === 'layer')?.structure).toBe('content-surface-present-pending');
  expect(records.find(row => row.id === 'content')?.kind).toBe('content-slot');
});

test('classic resume factories adopt shared controls while preserving confirmation events and field identity', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://resume-contract.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      return route.abort();
    }
    return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><meta charset="utf-8"><body><form id="f"></form><script src="/static/js/resume_common.js"></script></body></html>' });
  });
  await page.goto('http://resume-contract.test/');
  await page.evaluate(() => {
    const rz = (window as any).RZ;
    (window as any).confirmCount = 0;
    rz.confirmDialog('Test', () => (window as any).confirmCount++);
    const input = document.createElement('input'); input.type = 'text'; input.id = 'kept'; input.name = 'draft'; input.value = 'Original'; input.setAttribute('form', 'f');
    document.body.append(input); (window as any).kept = input;
    (window as any).inputEvents = 0; input.addEventListener('input', () => (window as any).inputEvents++);
    rz.adoptControl(input, { kind: 'input' });
  });
  const confirm = page.getByRole('button', { name: '确定删除', exact: true });
  await expect(confirm).toHaveClass(/lq-btn--destructive/);
  await expect(page.locator('#kept')).toHaveClass(/lq-input/);
  expect(await page.evaluate(() => {
    const input = document.querySelector('#kept') as HTMLInputElement;
    return { same: input === (window as any).kept, value: input.value, name: input.name, form: input.form?.id, events: (window as any).inputEvents };
  })).toEqual({ same: true, value: 'Original', name: 'draft', form: 'f', events: 0 });
  await confirm.click();
  expect(await page.evaluate(() => (window as any).confirmCount)).toBe(1);
  expect(errors).toEqual([]);
});

test('actual shared factories and raised drawers are recognized without accepting bare markers', async ({ page }) => {
  await page.route('http://component-audit.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      return route.abort();
    }
    return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><meta charset="utf-8"><body></body></html>' });
  });
  await page.goto('http://component-audit.test/');
  await page.evaluate(async () => {
    const { createComponent } = await import(String('/static/js/lq/components.js'));
    const { createContent } = await import(String('/static/js/lq/content.js'));
    const { createForm } = await import(String('/static/js/lq/forms.js'));
    const examples: [string, any][] = [['button', { label: 'Save' }], ['chip', { label: 'Filter', kind: 'filter' }], ['badge', { value: 2 }], ['avatar', { name: 'Test' }], ['spinner', {}], ['progress', { label: 'Progress', value: 40 }], ['skeleton', {}]];
    for (const [kind, props] of examples) document.body.append(createComponent(kind, props));
    for (const [kind, props] of [['card', { title: 'Card' }], ['empty', {}], ['page_head', { title: 'Page' }], ['list', { label: 'Items', items: [{ title: 'Row' }] }]] as [string, any][]) {
      const node = createContent(kind, props); node.dataset.lqComponent = kind; document.body.append(node);
    }
    for (const kind of ['input', 'textarea', 'select', 'checkbox', 'radio', 'range', 'switch']) document.body.append(createForm(kind, { id: `test-${kind}`, label: kind, name: kind }));
    const root = document.createElement('div'); root.className = 'lq-domain-region'; root.dataset.lqComponent = 'layer'; root.id = 'drawer-layer';
    root.innerHTML = '<section id="drawer" class="lq-domain-raised" data-lq-component="drawer" data-lq-material="raised"></section>';
    document.body.append(root);
    const invalid = document.createElement('section'); invalid.id = 'invalid-drawer'; invalid.className = 'lq-domain-raised'; invalid.dataset.lqComponent = 'drawer'; document.body.append(invalid);
  });
  const [frame] = await collectComponentDom(page);
  const records = 'records' in frame ? frame.records : [];
  expect(records.filter(row => row.ownership === 'declared-missing-canonical').map(row => row.id)).toEqual(['invalid-drawer']);
  expect(records.filter(row => row.kind === 'unknown')).toEqual([]);
  expect(records.find(row => row.id === 'drawer-layer')?.structure).toBe('content-surface-present-pending');
});

test('document content stays explicit and generated empty pseudo blur is measured', async ({ page }) => {
  await page.route('http://frame-audit.test/**', route => {
    const url = new URL(route.request().url());
    return route.fulfill({ contentType: 'text/html', body: url.pathname === '/'
      ? '<!doctype html><style>.host{display:block;width:30px;height:30px}.host::before{content:"";display:block;width:30px;height:30px;backdrop-filter:blur(4px)}</style><div id="paint" class="host"></div><button data-lq-component="region" class="lq-domain-region"><span id="leaf-paint" class="host"></span></button><label class="afm-search--dialog"><input class="lq-input" data-lq-component="input"></label><iframe src="/materials/render/3/"></iframe><iframe src="/document-preview/platform"></iframe>'
      : '<button>Original content action</button>' });
  });
  await page.goto('http://frame-audit.test/');
  const frames = await collectComponentDom(page);
  const main = frames.find(frame => frame.url.endsWith('/'))!;
  expect('records' in main && main.records.some(record => record.class === 'afm-search--dialog')).toBe(false);
  expect('blurHosts' in main && main.blurHosts.some(host => host.id === 'paint' && host.pseudo === '::before')).toBe(true);
  expect('blurHosts' in main && main.blurHosts.some(host => host.id === 'leaf-paint' && host.leaf)).toBe(true);
  const authored = frames.find(frame => frame.url.includes('/materials/render/'))!;
  const platform = frames.find(frame => frame.url.includes('/document-preview/'))!;
  expect('scope' in authored && authored.scope).toBe('document-content');
  expect('records' in authored && authored.records.length).toBe(1);
  expect('scope' in platform && platform.scope).toBe('platform-ui');
  expect('totals' in platform && platform.totals.unowned).toBe(1);
});

test('graph choice recipe requires the real SVG keyboard interaction boundary', async ({ page }) => {
  await page.setContent('<svg><g id="graph" data-lq-component="choice" class="lq-domain-graph-choice" role="button" tabindex="0" aria-label="Choose"><circle cx="20" cy="20" r="8" /></g></svg><div id="fake" data-lq-component="choice" class="lq-domain-graph-choice" role="button" tabindex="0"></div>');
  const [frame] = await collectComponentDom(page);
  const records = 'records' in frame ? frame.records : [];
  expect(records.find(row => row.id === 'graph')?.ownership).toBe('canonical-declared-pending');
  expect(records.find(row => row.id === 'fake')?.ownership).toBe('declared-missing-canonical');
});

test('overflow diagnostics distinguish document spill from a contained scrolling region', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.setContent('<style>body{margin:0}</style><div id="spill" style="width:520px;height:20px"></div>');
  const [spilling] = await collectComponentDom(page);
  expect('overflow' in spilling && spilling.overflow).toBe(130);
  expect('overflowCandidates' in spilling && spilling.overflowCandidates.some(node => node.id === 'spill')).toBe(true);
  await page.setContent('<style>body{margin:0}</style><div style="width:200px;overflow:auto"><div id="scroll-content" style="width:700px;height:20px"></div></div>');
  const [contained] = await collectComponentDom(page);
  expect('overflow' in contained && contained.overflow).toBe(0);
  expect('overflowCandidates' in contained && contained.overflowCandidates.some(node => node.id === 'scroll-content')).toBe(true);
});
