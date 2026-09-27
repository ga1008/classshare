import { test, expect, type Page, type Locator } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Public domain controllers and shipped CSS, without a database, polling or AI.
// Domain layout stays real; only the page/window placement and API data are fixtures.
const evidence = '.codex-temp/lq-final-surface-boundaries';
const photo = '/static/img/life_tips/hetong-sunny-glass-pavilion05-95b37ec2.webp';
async function mount(page: Page, appearance: 'light' | 'dark') {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('https://surface-boundaries.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw Error(`Unexpected asset ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.webp') ? 'image/webp' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname !== '/') return route.abort();
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="${appearance}" data-ui-palette="indigo" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/tailwind-app.css"><link rel="stylesheet" href="/static/css/ai_workspace.css"><link rel="stylesheet" href="/static/css/agent_workbench.css"><style>
      body#surface-fixture{min-height:100dvh;background:url('${photo}') center/cover fixed}
      #chat-room{position:relative;margin:24px;padding:20px;max-width:520px}#chat-input{width:100%;margin-top:16px}
      #ai-chat-modal{display:block}.ai-workspace-container{left:max(8px,calc(50% - 260px));top:40px;width:min(520px,calc(100vw - 16px));height:720px;max-height:calc(100dvh - 56px)!important}
    </style></head><body id="surface-fixture"><section id="chat-room" class="lq-surface" data-lq-component="surface" hidden><h2>课堂讨论附件</h2><div id="chat-messages"></div><form id="chat-form"><div id="attachment-previews" class="chat-attachment-preview-row" hidden></div><textarea id="chat-input" class="lq-textarea" data-lq-component="textarea" aria-label="讨论草稿">讨论草稿保留</textarea></form></section>
    <div id="ai-chat-modal" class="ai-workspace-modal" aria-hidden="false"><div data-lq-component="workspace" class="ai-chat-container ai-workspace-container lq-domain-workspace" role="dialog" aria-modal="false" aria-label="AI 助手"><header class="ai-chat-header ai-workspace-header"><div class="ai-workspace-title"><div><h3>AI 助手 · 任务</h3></div></div></header><section id="workbench" class="awb"></section></div></div>
    <script type="module">
      import {createAgentWorkbench} from '/static/js/agent_workbench.js';
      import {ClassroomChat} from '/static/js/chat.js';
      import {ExamDrawingWhiteboard} from '/static/js/whiteboard/exam_board.js';
      const h=window.h={requests:[],notifications:[]};
      h.apiJson=async(url,options={})=>{
        h.requests.push({url,method:options.method||'GET'});
        if(url==='/api/agent-tasks/bootstrap')return {runtime_configured:true,is_super_admin:true,tasks:[{id:9,title:'课堂讨论报告',status:'completed',runtime_status:'completed',is_owner:true,is_terminal:true,updated_at:'2026-09-27T10:00:00'}],workflow_catalog:[],queue_state:{}};
        if(url==='/api/agent-tasks/subscriptions')return {subscriptions:[]};
        if(url==='/api/agent-tasks/admin/queue')return {tasks:[],queue_state:{running_count:1,global_concurrency:2,queued_count:2}};
        throw Error('Unexpected fixture API '+url);
      };
      h.workbench=createAgentWorkbench({root:document.getElementById('workbench'),config:{userKey:'teacher:7',userRole:'teacher',classOfferingId:31},notify:(message,type)=>h.notifications.push({message,type}),apiJson:h.apiJson,capture:async()=>null});
      await h.workbench.prefill('请保留这份待发送的教学安排');
      h.chat=new ClassroomChat({classOfferingId:31,chatMessagesContainerId:'chat-messages',chatInputId:'chat-input',chatFormId:'chat-form',attachmentPreviewRowId:'attachment-previews',discussionRoomId:'chat-room'});
      h.chat.pendingAttachments=['课堂板书.png','课后练习.png'].map((name,index)=>({attachment_id:index+1,name,mime_type:'image/png',preview_url:'${photo}',thumbnail_url:'${photo}',width:640,height:360,file_size:1024}));
      h.chat.renderPendingAttachments();
      h.board=new ExamDrawingWhiteboard().init();window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://surface-boundaries.test/'); await page.waitForFunction(() => (window as any).ready);
  return errors;
}

