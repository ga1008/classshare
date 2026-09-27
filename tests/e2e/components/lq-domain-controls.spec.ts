import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page) {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('https://domain-controls.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected fixture asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file) });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh" data-theme="lanshare" data-appearance="light" data-ui-palette="indigo" data-lq-glass="tinted" data-lq-tier="A"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/lessondoc_editor.css"><style>body{padding:24px}#fields{max-width:380px}#board{position:fixed;inset:0;pointer-events:none}#board .twb-toolbar{pointer-events:auto}</style></head><body><main id="fields"></main><div id="board" class="teacher-whiteboard-root twb-root is-open" hidden></div><script type="module">
      import * as domain from '/static/js/lq/domain-controls.js';
      import * as ui from '/static/js/lessondoc_editor/ui.js';
      import * as wb from '/static/js/whiteboard/panels/controls.js';
      import {buildToolbarHtml} from '/static/js/whiteboard/toolbar.js';
      import {createPopover,popoverManager} from '/static/js/whiteboard/popover.js';
      import {ExamDrawingWhiteboard} from '/static/js/whiteboard/exam_board.js';
      import {textEditorMixin} from '/static/js/whiteboard/text_editor.js';
      import {createEmojiPicker} from '/static/js/emoji_picker.js';
      import {decoratePreviewCodeBlocks} from '/static/js/file_preview.js';
      import {ClassroomPrivateMessages} from '/static/js/classroom_private_messages.js';
      import {SignaturePointControl} from '/static/js/signature_point_workflow.js';
      window.fixture={domain,ui,wb,buildToolbarHtml,createPopover,popoverManager,ExamDrawingWhiteboard,textEditorMixin,createEmojiPicker,decoratePreviewCodeBlocks,ClassroomPrivateMessages,SignaturePointControl};window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://domain-controls.test/'); await page.waitForFunction(() => (window as any).ready);
  return errors;
}

test('LQ domain adoption preserves native form identity, values, state and event ownership', async ({ page }) => {
  const errors = await mount(page);
  const result = await page.evaluate(() => {
    const { domain } = (window as any).fixture, form = document.createElement('form'); form.id = 'owner';
    const input = document.createElement('input'); input.type = 'checkbox'; input.name = 'accepted'; input.value = 'original'; input.checked = true; input.indeterminate = true; input.disabled = true; input.hidden = true;
    form.append(input); document.body.append(form); let changes = 0; input.addEventListener('change', () => changes++);
    const adopted = domain.adoptDomainControl(input); domain.adoptDomainControl(input);
    const preserved = adopted === input && input.form === form && input.type === 'checkbox' && input.name === 'accepted' && input.value === 'original' && input.checked && input.indeterminate && input.disabled && input.hidden;
    input.dispatchEvent(new Event('change'));
    const choice = domain.createDomainButton({ className: 'fixture-choice', kind: 'choice', attrs: { 'aria-label': '布局样本' } });
    const sample = document.createElement('strong'); sample.textContent = '保留内容'; choice.append(sample);
    domain.adoptDomainControl(choice, { kind: 'choice' });
    return { preserved, changes, kind: input.dataset.lqComponent, className: input.className, choiceName: choice.getAttribute('aria-label'), sameSlot: choice.firstChild === sample };
  });
  expect(result).toEqual({ preserved: true, changes: 1, kind: 'checkbox', className: 'lq-checkbox', choiceName: '布局样本', sameSlot: true });
  expect(errors).toEqual([]);
});

test('domain button names follow live labels and canvas slots preserve exact editing geometry', async ({ page }) => {
  const errors = await mount(page);
  const result = await page.evaluate(() => {
    const {domain,textEditorMixin,ExamDrawingWhiteboard} = (window as any).fixture;
    const button = domain.createDomainButton({label:'保存'}); document.getElementById('fields')!.append(button); button.textContent = '保存中…';
    const handle = document.createElement('button'); handle.style.cssText = 'position:absolute;width:12px;height:12px;padding:0;border:0;background:transparent';
    domain.adoptDomainHandle(handle,{kind:'resize'}); document.body.append(handle);
    const stage = document.createElement('div'); document.body.append(stage);
    const editor: any = { ...textEditorMixin, worldToScreen:(p:any)=>p, canvasWidth:800, canvasHeight:600, settings:{textColor:'#ff0000',fontSize:27},
      viewport:{scale:1.5}, stageEl:stage };
    editor.openTextEditor({x:60,y:80}); const node = editor.textEditor.element;
    const slot = {component:node.dataset.lqComponent, inputPaint:node.classList.contains('lq-textarea'), left:node.style.left, top:node.style.top,
      fontSize:node.style.fontSize, color:node.style.color, worldSize:editor.textEditor.fontSize};
    editor.closeTextEditor(); const drawing = new ExamDrawingWhiteboard().init();
    const native = [...drawing.rootEl.querySelectorAll('button,input')].every((node:any)=>Boolean(node.dataset.lqComponent));
    const geometry = {width:getComputedStyle(handle).width,height:getComputedStyle(handle).height,component:handle.dataset.lqComponent,shape:handle.dataset.lqShape,pill:handle.classList.contains('lq-btn')};
    return {nameOverride:button.getAttribute('aria-label'),slot,geometry,native};
  });
  expect(result).toEqual({nameOverride:null,slot:{component:'content-slot',inputPaint:false,left:'60px',top:'80px',fontSize:'27px',color:'rgb(255, 0, 0)',worldSize:18},
    geometry:{width:'12px',height:'12px',component:'handle',shape:'surface',pill:false},native:true});
  await expect(page.getByRole('button',{name:'保存中…'})).toBeVisible();
  expect(errors).toEqual([]);
});

test('LQ inspector fields keep range preview separate from commit, mixed values and visual selection', async ({ page }) => {
  const errors = await mount(page);
  const result = await page.evaluate(() => {
    const { ui } = (window as any).fixture, host = document.getElementById('fields')!, previews: number[] = [], commits: number[] = [], selected: string[] = [];
    const range = ui.field('不透明度', null, (v: number) => commits.push(v), { type: 'number', min: 0, max: 100, mixed: true, preview: (v: number) => previews.push(v) });
    const mixed = ui.field('混合选择', null, () => {}, { type: 'checkbox', mixed: true });
    const visuals = ui.field('布局', 'a', (v: string) => selected.push(v), { choices: { a: '左右', b: '上下' }, visual: 'align' });
    host.append(range, mixed, visuals); const input = range.querySelector('input')!;
    const initial = range.querySelector('output')!.value; input.focus(); input.value = '65'; input.dispatchEvent(new Event('input'));
    const during = { previews: [...previews], commits: [...commits], focused: document.activeElement === input, valueText: input.getAttribute('aria-valuetext') };
    input.dispatchEvent(new Event('change')); visuals.querySelectorAll('button')[1].click();
    return { initial, during, commits, rangeKind: input.dataset.lqComponent, mixed: mixed.querySelector('input')!.indeterminate,
      selected, choices: [...visuals.querySelectorAll('button')].map((b: any) => [b.dataset.lqComponent, b.getAttribute('aria-pressed'), b.querySelector('.lde-choice-sample') !== null]) };
  });
  expect(result).toEqual({ initial: '混合值', during: { previews: [65], commits: [], focused: true, valueText: '65' }, commits: [65], rangeKind: 'range', mixed: true,
    selected: ['b'], choices: [['choice', 'false', true], ['choice', 'true', true]] });
  expect(errors).toEqual([]);
});

test('LQ color picker keeps nested dialog ownership and cancel restores the last committed color', async ({ page }) => {
  const errors = await mount(page);
  await page.evaluate(() => {
    const w = window as any, { ui } = w.fixture; w.colors = { previews: [], commits: [] };
    ui.dialog('背景设置', ({ body }: { body: HTMLElement }) => body.append(ui.field('背景颜色', '#ff0000', (v: string) => w.colors.commits.push(v), { type: 'color', preview: (v: string) => w.colors.previews.push(v) })));
  });
  await page.locator('.ls-color-trigger').click(); await expect(page.locator('.ls-color-picker')).toBeVisible();
  await expect(page.locator('.lde-dialog')).toBeVisible();
  await expect(page.locator('.ls-color-trigger')).toHaveAttribute('data-lq-visual', 'color');
  await page.locator('.ls-color-hue').evaluate((node: HTMLInputElement) => { node.value = '120'; node.dispatchEvent(new Event('input')); });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  expect(await page.evaluate(() => (window as any).colors.commits)).toEqual([]);
  await page.locator('.ls-color-plane').dispatchEvent('pointercancel');
  expect(await page.evaluate(() => (window as any).colors.previews.at(-1))).toBe('#ff0000');
  await page.locator('.ls-color-exact input').fill('#0000ff'); await page.locator('.ls-color-exact input').press('Enter');
  expect(await page.evaluate(() => (window as any).colors.commits)).toEqual(['#0000ff']);
  await expect(page.locator('.ls-color-chip').first()).toHaveAttribute('data-lq-component', 'choice');
  await page.keyboard.press('Escape'); await expect(page.locator('.ls-color-picker')).not.toBeVisible(); await expect(page.locator('.lde-dialog')).toBeVisible();
  await page.keyboard.press('Escape'); await expect(page.locator('.lde-dialog')).not.toBeVisible();
  expect(errors).toEqual([]);
});

test('LQ whiteboard toolbar retains every tool/action and compact responsive bounds', async ({ page }) => {
  const errors = await mount(page);
  const contract = await page.evaluate(() => {
    const { buildToolbarHtml } = (window as any).fixture, board = document.getElementById('board')!; board.hidden = false; board.innerHTML = buildToolbarHtml();
    const buttons = [...board.querySelectorAll('button')];
    return { allDeclared: buttons.every(node => node.classList.contains('lq-btn') && ['button', 'choice'].includes(node.dataset.lqComponent!)),
      tools: buttons.flatMap(node => node.dataset.whiteboardTool ? [node.dataset.whiteboardTool] : []),
      shapes: buttons.flatMap(node => node.dataset.whiteboardShape ? [node.dataset.whiteboardShape] : []),
      actions: buttons.flatMap(node => node.dataset.whiteboardAction ? [node.dataset.whiteboardAction] : []),
      names: buttons.every(node => Boolean(node.getAttribute('aria-label') || node.textContent?.trim())),
      syncDot: Boolean(board.querySelector('#teacher-whiteboard-sync-dot')), values: board.querySelectorAll('[id^="twb-chip-"][id$="-value"]').length };
  });
  expect(contract).toEqual({ allDeclared: true, tools: ['hand', 'brush', 'eraser', 'text'], shapes: ['circle', 'square', 'rectangle', 'rounded', 'diamond'],
    actions: ['history', 'save-menu', 'new-board', 'undo', 'redo', 'zoom-out', 'zoom-in', 'reset-view', 'clear'], names: true, syncDot: true, values: 4 });
  for (const width of [1440, 760, 390]) {
    await page.setViewportSize({ width, height: 900 }); const box = (await page.locator('#teacher-whiteboard-toolbar').boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(-1); expect(box.x + box.width).toBeLessThanOrEqual(width + 1);
  }
  expect(errors).toEqual([]);
});

test('LQ whiteboard field adapters retain live settings and explicit popover lifecycle', async ({ page }) => {
  const errors = await mount(page);
  const result = await page.evaluate(() => {
    const { wb, createPopover, popoverManager } = (window as any).fixture, host = document.getElementById('fields')!, values: number[] = [];
    const anchor = wb.h('button', { text: '画笔设置', type: 'button' }); host.append(anchor);
    const range = wb.rangeRow({ label: '粗细', min: 1, max: 32, value: 4, format: (v: number) => `${v}px`, onInput: (v: number) => values.push(v) });
    const input = range.el.querySelector('input')!; input.value = '12'; input.dispatchEvent(new Event('input')); range.set(18);
    const menu = wb.menuItem({ icon: 'save', label: '保存', disabled: true, onClick() {} }); const swatches = wb.swatchRow({ value: '#ff0000', onPick() {} });
    const panel = wb.h('div', { className: 'twb-panel-body' }, [range.el, menu, swatches.el]);
    const popup = createPopover({ anchor, panel, kind: 'popover', label: '画笔设置' }); popup.open();
    const open = { declared: panel.dataset.lqComponent, raised: panel.dataset.lqMaterial, active: popoverManager.isOpen(), disabled: menu.disabled, labelSlot: Boolean(menu.querySelector('.lq-btn__label')) };
    popup.close(); popup.destroy();
    return { values, value: input.value, valueText: input.getAttribute('aria-valuetext'), output: range.el.querySelector('output')!.textContent,
      colors: [...swatches.el.querySelectorAll('button')].every((b: any) => b.dataset.lqComponent === 'choice' && b.dataset.lqVisual === 'color'), open, closed: !popoverManager.isOpen() };
  });
  expect(result).toEqual({ values: [12], value: '18', valueText: '18px', output: '18px', colors: true,
    open: { declared: 'popover', raised: 'raised', active: true, disabled: true, labelSlot: true }, closed: true });
  expect(errors).toEqual([]);
});

test('shared emoji choice preserves image fallback, selection replacement and caret',async({page})=>{
  const errors=await mount(page);
  const result=await page.evaluate(()=>{
    const w=window as any,input=document.createElement('textarea');input.value='ABC';document.body.append(input);input.setSelectionRange(1,2);
    const picker=w.fixture.createEmojiPicker({targetInput:input});document.body.append(picker.element);picker.open();
    const button=picker.element.querySelector('button'),img=button.querySelector('img'),char=img?.alt||button.textContent;
    img?.dispatchEvent(new Event('error'));button.click();
    const state={value:input.value,expected:'A'+char+'C',caret:input.selectionStart,expectedCaret:1+char.length,focus:document.activeElement===input,
      kind:button.dataset.lqComponent,slot:button.querySelector('span')?.textContent||button.textContent,popover:picker.element.dataset.lqComponent};
    picker.close();return state;
  });
  expect(result.value).toBe(result.expected);expect(result.caret).toBe(result.expectedCaret);expect(result.focus).toBe(true);expect(result.kind).toBe('choice');expect(result.popover).toBe('popover');expect(result.slot).toBeTruthy();
  expect(errors).toEqual([]);
});

test('code copy fallback keeps the offscreen native selection and removes its content slot',async({page})=>{
  const errors=await mount(page);
  await page.evaluate(()=>{
    const w=window as any,host=document.getElementById('fields')!;host.innerHTML='<div class="md-content"><pre><code>print(42)</code></pre></div>';
    Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});w.copies=[];
    document.execCommand=()=>{const node=document.activeElement as HTMLTextAreaElement;w.copies.push({value:node.value,kind:node.dataset.lqComponent,readonly:node.readOnly,position:node.style.position,opacity:node.style.opacity,selected:node.selectionEnd-node.selectionStart});return true;};
    w.fixture.decoratePreviewCodeBlocks(host);w.fixture.decoratePreviewCodeBlocks(host);
  });
  await expect(page.locator('.materials-code-copy-btn')).toHaveCount(1);await page.locator('.materials-code-copy-btn').click();
  expect(await page.evaluate(()=>(window as any).copies)).toEqual([{value:'print(42)',kind:'content-slot',readonly:true,position:'fixed',opacity:'0',selected:9}]);
  await expect(page.locator('textarea')).toHaveCount(0);await expect(page.locator('.materials-code-copy-btn')).toBeDisabled();
  await expect(page.locator('.materials-code-copy-btn')).toBeEnabled();await expect(page.locator('.materials-code-copy-btn')).toHaveAttribute('data-lq-component','button');expect(errors).toEqual([]);
});

