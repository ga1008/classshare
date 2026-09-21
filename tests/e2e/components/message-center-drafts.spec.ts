import { test, expect, type Page, type Route } from '@playwright/test';
import { bootstrap, contacts, conversation, mountMessageCenter } from '../fixtures/message-center-controller';

const input = '#message-center-compose-input';
const preview = '#message-center-attachment-preview';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jBScAAAAASUVORK5CYII=', 'base64');

// Observe the real browser File references and native FormData appends. No
// controller state, API response, timers or DOM values are replaced by this probe.
async function mount(page: Page) {
  await page.addInitScript(() => {
    const probe = (window as any).__draftProbe = { created: [], revoked: [], forms: [] };
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => {
      const url = create(blob);
      probe.created.push({ url, file: blob });
      return url;
    };
    URL.revokeObjectURL = url => { probe.revoked.push(url); revoke(url); };
    const NativeFormData = window.FormData;
    window.FormData = class extends NativeFormData {
      constructor(...args: ConstructorParameters<typeof FormData>) {
        super(...args);
        probe.forms.push(this);
      }
    };
    // Retain the original File passed to append, since native FormData may wrap
    // it when an explicit filename is supplied by the controller.
    const append = NativeFormData.prototype.append;
    window.FormData.prototype.append = function (name: string, value: string | Blob, filename?: string) {
      if (value instanceof File) {
        const form = this as FormData & { originalFiles?: File[] };
        (form.originalFiles ||= []).push(value);
      }
      if (typeof value === 'string') append.call(this, name, value);
      else if (filename === undefined) append.call(this, name, value);
      else append.call(this, name, value, filename);
    } as typeof NativeFormData.prototype.append;
  });
  const h = await mountMessageCenter(page);
  await h.readyA();
  await page.evaluate(() => {
    const p = (window as any).__draftProbe;
    p.input = document.querySelector('#message-center-compose-input');
    p.fileInput = document.querySelector('#message-center-file-input');
    p.form = document.querySelector('#message-center-compose-form');
  });
  return h;
}

async function attach(page: Page, name: string) {
  await page.locator('#message-center-image-input').setInputFiles({ name, mimeType: 'image/png', buffer: png });
  await expect(page.locator(preview).getByRole('button', { name: `移除 ${name}`, exact: true })).toBeVisible();
}

async function switchTo(page: Page, h: Awaited<ReturnType<typeof mount>>, identity: string, scope = 10) {
  await h.select(identity, scope);
  await h.reply(await h.take('/private/conversation', { contact: identity, scope: String(scope) }), conversation(identity, scope, `${identity}/${scope} current`));
  await expect.poll(() => page.evaluate(() => (window as any).__LANSHARE_MESSAGE_CENTER_WORKSPACE__.loadStatus)).toBe('ready');
}

async function send(page: Page, h: Awaited<ReturnType<typeof mount>>) {
  await page.locator('[data-send-button]').click();
  return h.take('/private/messages');
}

function success(identity = 'teacher:1', scope = 10) {
  return {
    contact: contacts.find(item => item.identity === identity && item.class_offering_id === scope),
    conversation_key: `student:99|${identity}|${scope}`,
    sent_message: { id: `sent-${identity}-${scope}`, content: 'A sent response', sender_display_name: '本人', created_at: '2026-09-21T09:00:00', attachments: [] },
  };
}