async function material(locator: Locator) {
  return locator.evaluate(element => {
    const css = getComputedStyle(element), box = element.getBoundingClientRect();
    const leaves = [...element.querySelectorAll('button,input,textarea,select')].map(node => ({ component: (node as HTMLElement).dataset.lqComponent, blur: getComputedStyle(node).backdropFilter }));
    const reference = document.createElement('i'); reference.style.color = 'hsl(var(--lq-material-ink))'; element.append(reference);
    const sharedInk = getComputedStyle(reference).color; reference.remove();
    return { blur: css.backdropFilter, background: css.backgroundColor, image: css.backgroundImage, color: css.color, sharedInk,
      x: box.x, y: box.y, width: box.width, height: box.height, leaves };
  });
}
async function glassHosts(root: Locator) {
  return root.evaluate(element => [element, ...element.querySelectorAll('*')].flatMap(node => {
    if (!(node instanceof HTMLElement) || !node.getClientRects().length || node.closest('[hidden]')) return [];
    return ['', '::before', '::after'].flatMap(pseudo => {
      const css = getComputedStyle(node, pseudo || null);
      if (css.display === 'none' || css.visibility === 'hidden' || (pseudo && ['none', 'normal'].includes(css.content))) return [];
      return css.backdropFilter && css.backdropFilter !== 'none' ? [`${node.id || node.className}${pseudo}`] : [];
    });
  }));
}
async function screenshot(page: Page, name: string) {
  fs.mkdirSync(evidence, { recursive: true }); await page.screenshot({ path: `${evidence}/${name}.png` });
}