test('private-message attachment removal retains image/meta slots and releases only its own preview',async({page})=>{
  const errors=await mount(page);
  const result=await page.evaluate(()=>{
    const {ClassroomPrivateMessages}=(window as any).fixture,preview=document.createElement('div');document.body.append(preview);
    const released:string[]=[],host:any={pendingAttachments:[{id:1,isImage:true,previewUrl:'blob:fixture-one',file:{name:'原图.png',size:12}},{id:2,isImage:false,file:{name:'附件.txt',size:20}}],
      previewEl:preview,updateControls(){},onModeChange(){},revokeAttachmentPreview(item:any){released.push(item.previewUrl);},renderPendingAttachments:ClassroomPrivateMessages.prototype.renderPendingAttachments};
    host.renderPendingAttachments();const original=preview.querySelector('button')!,declared=original.dataset.lqComponent,slots=Boolean(preview.querySelector('img')&&preview.querySelector('.classroom-private-preview-meta'));
    original.click();return {declared,slots,released,remaining:host.pendingAttachments.map((x:any)=>x.id),text:preview.textContent,buttons:preview.querySelectorAll('button').length};
  });
  expect(result).toEqual({declared:'button',slots:true,released:['blob:fixture-one'],remaining:[2],text:expect.stringContaining('附件.txt'),buttons:1});expect(errors).toEqual([]);
});

