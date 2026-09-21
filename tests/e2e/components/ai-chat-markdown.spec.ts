import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import { execFileSync } from 'node:child_process';

// Only transport and clipboard are fixtures. Parsing, sanitizing, rendering,
// streaming state, history restoration and responsive styles are production code.
const styles = fs.readFileSync('static/css/tailwind-app.css', 'utf8');
const shell = execFileSync('python', ['-X', 'utf8', '-c', `
from jinja2 import Environment, FileSystemLoader
print(Environment(loader=FileSystemLoader('templates')).get_template('partials/ai_workspace_widget.html').render(user_info={'role':'teacher','name':'示例教师'}))
`], { encoding: 'utf8' });
const icmp = '张老师，ICMP 是网络层的一个关键协议，对应教材**第4章 4.4 节**。\n\n---\n\n## 一、全称与定位\n\n| 项目 | 内容 |\n|---|---|\n| 英文全称 | Internet Control Message Protocol |\n| 中文 | 网际控制报文协议 |\n| 层次 | **网络层** |\n| 载体 | 报文封装在 **IP 数据报**中传输 |\n\n---\n\n## 二、核心作用\n\n- 差错报告\n- 网络诊断\n\n> ICMP 不能保证数据可靠送达。';

async function mount(page: Page, options: { contextOnly?: boolean; parser?: boolean; module?: boolean } = {}) {
  const errors: string[] = [];
  const history: any[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/ai/chat/history/')) return route.fulfill({ json: { messages: history } });
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${styles}</style><body>${shell}</body></html>` });
    return route.abort();
  });
  await page.goto('http://ai-markdown.test/');
  await page.addScriptTag({ path: 'static/vendor/runtime/es2022-polyfills.js' });
  if (options.parser !== false) await page.addScriptTag({ path: 'static/js/marked.min.js' });
  await page.addScriptTag({ path: 'static/js/markdown_runtime.js' });
  await page.addScriptTag({ path: 'static/js/ai_chat_component.js', ...(options.module ? { type: 'module' } : {}) });
  await page.evaluate(({ contextOnly }) => {
    const w = window as any;
    w.fixtureCopies = [];
    w.fixtureNotices = [];
    w.showMessage = (message: string, type: string) => w.fixtureNotices.push({ message, type });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { w.fixtureCopies.push(text); } } });
    w.chatFixture = new w.AIChatComponent({ classOfferingId: contextOnly ? null : 42, contextOnly });
    w.chatFixture.init();
    w.chatFixture.currentSessionUUID = 'fixture-session';
    w.chatFixture.openChat();
    w.chatFixture.messagesBox.replaceChildren();
  }, { contextOnly: options.contextOnly ?? true });
  return { errors, history };
}

async function render(page: Page, content: string, thinking = '') {
  await page.evaluate(({ content, thinking }) => (window as any).chatFixture.renderMessage('assistant', content, [], thinking), { content, thinking });
  return page.locator('.ai-chat-message.assistant').last();
}

async function startStream(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    const originalFetch = window.fetch.bind(window);
    w.fixtureRequests = [];
    window.fetch = async (input: any, init: any) => {
      if (!['/api/ai/workspace-chat', '/api/ai/chat'].includes(String(input))) return originalFetch(input, init);
      w.fixtureRequests.push({ url: String(input), message: init.body.get('message') });
      return new Response(new ReadableStream({ start(controller) { w.fixtureStream = controller; } }), { status: 200 });
    };
    w.chatFixture.textarea.value = 'ICMP是什么';
    w.fixtureSending = w.chatFixture.handleSendMessage();
  });
  await expect.poll(() => page.evaluate(() => Boolean((window as any).fixtureStream))).toBe(true);
}

async function enqueue(page: Page, events: any[], close = false) {
  await page.evaluate(({ events, close }) => {
    const w = window as any;
    // Split in the middle of JSON tokens and multibyte Chinese UTF-8 characters.
    const bytes = new TextEncoder().encode(events.map(event => JSON.stringify(event)).join('\n') + (close ? '' : '\n'));
    for (let offset = 0; offset < bytes.length; offset += 7) w.fixtureStream.enqueue(bytes.slice(offset, offset + 7));
    if (close) w.fixtureStream.close();
  }, { events, close });
}

test('the material reader renders parser dependencies before chat initialization', async () => {
  const html = execFileSync('python', ['-X', 'utf8', '-c', `
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('templates'))
env.globals.update(asset_url=lambda name: '/static/'+name, static_asset_revision=lambda: 'fixture', vite_entry_tags=lambda entry: '')
shell = dict(entry_name='lesson_5.html', material_name='课程', entry_material_id=1, material_path='lesson_5.html', is_html_package=True, node_id=1, package_root_id=1, iframe_src='/fixture-lesson', lesson_number=5)
print(env.get_template('material_render_shell.html').render(shell=shell, user_info={'id':1,'role':'teacher','name':'示例教师'}, learning_context={'class_offering_id':42,'session_id':5}, reader_return=None))
`], { encoding: 'utf8' });
  const dependencies = ['es2022_polyfills', 'marked', 'markdown_runtime', 'js/ai_chat_component.js', 'js/ai_workspace_widget.js'];
  const positions = dependencies.map(name => html.indexOf(`src="/static/${name}"`));
  expect(positions.every(position => position >= 0), 'The actual material reader must include every Markdown dependency').toBe(true);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
});

test('screenshot ICMP answer renders headings, emphasis, rules, GFM table, lists and quotes', async ({ page }) => {
  const h = await mount(page);
  const message = await render(page, icmp);
  await expect(message.locator('h2')).toHaveText(['一、全称与定位', '二、核心作用']);
  await expect(message.locator('strong')).toHaveText(['第4章 4.4 节', '网络层', 'IP 数据报']);
  await expect(message.locator('hr')).toHaveCount(2);
  await expect(message.locator('table tbody tr')).toHaveCount(4);
  await expect(message.locator('th')).toHaveText(['项目', '内容']);
  await expect(message.locator('li')).toHaveText(['差错报告', '网络诊断']);
  await expect(message.locator('blockquote')).toContainText('ICMP 不能保证');
  expect(h.errors).toEqual([]);
});

test('fragmented response, completion and restored history produce identical Markdown and thinking', async ({ page }) => {
  const h = await mount(page, { contextOnly: false });
  const thinking = '分析 ICMP 的职责，保留 # 原始思考文本。';
  await startStream(page);
  await enqueue(page, [{ event: 'meta', thinking_supported: true }, { event: 'thinking_delta', delta: thinking }]);
  await expect(page.locator('.thinking-status')).toContainText('正在思考中');
  await enqueue(page, [{ event: 'thinking_end' }, { event: 'answer_delta', delta: icmp.slice(0, 20) }]);
  await expect(page.locator('.final-answer')).toContainText(icmp.slice(0, 12));
  await expect(page.locator('.streaming-cursor')).toHaveCount(1);
  const events = [];
  for (let offset = 20; offset < icmp.length; offset += 11) events.push({ event: 'answer_delta', delta: icmp.slice(offset, offset + 11) });
  events.push({ event: 'done' });
  await enqueue(page, events, true);
  await page.evaluate(() => (window as any).fixtureSending);
  await expect(page.locator('.streaming-cursor')).toHaveCount(0);
  const completed = await page.locator('.final-answer').innerHTML();
  await page.locator('.thinking-header').click();
  await expect(page.locator('.thinking-text')).toHaveText(thinking);
  h.history.push({ role: 'assistant', message: icmp, final_answer: icmp, thinking_content: thinking });
  await page.evaluate(() => (window as any).chatFixture.loadSession('fixture-session'));
  expect(await page.locator('.final-answer').innerHTML()).toBe(completed);
  await expect(page.locator('.copy-btn')).toHaveAttribute('data-raw-markdown', icmp);
  await expect(page.locator('h2')).toHaveCount(2);
  expect(await page.evaluate(() => (window as any).fixtureRequests)).toEqual([{ url: '/api/ai/chat', message: 'ICMP是什么' }]);
  expect(h.errors).toEqual([]);
});

test('an interrupted stream retains its formatted partial answer and permits another message', async ({ page }) => {
  const h = await mount(page);
  await startStream(page);
  const partial = '## 已收到的内容\n\n**诊断**信息\n\n```python\nprint("ICMP")\n';
  await enqueue(page, [{ event: 'answer_delta', delta: partial }]);
  await expect(page.locator('.final-answer h2')).toHaveText('已收到的内容');
  await page.evaluate(() => (window as any).fixtureStream.error(new Error('fixture connection interrupted')));
  await page.evaluate(() => (window as any).fixtureSending);
  await expect(page.locator('.final-answer strong')).toHaveText('诊断');
  await expect(page.locator('pre code')).toHaveText('print("ICMP")\n');
  await expect(page.locator('.copy-btn')).toHaveAttribute('data-raw-markdown', partial);
  await expect(page.locator('.streaming-cursor')).toHaveCount(0);
  await page.locator('#ai-chat-textarea').fill('继续');
  await expect(page.locator('#ai-chat-btn-send')).toBeEnabled();
  expect(h.errors).toEqual([]);
});