for (const appearance of ['light', 'dark'] as const) for (const width of [1440, 390]) {
  test(`Agent drawers and chat attachment surfaces: ${appearance} ${width}`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: width === 390, isMobile: width === 390 });
    try {
      const page = await context.newPage(), errors = await mount(page, appearance), records: Record<string, unknown> = {};
      const workspace = page.locator('#ai-chat-modal');
      expect(await glassHosts(workspace)).toHaveLength(1);
      for (const drawerName of ['history', 'admin']) {
        const trigger = page.locator(`[data-awb-drawer-toggle="${drawerName}"]`), drawer = page.locator(`[data-awb-drawer="${drawerName}"]`);
        await expect(drawer).toBeHidden(); await trigger.click(); await expect(drawer).toBeVisible(); await expect(trigger).toHaveAttribute('aria-expanded', 'true');
        if (drawerName === 'admin') await expect(drawer).toContainText('执行中 1/2');
        else await expect(drawer).toContainText('课堂讨论报告');
        const paint = await material(drawer); records[drawerName] = paint;
        expect(paint.blur).toContain('blur(24px)'); expect(paint.color).toBe(paint.sharedInk);
        expect(paint.x).toBeGreaterThanOrEqual(0); expect(paint.x + paint.width).toBeLessThanOrEqual(width);
        expect(paint.y + paint.height).toBeLessThanOrEqual(901);
        expect(paint.leaves.every(leaf => leaf.component && leaf.blur === 'none')).toBe(true);
        expect(await glassHosts(workspace)).toHaveLength(2);
        await screenshot(page, `agent-${drawerName}-${appearance}-${width}`);
        await page.locator('html').evaluate(el => el.setAttribute('data-lq-glass', 'off'));
        expect(await glassHosts(workspace)).toEqual([]); const opaque = await material(drawer);
        expect(opaque.background).toMatch(/^rgb\(/); expect(opaque.color).toBe(opaque.sharedInk); records[`${drawerName}-off`] = opaque;
        await screenshot(page, `agent-${drawerName}-off-${appearance}-${width}`);
        await drawer.getByRole('button', { name: '收起', exact: true }).click(); await expect(drawer).toBeHidden(); await expect(trigger).toHaveAttribute('aria-expanded', 'false');
        await expect(page.locator('[data-awb-input]')).toHaveValue('请保留这份待发送的教学安排');
        await page.locator('html').evaluate(el => el.setAttribute('data-lq-glass', 'tinted'));
      }
      expect(await page.evaluate(() => (window as any).h.requests.every((entry: any) => entry.method === 'GET'))).toBe(true);
      await workspace.evaluate(el => (el as HTMLElement).style.display = 'none');
      await page.locator('#chat-room').evaluate(el => (el as HTMLElement).hidden = false);
      const cards = page.locator('.chat-attachment-preview-card'); await expect(cards).toHaveCount(2);
      const cardPaint = await material(cards.first()); records.card = cardPaint;
      expect(cardPaint.blur).toBe('none'); expect(cardPaint.image).toBe('none');
      expect(cardPaint.x + cardPaint.width).toBeLessThanOrEqual(width); expect(cardPaint.leaves.every(leaf => leaf.component && leaf.blur === 'none')).toBe(true);
      await expect(cards.first()).toHaveAttribute('data-lq-component', 'surface');
      expect(await glassHosts(page.locator('#chat-room'))).toEqual([]);
      await screenshot(page, `chat-attachments-${appearance}-${width}`);
      await cards.first().getByTitle('预览图片').click(); await expect.poll(() => page.evaluate(() => (window as any).h.chat.imagePreviewController.isOpen())).toBe(true);
      await page.keyboard.press('Escape'); await expect.poll(() => page.evaluate(() => (window as any).h.chat.imagePreviewController.isOpen())).toBe(false);
      await page.getByRole('button', { name: '移除 课堂板书.png', exact: true }).click(); await expect(cards).toHaveCount(1); await expect(cards).toContainText('课后练习.png');
      await expect(page.locator('#chat-input')).toHaveValue('讨论草稿保留');
      await page.locator('html').evaluate(el => el.setAttribute('data-lq-glass', 'off')); expect(await glassHosts(page.locator('#chat-room'))).toEqual([]);
      records['card-off'] = await material(cards.first()); await screenshot(page, `chat-attachments-off-${appearance}-${width}`);
      expect(errors).toEqual([]); expect(await page.evaluate(() => (window as any).h.notifications)).toEqual([]);
      fs.writeFileSync(`${evidence}/agent-chat-${appearance}-${width}.json`, JSON.stringify(records, null, 2));
    } finally { await context.close(); }
  });

  test(`Exam whiteboard toolbar material preserves drawing and actions: ${appearance} ${width}`, async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width, height: 900 }, hasTouch: width === 390, isMobile: width === 390 });
    try {
      const page = await context.newPage(), errors = await mount(page, appearance);
      await page.locator('#ai-chat-modal').evaluate(el => (el as HTMLElement).style.display = 'none');
      await page.evaluate(() => { const h = (window as any).h; h.result = h.board.open({ paperTitle: '课堂练习', questionId: 12, fileName: '课堂附图.png' }); });
      const root = page.locator('#exam-drawing-whiteboard-root'), toolbar = root.getByRole('toolbar'); await expect(toolbar).toBeVisible();
      await expect.poll(() => page.evaluate(() => (window as any).h.board.canvasWidth)).toBeGreaterThan(0);
      const paint = await material(toolbar);
      expect(paint.blur).toContain('blur(24px)'); expect(paint.color).toBe(paint.sharedInk);
      expect(paint.x).toBeGreaterThanOrEqual(0); expect(paint.x + paint.width).toBeLessThanOrEqual(width);
      expect(paint.leaves.every(leaf => leaf.component && leaf.blur === 'none')).toBe(true);
      expect(await glassHosts(root)).toHaveLength(1);
      await toolbar.getByRole('button', { name: '橡皮擦', exact: true }).click(); expect(await page.evaluate(() => (window as any).h.board.settings.tool)).toBe('eraser');
      await toolbar.getByRole('button', { name: '画笔', exact: true }).click();
      await toolbar.getByRole('slider', { name: '笔触粗细' }).fill('12'); expect(await page.evaluate(() => (window as any).h.board.settings.brushSize)).toBe(12);
      await page.mouse.move(width / 2 - 60, 500); await page.mouse.down(); await page.mouse.move(width / 2 + 60, 540, { steps: 8 }); await page.mouse.up();
      expect(await page.evaluate(() => (window as any).h.board.strokes.length)).toBe(1);
      await toolbar.getByRole('button', { name: '撤销', exact: true }).click(); expect(await page.evaluate(() => (window as any).h.board.strokes.length)).toBe(0);
      await toolbar.getByRole('button', { name: '重做', exact: true }).click(); expect(await page.evaluate(() => (window as any).h.board.strokes.length)).toBe(1);
      await screenshot(page, `whiteboard-${appearance}-${width}`);
      await page.locator('html').evaluate(el => el.setAttribute('data-lq-glass', 'off'));
      expect(await glassHosts(root)).toEqual([]); const off = await material(toolbar); expect(off.background).toMatch(/^rgb\(/); expect(off.color).toBe(off.sharedInk);
      await screenshot(page, `whiteboard-off-${appearance}-${width}`);
      await toolbar.getByRole('button', { name: '保存附图', exact: true }).click(); await expect(root).toBeHidden();
      const result = await page.evaluate(async () => { const result = await (window as any).h.result; return { png: result.dataUrl.startsWith('data:image/png;base64,'), width: result.width, height: result.height, name: result.fileName }; });
      expect(result.png).toBe(true); expect(result.width).toBeGreaterThan(0); expect(result.height).toBeGreaterThan(0); expect(result.name).toBe('课堂附图.png');
      await page.evaluate(() => { const h = (window as any).h; h.result = h.board.open(); });
      await toolbar.getByRole('button', { name: '关闭', exact: true }).click(); await expect(root).toBeHidden(); expect(await page.evaluate(() => (window as any).h.result)).toBeNull();
      expect(errors).toEqual([]); fs.writeFileSync(`${evidence}/whiteboard-${appearance}-${width}.json`, JSON.stringify({ paint, off, result }, null, 2));
    } finally { await context.close(); }
  });
}