test('signature selections retain surface ownership across dirty, updating and confirmed states',async({page})=>{
  const errors=await mount(page);
  const result=await page.evaluate(()=>{
    const {SignaturePointControl}=(window as any).fixture,root=document.getElementById('fields')!;
    const point=new SignaturePointControl({root,pointKey:'test.review',pointLabel:'签名',materialType:'test',materialId:1,initialSelectedIds:[1]});
    point.state={signatures:[{id:1,name:'原签名',can_use:true},{id:2,name:'新签名',can_use:true}],usable_signatures:[],point:{required_identity_labels:[]}};point.render();
    const snapshots=[];for(const step of ['confirmed','dirty','updating','confirmed']) {
      point.selectedIds=step==='confirmed'?[1]:[2];point.updating=step==='updating';point.updateSelectionStatus();
      const area=root.querySelector('[data-spw-area]')!;snapshots.push({kind:(area as HTMLElement).dataset.lqComponent,surface:area.classList.contains('lq-surface'),state:point.areaState(),same:area===root.querySelector('[data-spw-area]')});
    }
    const dialog=point.ensureDialog(),panel=dialog.querySelector('[data-spw-dialog-panel]')!;
    return {snapshots,modal:dialog.dataset.lqComponent,material:panel.getAttribute('data-lq-material'),blur:getComputedStyle(root.querySelector('[data-spw-area]')!).backdropFilter};
  });
  expect(result).toEqual({snapshots:['confirmed','dirty','updating','confirmed'].map(state=>({kind:'surface',surface:true,state,same:true})),modal:'layer',material:'raised',blur:'none'});expect(errors).toEqual([]);
});
