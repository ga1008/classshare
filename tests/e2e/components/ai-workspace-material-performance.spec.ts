import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Real CSS and history controller, with synthetic conversations and no server/AI.
async function mount(page: Page, appearance: 'light' | 'dark') {
  const errors: string[] = [];
  let sessionCount = 3;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://material.test/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve('.' + url.pathname);
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) throw new Error(`Unexpected static file ${file}`);
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.webp') ? 'image/webp' : 'text/javascript', body: fs.readFileSync(file) });
    }
    if (url.pathname === '/api/ai/workspace/sessions') {
      return route.fulfill({ json: { sessions: Array.from({ length: sessionCount }, (_, index) => ({ session_uuid: `fixture-${index}`, title: `课程讨论 ${index + 1}`, updated_at: '2026-09-27T09:00:00' })) } });
    }
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-appearance="${appearance}" data-lq-glass="tinted" data-lq-tier="A"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/css/ui-system.src.css"><link rel="stylesheet" href="/static/css/ai_workspace.css"><style>
      body#material-fixture{min-height:1200px;background:url('/static/img/life_tips/hetong-sunny-glass-pavilion05-95b37ec2.webp') center/cover fixed}
      #page-area{padding:24px;max-width:620px}.ai-workspace-container{left:max(8px,calc(50% - 260px));top:80px;width:min(520px,calc(100vw - 16px));height:640px;max-height:calc(100dvh - 96px)!important}
      #ai-chat-modal{display:block}.ai-workspace-panel{display:flex;flex:1;flex-direction:column}.ai-workspace-title{display:flex;align-items:center}.ai-workspace-title__mark{padding:8px}#page-action{padding:10px}
    </style></head><body id="material-fixture"><main id="page-area"><button id="page-action" class="lq-btn lq-btn--soft">页面操作</button><h1>课程工作台</h1><p>背景内容保持可见，助手只占用自己的窗口。</p></main>
    <div id="ai-chat-modal" class="ai-workspace-modal" aria-hidden="false"><div class="ai-chat-container ai-workspace-container" role="dialog" aria-modal="false" aria-label="AI 助手">
      <header class="ai-chat-header ai-workspace-header"><div class="ai-workspace-title"><span class="ai-workspace-title__mark">AI</span><h3>AI 助手</h3></div><div class="header-buttons"><button id="history-toggle" class="chat-btn" aria-label="我的对话">☰</button><button class="chat-btn" aria-label="新对话">＋</button><button class="chat-btn" aria-label="关闭">×</button></div></header>
      <section class="ai-workspace-panel is-active"><div class="ai-chat-messages"><div class="ai-chat-message user"><div class="bubble">帮我梳理这节课的重点。</div></div><div class="ai-chat-message"><div class="bubble">我们可以从概念、例子和练习三个方面整理。<br>你想先讨论哪一部分？</div></div></div>
      <div class="ai-chat-input-area"><div class="ai-workspace-modebar"><button class="ai-workspace-mode-option active">对话</button><button class="ai-workspace-mode-option">任务</button></div><div class="input-row"><div class="left-buttons"><button class="chat-btn" aria-label="添加附件">＋</button></div><textarea aria-label="对话草稿" placeholder="输入问题"></textarea><div class="right-buttons"><button class="chat-btn send" aria-label="发送">↑</button></div></div></div></section>
    </div></div><script type="module">
      import {createConversationHistory} from '/static/js/ai_workspace_history.js';
      const container=document.querySelector('.ai-workspace-container');
      window.stats={pageClicks:0,loaded:null,errors:[]};
      window.historyPicker=createConversationHistory(container,{currentSessionUUID:'fixture-0',isLoading:false,loadSession:async uuid=>{stats.loaded=uuid;}},message=>stats.errors.push(message));
      document.getElementById('history-toggle').onclick=()=>historyPicker.toggle();
      document.getElementById('page-action').onclick=()=>stats.pageClicks++;
      window.ready=true;
    </script></body></html>` });
  });
  await page.goto('https://material.test/fixture');
  await page.waitForFunction(() => (window as any).ready);
  await page.locator('textarea').fill('保留的对话草稿');
  return { errors, setSessionCount: (count: number) => { sessionCount = count; } };
}

async function glassHosts(page: Page) {
  return page.locator('#ai-chat-modal').evaluate(root => {
    const hosts: string[] = [];
    for (const element of [root, ...root.querySelectorAll('*')]) {
      if (!(element instanceof HTMLElement) || !element.getClientRects().length || element.closest('[hidden]')) continue;
      for (const pseudo of ['', '::before', '::after']) {
        const style = getComputedStyle(element, pseudo || null);
        if (style.display === 'none' || style.visibility === 'hidden' || (pseudo && ['none', 'normal'].includes(style.content))) continue;
        const filter = style.backdropFilter || style.getPropertyValue('-webkit-backdrop-filter');
        if (filter && filter !== 'none') hosts.push(`${element.id || element.className}${pseudo}`);
      }
    }
    return hosts;
  });
}

for (const appearance of ['light', 'dark'] as const) {
  for (const touch of [false, true]) {
    test(`AI glass hosts stay bounded with growing history: ${appearance}, ${touch ? 'touch' : 'desktop'}`, async ({ browser }, testInfo) => {
      const context = await browser.newContext({ viewport: touch ? { width: 390, height: 844 } : { width: 1280, height: 900 }, hasTouch: touch, isMobile: touch });
      try {
        const page = await context.newPage();
        const fixture = await mount(page, appearance);
        const before = await glassHosts(page);
        await page.screenshot({ path: testInfo.outputPath('workspace.png') });
        expect(before).toEqual(['ai-chat-container ai-workspace-container::before']);
        const controls = await page.locator('#ai-chat-modal button, .ai-workspace-title__mark').evaluateAll(elements => elements.map(element => {
          const style = getComputedStyle(element);
          return { filter: style.backdropFilter, sheen: style.backgroundImage, shadow: style.boxShadow, border: style.borderTopWidth };
        }));
        for (const control of controls) {
          expect(control.filter).toBe('none');
          expect(control.sheen).toContain('gradient');
          expect(control.shadow).not.toBe('none');
          expect(control.border).toBe('1px');
        }
        expect(await page.locator('.ai-workspace-container').evaluate(element => getComputedStyle(element, '::before').backdropFilter)).toContain('blur(34px)');

        await page.getByRole('button', { name: '我的对话', exact: true }).click();
        await expect(page.locator('.ai-conversation-entry')).toHaveCount(3);
        const smallHistory = await glassHosts(page);
        expect(smallHistory).toHaveLength(2);
        expect(smallHistory).toContain('ai-agent-history-drawer ai-conversation-history');
        expect(await page.locator('.ai-conversation-history').evaluate(element => getComputedStyle(element).backdropFilter)).toContain('blur(32px)');
        await page.getByRole('button', { name: '收起对话历史' }).click();
        fixture.setSessionCount(180);
        await page.getByRole('button', { name: '我的对话', exact: true }).click();
        await expect(page.locator('.ai-conversation-entry')).toHaveCount(180);
        expect(await glassHosts(page)).toEqual(smallHistory);
        await page.screenshot({ path: testInfo.outputPath('history.png') });
        await page.locator('.ai-conversation-entry').first().click();
        await expect(page.locator('.ai-conversation-history')).toBeHidden();
        expect(await page.evaluate(() => (window as any).stats.loaded)).toBe('fixture-0');
        await expect(page.locator('textarea')).toHaveValue('保留的对话草稿');
        await page.getByRole('button', { name: '页面操作' }).click();
        expect(await page.evaluate(() => (window as any).stats.pageClicks)).toBe(1);

        await page.locator('html').evaluate(element => element.setAttribute('data-lq-glass', 'off'));
        await page.getByRole('button', { name: '我的对话', exact: true }).click();
        await expect(page.locator('.ai-conversation-entry')).toHaveCount(180);
        expect(await glassHosts(page)).toEqual([]);
        await page.screenshot({ path: testInfo.outputPath('transparency-off.png') });
        expect(fixture.errors).toEqual([]);
        expect(await page.evaluate(() => (window as any).stats.errors)).toEqual([]);
      } finally { await context.close(); }
    });
  }
}