async function checkPayload(route: Route, identity: string, scope: number, content: string, files: string[]) {
  expect(route.request().method()).toBe('POST');
  const headers = await route.request().allHeaders();
  if (files.length) {
    expect(headers['content-type']).toContain('multipart/form-data; boundary=');
    const body = route.request().postDataBuffer()!.toString('latin1');
    for (const [name, value] of Object.entries({ contact_identity: identity, class_offering_id: String(scope), content })) {
      expect(body).toContain(`name="${name}"\r\n\r\n${value}\r\n`);
    }
    expect([...body.matchAll(/filename="([^"]+)"/g)].map(match => match[1])).toEqual(files);
    for (const name of files) expect(body).toContain(`filename="${name}"\r\nContent-Type: image/png\r\n\r\n${png.toString('latin1')}`);
  } else {
    expect(route.request().postDataJSON()).toEqual({ contact_identity: identity, class_offering_id: scope, content });
  }
}

async function urls(page: Page) {
  return page.evaluate(() => {
    const p = (window as any).__draftProbe;
    return { created: p.created.map((item: any) => ({ url: item.url, name: item.file.name })), revoked: p.revoked };
  });
}

async function advance(page: Page) {
  await page.clock.install();
  await page.clock.fastForward(60_000);
  await page.clock.resume();
}

async function stableNodes(page: Page) {
  expect(await page.evaluate(() => {
    const p = (window as any).__draftProbe;
    return p.input === document.querySelector('#message-center-compose-input')
      && p.fileInput === document.querySelector('#message-center-file-input')
      && p.form === document.querySelector('#message-center-compose-form');
  })).toBe(true);
}

test.describe('LQ C2.2 actual Profile controller draft and send ownership', () => {
  test('held A send cannot clear B text, File, URL, focus or messages; B sends its original File', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('A submitted');
    await attach(page, 'A.png');
    const postA = await send(page, h);
    await checkPayload(postA, 'teacher:1', 10, 'A submitted', ['A.png']);
    await switchTo(page, h, 'teacher:2');
    await expect(page.locator(input)).toHaveValue('');
    await expect(page.locator(preview)).toBeHidden();
    await page.locator(input).fill('B independent draft');
    await attach(page, 'B.png');
    await page.locator(input).focus();
    const before = await urls(page);
    expect(before.revoked).toEqual([]);
    const getCount = h.requests.filter(request => request.startsWith('GET ')).length;
    await h.reply(postA, success());
    await expect(page.locator(input)).toHaveValue('B independent draft');
    await expect(page.locator(input)).toBeFocused();
    await expect(page.locator(preview).getByRole('button', { name: '移除 B.png' })).toBeVisible();
    await expect(page.locator('#message-center-conversation-body')).toContainText('teacher:2/10 current');
    await expect(page.locator('#message-center-conversation-body')).not.toContainText('A sent response');
    expect((await urls(page)).revoked).toEqual([before.created[0].url]);
    expect(h.requests.filter(request => request.startsWith('GET '))).toHaveLength(getCount);
    await advance(page);
    const postB = await send(page, h);
    await checkPayload(postB, 'teacher:2', 10, 'B independent draft', ['B.png']);
    expect(await page.evaluate(() => {
      const p = (window as any).__draftProbe;
      return p.forms[1].originalFiles[0] === p.created.find((item: any) => item.file.name === 'B.png').file;
    })).toBe(true);
    await h.reply(postB, success('teacher:2'));
    await stableNodes(page);
    expect(h.errors).toEqual([]);
  });

  test('continued A edits and newly added attachments survive; only submitted URLs are released', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('A submitted');
    await attach(page, 'sent.png');
    const post = await send(page, h);
    await page.locator(input).fill('A next draft');
    await attach(page, 'next.png');
    const before = await urls(page);
    await checkPayload(post, 'teacher:1', 10, 'A submitted', ['sent.png']);
    await h.reply(post, success());
    await expect(page.locator(input)).toHaveValue('A next draft');
    await expect(page.locator(preview).getByRole('button', { name: '移除 next.png' })).toBeVisible();
    await expect(page.locator(preview).getByRole('button', { name: '移除 sent.png' })).toHaveCount(0);
    expect((await urls(page)).revoked).toEqual([before.created[0].url]);
    await page.locator(preview).getByRole('button', { name: '移除 next.png' }).click();
    expect((await urls(page)).revoked).toEqual(before.created.map((item: any) => item.url));
    await stableNodes(page);
    expect(h.errors).toEqual([]);
  });

  for (const edit of ['markdown', 'emoji']) test(`A to B to A ${edit} programmatic write is retained after pending A success`, async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('original');
    const post = await send(page, h);
    await switchTo(page, h, 'teacher:2');
    await page.locator(input).fill('B saved');
    await switchTo(page, h, 'teacher:1');
    await expect(page.locator(input)).toHaveValue('original');
    await page.locator(input).evaluate((node: HTMLTextAreaElement) => node.setSelectionRange(node.value.length, node.value.length));
    if (edit === 'markdown') await page.locator('[data-md-insert="bold"]').click();
    else {
      await page.locator('#message-center-emoji-trigger').click();
      await page.locator('.emoji-picker-item').first().click();
    }
    const changed = await page.locator(input).inputValue();
    expect(changed).not.toBe('original');
    await h.reply(post, success());
    await expect(page.locator(input)).toHaveValue(changed);
    await switchTo(page, h, 'teacher:2');
    await expect(page.locator(input)).toHaveValue('B saved');
    await switchTo(page, h, 'teacher:1');
    await expect(page.locator(input)).toHaveValue(changed);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await stableNodes(page);
    expect(h.errors).toEqual([]);
  });

  test('editing away and back to the submitted text does not erase the new revision', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('same text');
    const post = await send(page, h);
    await page.locator(input).fill('different revision');
    await page.locator(input).fill('same text');
    await h.reply(post, success());
    await expect(page.locator(input)).toHaveValue('same text');
    expect(h.errors).toEqual([]);
  });

  test('the same contact in two classroom scopes owns separate text, Files and sent payload', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('scope ten');
    await attach(page, 'ten.png');
    await switchTo(page, h, 'teacher:1', 20);
    await expect(page.locator(input)).toHaveValue('');
    await expect(page.locator(preview)).toBeHidden();
    await page.locator(input).fill('scope twenty');
    await attach(page, 'twenty.png');
    const post = await send(page, h);
    await checkPayload(post, 'teacher:1', 20, 'scope twenty', ['twenty.png']);
    await switchTo(page, h, 'teacher:1', 10);
    await h.reply(post, success('teacher:1', 20));
    await expect(page.locator(input)).toHaveValue('scope ten');
    await expect(page.locator(preview).getByRole('button', { name: '移除 ten.png' })).toBeVisible();
    const observed = await urls(page);
    expect(observed.revoked).toEqual([observed.created[1].url]);
    await switchTo(page, h, 'teacher:1', 20);
    await expect(page.locator(input)).toHaveValue('');
    await expect(page.locator(preview)).toBeHidden();
    await stableNodes(page);
    expect(h.errors).toEqual([]);
  });

  for (const status of [429, 503]) test(`${status} preserves both drafts and attachments without an automatic POST retry`, async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('A failed draft');
    await attach(page, 'A-failed.png');
    const post = await send(page, h);
    await switchTo(page, h, 'teacher:2');
    await page.locator(input).fill('B while A failed');
    await attach(page, 'B-kept.png');
    await h.reply(post, status === 429
      ? { detail: { message: '发送太频繁', retry_after_seconds: 12 }, code: 'rate_limited',
        error: { code: 'rate_limited', message: '发送太频繁', details: { retry_after_seconds: 12 } } }
      : { detail: '服务暂时不可用，草稿保留' }, status);
    await expect(page.locator(input)).toHaveValue('B while A failed');
    await expect(page.locator(input)).toBeEditable();
    await expect(page.locator(preview).getByRole('button', { name: '移除 B-kept.png' })).toBeVisible();
    if (status === 429) await expect(page.locator('[data-send-button]')).toBeDisabled();
    await switchTo(page, h, 'teacher:1');
    await expect(page.locator(input)).toHaveValue('A failed draft');
    await expect(page.locator(preview).getByRole('button', { name: '移除 A-failed.png' })).toBeVisible();
    expect((await urls(page)).revoked).toEqual([]);
    await advance(page);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    await expect(page.locator('[data-send-button]')).toBeEnabled();
    expect(h.errors).toEqual([]);
  });

  test('lost POST response is explicitly uncertain and never automatically resent', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('possibly committed');
    await attach(page, 'uncertain.png');
    const post = await send(page, h);
    await checkPayload(post, 'teacher:1', 10, 'possibly committed', ['uncertain.png']);
    // The route has already captured the complete POST; the response is lost.
    await post.abort('connectionreset');
    await expect(page.getByText('发送状态尚未确认，草稿已保留，请查看会话后再决定是否重发。', { exact: true })).toBeVisible();
    await expect(page.locator(input)).toHaveValue('possibly committed');
    await expect(page.locator(preview).getByRole('button', { name: '移除 uncertain.png' })).toBeVisible();
    expect((await urls(page)).revoked).toEqual([]);
    await advance(page);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    expect(h.errors).toEqual([]);
  });

  for (const invalid of ['message-id', 'contact', 'conversation-key']) test(`malformed 200 ${invalid} does not discard a possibly committed draft`, async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('unconfirmed receipt');
    await attach(page, 'unconfirmed.png');
    const post = await send(page, h);
    const receipt = success();
    if (invalid === 'message-id') receipt.sent_message.id = '';
    if (invalid === 'contact') receipt.contact = contacts[1];
    if (invalid === 'conversation-key') receipt.conversation_key = '';
    await h.reply(post, receipt);
    await expect(page.getByText('发送状态尚未确认，草稿已保留，请查看会话后再决定是否重发。', { exact: true })).toBeVisible();
    await expect(page.locator(input)).toHaveValue('unconfirmed receipt');
    await expect(page.locator(preview).getByRole('button', { name: '移除 unconfirmed.png' })).toBeVisible();
    expect((await urls(page)).revoked).toEqual([]);
    await advance(page);
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    expect(h.errors).toEqual([]);
  });

  test('success during an A refresh replaces the pre-commit GET and retains a continued A draft', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('sent before refresh');
    const post = await send(page, h);
    await page.locator(input).fill('continued while refreshing');
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('lanshare:message-center-workspace-command', { detail: { type: 'refresh' } })));
    await h.reply(await h.take('/bootstrap'), bootstrap);
    const preCommit = await h.take('/private/conversation', { contact: 'teacher:1', scope: '10' });
    await h.reply(post, success());
    const postCommit = await h.take('/private/conversation', { contact: 'teacher:1', scope: '10' });
    await h.reply(postCommit, conversation('teacher:1', 10, 'post-commit A visible'));
    await h.reply(preCommit, conversation('teacher:1', 10, 'pre-commit stale A'));
    await expect(page.locator('#message-center-conversation-body')).toContainText('post-commit A visible');
    await expect(page.locator('#message-center-conversation-body')).not.toContainText('pre-commit stale A');
    await expect(page.locator(input)).toHaveValue('continued while refreshing');
    expect(h.requests.filter(request => request.startsWith('POST '))).toHaveLength(1);
    expect(h.requests.filter(request => request.includes('/private/conversation?'))).toHaveLength(3);
    expect(h.errors).toEqual([]);
  });

  test('beforeunload and persisted pagehide keep all drafts; final pagehide revokes every remaining URL once', async ({ page }) => {
    const h = await mount(page);
    await page.locator(input).fill('A pending lifecycle');
    await attach(page, 'A-life.png');
    const post = await send(page, h);
    await switchTo(page, h, 'teacher:2');
    await page.locator(input).fill('B lifecycle');
    await attach(page, 'B-life.png');
    await page.evaluate(() => {
      window.dispatchEvent(new Event('beforeunload'));
      window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
      window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
    });
    expect((await urls(page)).revoked).toEqual([]);
    await expect(page.locator(input)).toHaveValue('B lifecycle');
    await switchTo(page, h, 'teacher:1');
    await expect(page.locator(input)).toHaveValue('A pending lifecycle');
    await expect(page.locator(preview).getByRole('button', { name: '移除 A-life.png' })).toBeVisible();
    const before = await urls(page);
    await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: false })));
    expect((await urls(page)).revoked.sort()).toEqual(before.created.map((item: any) => item.url).sort());
    await h.reply(post, success());
    // A response after destruction must not settle its sent attachment again.
    expect((await urls(page)).revoked).toHaveLength(2);
    await expect(page.locator('#message-center-conversation-body')).not.toContainText('A sent response');
    expect(h.errors).toEqual([]);
  });
});