test('copy Markdown preserves the original answer and code copy preserves exact code', async ({ page }) => {
  const h = await mount(page);
  const code = 'const fragment = "##literal";\n\n\n\nconsole.log(fragment);\n';
  const source = '  原文开头保留空格\n\n```js\n' + code + '```\n\n结尾空格  ';
  const message = await render(page, source);
  await message.locator('.copy-btn').click();
  await message.locator('pre').hover();
  await message.locator('.copy-code-btn').click();
  expect(await page.evaluate(() => (window as any).fixtureCopies)).toEqual([source, code]);
  expect(h.errors).toEqual([]);
});

for (const sample of [
  { name: 'inline hashes and emphasis', source: '使用 `C#`、`##标题`、`** 原文 **` 和 ``a`##b``。', codes: ['C#', '##标题', '** 原文 **', 'a`##b'] },
  { name: 'tilde fence and blank lines', source: '~~~python\n#literal\nvalue = "##literal"\n\n\n\nprint(value)\n~~~', codes: ['#literal\nvalue = "##literal"\n\n\n\nprint(value)\n'] },
  { name: 'four-backtick fence containing triple backticks', source: '````markdown\n```python\n##literal\n\n\n\n```\n````', codes: ['```python\n##literal\n\n\n\n```\n'] },
  { name: 'indented code at the start of an answer', source: '    #literal\n    value = "##literal"\n\n    print(value)\n', codes: ['#literal\nvalue = "##literal"\n\nprint(value)\n'] },
]) {
  test(`standard Markdown preserves ${sample.name}`, async ({ page }) => {
    const h = await mount(page);
    const message = await render(page, sample.source);
    expect(await message.locator('code').allTextContents()).toEqual(sample.codes);
    await expect(message.locator('h1,h2')).toHaveCount(0);
    expect(h.errors).toEqual([]);
  });
}

