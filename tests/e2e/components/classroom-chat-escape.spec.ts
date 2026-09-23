import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const styles = fs.readFileSync('static/css/tailwind-app.css', 'utf8');
const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jGRkAAAAASUVORK5CYII=';

async function mount(page: Page) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) {
        return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
      }
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html>
      <html><head><meta charset="utf-8"><style>${styles}</style></head><body>
      <button id="external-trigger">外部入口</button>
      <div id="external-dialog" role="dialog" aria-modal="true" hidden><button id="external-close">关闭外部弹层</button></div>
      <section id="chat-room"><div id="chat-messages"></div>
        <form id="chat-form"><textarea id="chat-input">保留草稿</textarea></form>
        <button id="emoji-trigger">表情</button><div id="emoji-panel" hidden><button id="emoji-item">表情选项</button></div>
        <div id="message-menu" hidden><button id="message-item">消息操作</button></div>
        <button id="attachment">图片附件</button>
      </section>
      <script type="module">
        import { ClassroomChat } from '/static/js/chat.js';
        import { openImageLightbox } from '/static/js/ls_image_lightbox.js';
        window.chat = new ClassroomChat({ classOfferingId:1, chatMessagesContainerId:'chat-messages', chatInputId:'chat-input', chatFormId:'chat-form', emojiTriggerButtonId:'emoji-trigger', emojiPopoverId:'emoji-panel', messageMenuId:'message-menu', discussionRoomId:'chat-room' });
        window.externalImage = () => openImageLightbox({items:[{src:${JSON.stringify(image)},title:'外部图片'}]});
        document.getElementById('attachment').onclick = () => window.chat.openAttachmentPreview({attachment_id:1,mime_type:'image/png',preview_url:${JSON.stringify(image)}});
        // Match an earlier external modal listener that restores focus before
        // the chat document listener sees the same Escape event.
        document.addEventListener('keydown', event => {
          if (event.key === 'Escape' && event.target.closest?.('#external-dialog')) {
            document.getElementById('external-dialog').hidden = true;
            document.getElementById('external-trigger').focus();
          }
        });
        document.addEventListener('keydown', window.chat.handleDocumentKeydown);
        document.addEventListener('keydown', event => { window.lastEscapeConsumed = event.defaultPrevented; });
        document.body.dataset.ready = 'true';
      </script></body></html>` });
    return route.abort();
  });
  await page.goto('http://classroom-chat.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test('Escape without a chat layer preserves external focus and the ordinary chat draft', async ({ page }) => {
  await mount(page);
  const external = page.locator('#external-trigger');
  await external.focus();
  await page.keyboard.press('Escape');
  await expect(external).toBeFocused();
  expect(await page.evaluate(() => (window as any).lastEscapeConsumed)).toBe(false);
  const input = page.locator('#chat-input');
  await input.focus();
  await input.evaluate(node => (node as HTMLTextAreaElement).setSelectionRange(1, 3));
  await page.keyboard.press('Escape');
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('保留草稿');
  expect(await input.evaluate(node => [(node as HTMLTextAreaElement).selectionStart, (node as HTMLTextAreaElement).selectionEnd])).toEqual([1, 3]);
  expect(await page.evaluate(() => (window as any).lastEscapeConsumed)).toBe(false);
});

test('completed AI streams use the same single action footer as regular messages', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const chat = (window as any).chat;
    chat.handleDiscussionAiStreamStart({ stream_id: 'layout-stream', sender: '课堂助教', role: 'assistant' });
    chat.handleDiscussionAiStreamDone({ stream_id: 'layout-stream', id: 42, sender: '课堂助教', role: 'assistant', message: '完整回答' });
    // A repeated lifecycle completion must not duplicate the footer.
    chat.ensureMessageActions(document.querySelector('[data-message-id="42"]'), 42);
    chat.appendChatMessage({ id: 43, sender: '课堂同学', role: 'student', message: '收到' });
  });
  for (const id of [42, 43]) {
    const message = page.locator(`.chat-message[data-message-id="${id}"]`);
    await expect(message.locator('.chat-message-main > .chat-message-actions')).toHaveCount(1);
    await expect(message.locator('.chat-message-header .chat-message-actions')).toHaveCount(0);
    await expect(message.locator('.chat-message-main > :last-child')).toHaveClass('chat-message-actions');
  }
});

test('an external dialog keeps its restored focus without closing background chat panels', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const emoji = document.getElementById('emoji-panel')!;
    emoji.hidden = false; emoji.classList.add('is-open');
    document.getElementById('message-menu')!.hidden = false;
    document.getElementById('external-dialog')!.hidden = false;
    document.getElementById('external-close')!.focus();
  });
  await page.keyboard.press('Escape');
  await expect(page.locator('#external-dialog')).toBeHidden();
  await expect(page.locator('#external-trigger')).toBeFocused();
  await expect(page.locator('#emoji-panel')).toHaveClass('is-open');
  await expect(page.locator('#message-menu')).toBeVisible();
});

test('consumed and composing Escape events do not dismiss chat layers', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const emoji = document.getElementById('emoji-panel')!;
    emoji.hidden = false; emoji.classList.add('is-open');
    const target = document.getElementById('emoji-item')!;
    target.focus();
    target.addEventListener('keydown', event => event.preventDefault(), { once: true });
  });
  await page.keyboard.press('Escape');
  await expect(page.locator('#emoji-panel')).toHaveClass('is-open');
  await expect(page.locator('#emoji-item')).toBeFocused();
  await page.locator('#emoji-item').dispatchEvent('keydown', { key: 'Escape', isComposing: true });
  await expect(page.locator('#emoji-panel')).toHaveClass('is-open');
});

test('a dialog hosting this chat still closes its emoji panel from the composer', async ({ page }) => {
  await mount(page);
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'chat-host'; host.setAttribute('role', 'dialog');
    document.body.append(host);
    host.append(document.getElementById('chat-room')!);
    const emoji = document.getElementById('emoji-panel')!;
    emoji.hidden = false; emoji.classList.add('is-open');
    document.getElementById('chat-input')!.focus();
  });
  await page.keyboard.press('Escape');
  await expect(page.locator('#emoji-panel')).toBeHidden();
  await expect(page.locator('#chat-host')).toBeVisible();
  await expect(page.locator('#chat-input')).toBeFocused();
  expect(await page.evaluate(() => (window as any).lastEscapeConsumed)).toBe(true);
});

for (const panel of ['emoji', 'message']) {
  test(`Escape closes the owned ${panel} panel and returns focus to its composer`, async ({ page }) => {
    await mount(page);
    await page.evaluate(panel => {
      const node = document.getElementById(panel === 'emoji' ? 'emoji-panel' : 'message-menu')!;
      node.hidden = false;
      if (panel === 'emoji') node.classList.add('is-open');
      node.querySelector('button')!.focus();
    }, panel);
    await page.keyboard.press('Escape');
    await expect(page.locator(panel === 'emoji' ? '#emoji-panel' : '#message-menu')).toBeHidden();
    await expect(page.locator('#chat-input')).toBeFocused();
    await expect(page.locator('#chat-input')).toHaveValue('保留草稿');
    expect(await page.evaluate(() => (window as any).lastEscapeConsumed)).toBe(true);
  });
}

test('shared attachment and external image previews restore their own opener', async ({ page }) => {
  await mount(page);
  await page.locator('#attachment').click();
  await expect(page.locator('.ls-lightbox')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.ls-lightbox')).toBeHidden();
  await expect(page.locator('#attachment')).toBeFocused();
  await page.locator('#external-trigger').focus();
  await page.evaluate(() => {
    document.getElementById('message-menu')!.hidden = false;
    (window as any).externalImage();
  });
  await expect(page.locator('.ls-lightbox')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.ls-lightbox')).toBeHidden();
  await expect(page.locator('#external-trigger')).toBeFocused();
  await expect(page.locator('#message-menu')).toBeVisible();
});