test('URL hash fragments and ordinary hash text are preserved', async ({ page }) => {
  await mount(page);
  const message = await render(page, '查看 [章节](https://example.test/docs##section)，语言 C#。\n\n#没有空格的文字');
  await expect(message.locator('a')).toHaveAttribute('href', 'https://example.test/docs##section');
  await expect(message.locator('h1,h2')).toHaveCount(0);
  await expect(message).toContainText('#没有空格的文字');
});

test('GFM short separators and explicit column alignment survive parsing and CSS', async ({ page }) => {
  await mount(page);
  const message = await render(page, '| 左 | 中 | 右 |\n| :- | :-: | -: |\n| 文字 | 居中 | 42 |');
  await expect(message.locator('th')).toHaveCount(3);
  for (const [index, align] of ['left', 'center', 'right'].entries()) {
    await expect(message.locator('th').nth(index)).toHaveCSS('text-align', align);
    await expect(message.locator('td').nth(index)).toHaveCSS('text-align', align);
  }
});

test('malicious HTML is sanitized while harmless Markdown remains visible', async ({ page }) => {
  const h = await mount(page);
  const message = await render(page, '## 安全标题\n\n<script>window.fixtureXss=1</script>\n\n<img src="x" onerror="window.fixtureXss=2"><a href="javascript:window.fixtureXss=3" onclick="window.fixtureXss=4">危险链接</a><iframe srcdoc="<script>parent.fixtureXss=5</script>"></iframe>\n\n**保留正文**');
  await expect(message.locator('h2')).toHaveText('安全标题');
  await expect(message.locator('strong')).toHaveText('保留正文');
  await expect(message.locator('script,iframe,[onerror],[onclick],a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).fixtureXss)).toBeUndefined();
  expect(h.errors).toEqual([]);
});

test('unknown HTML wrappers cannot reinsert dropped descendants or lose sanitized children', async ({ page }) => {
  const h = await mount(page);
  const message = await render(page, '<x-wrapper><script>window.fixtureXss=1</script><iframe srcdoc="bad"></iframe><x-nested><strong>保留合法内容</strong><img src=x onerror="window.fixtureXss=2"></x-nested></x-wrapper>');
  await expect(message.locator('script,iframe,x-wrapper,x-nested,[onerror]')).toHaveCount(0);
  await expect(message.locator('strong')).toHaveText('保留合法内容');
  expect(await page.evaluate(() => (window as any).fixtureXss)).toBeUndefined();
  expect(h.errors).toEqual([]);
});

test('missing parser degrades to safe readable lines and recovers when assets become available', async ({ page }) => {
  const h = await mount(page, { parser: false });
  const source = '## 尚未加载解析器\n\n<img src=x onerror="window.fixtureXss=1">';
  const first = await render(page, source);
  await expect(first.locator('img')).toHaveCount(0);
  await expect(first).toContainText('## 尚未加载解析器');
  await page.addScriptTag({ path: 'static/js/marked.min.js' });
  const second = await render(page, icmp);
  await expect(second.locator('table')).toHaveCount(1);
  await expect(second.locator('h2')).toHaveCount(2);
  expect(h.errors).toEqual([]);
});

test('parser exceptions preserve safe readable text and later successful parsing', async ({ page }) => {
  const h = await mount(page);
  await page.evaluate(() => {
    const w = window as any;
    w.fixtureOriginalMarked = w.marked;
    w.marked = { parse: () => { throw new Error('fixture parse failure'); } };
  });
  const failed = await render(page, '## 安全降级\n<img src=x onerror="window.fixtureXss=1">');
  await expect(failed).toContainText('## 安全降级');
  await expect(failed.locator('img')).toHaveCount(0);
  await page.evaluate(() => { const w = window as any; w.marked = w.fixtureOriginalMarked; });
  const recovered = await render(page, icmp);
  await expect(recovered.locator('table')).toHaveCount(1);
  expect(await page.evaluate(() => (window as any).fixtureXss)).toBeUndefined();
  expect(h.errors).toEqual([]);
});

test('module entry exports the shared renderer and Agent results preserve code and alignment', async ({ page }) => {
  const h = await mount(page, { module: true });
  expect(await page.evaluate(() => typeof (window as any).renderAIChatMarkdown)).toBe('function');
  const widgetSource = fs.readFileSync('static/js/ai_workspace_widget.js', 'utf8');
  const widget = widgetSource.slice(0, widgetSource.lastIndexOf("if (document.readyState === 'loading')"));
  await page.addScriptTag({ content: `(() => { ${widget}\nwindow.fixtureAgentRenderers = { renderBusinessResult, renderDeliverable, renderRuntimeDetail }; })();` });
  const source = '    #literal\n    print("##literal")\n\n| 左 | 中 | 右 |\n| :- | :-: | -: |\n| 文字 | 居中 | 42 |';
  await page.evaluate(source => {
    const w = window as any;
    const result = document.createElement('div');
    result.id = 'fixture-agent-results';
    result.innerHTML = w.fixtureAgentRenderers.renderBusinessResult({ markdown: source })
      + w.fixtureAgentRenderers.renderDeliverable({ deliverable_markdown: source })
      + w.fixtureAgentRenderers.renderRuntimeDetail({ text_outputs: [{ text: source }] });
    w.chatFixture.messagesBox.appendChild(result);
  }, source);
  for (const selector of ['.ai-task-business-markdown', '.ai-task-deliverable', '.ai-task-runtime-output']) {
    const result = page.locator(selector);
    expect(await result.locator('pre code').textContent()).toBe('#literal\nprint("##literal")\n');
    await expect(result.locator('h1,h2')).toHaveCount(0);
    for (const [index, align] of ['left', 'center', 'right'].entries()) {
      await expect(result.locator('th').nth(index)).toHaveCSS('text-align', align);
      await expect(result.locator('td').nth(index)).toHaveCSS('text-align', align);
    }
  }
  expect(h.errors).toEqual([]);
});

for (const width of [1440, 390]) {
  test(`long Markdown remains contained at ${width}px in window and fullscreen`, async ({ page }) => {
    await page.setViewportSize({ width, height: 980 });
    const h = await mount(page);
    const content = icmp + '\n\n| 长列 | 说明 |\n|---|---|\n| ' + 'W'.repeat(150) + ' | 保留横向滚动 |\n\n```text\n' + 'code_'.repeat(100) + '\n```\n\n' + 'unbroken'.repeat(100);
    const message = await render(page, content);
    for (const fullscreen of [false, true]) {
      if (fullscreen) await page.locator('#ai-chat-btn-fullscreen').click();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
      const bounds = await page.locator('#ai-chat-messages-box').boundingBox();
      const bubble = await message.locator('.bubble').boundingBox();
      expect(bounds).not.toBeNull();
      expect(bubble!.x).toBeGreaterThanOrEqual(bounds!.x - 1);
      expect(bubble!.x + bubble!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width + 1);
      for (const element of await message.locator('table,pre').all()) {
        expect(await element.evaluate(node => node.getBoundingClientRect().width <= node.closest('.bubble')!.getBoundingClientRect().width)).toBe(true);
      }
      await page.locator('#ai-chat-messages-box').evaluate(node => { node.scrollTop = 0; });
      await page.screenshot({ path: `.codex-temp/ai-chat-markdown-${width}-${fullscreen ? 'fullscreen' : 'window'}.png`, fullPage: true });
    }
    expect(h.errors).toEqual([]);
  });
}
