import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function fixture(page: Page) {
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(`.${url.pathname}`);
      if (file.startsWith(path.resolve('static') + path.sep) && fs.existsSync(file)) return route.fulfill({
        contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(file),
      });
    }
    if (url.pathname !== '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><title>Destination</title><main><h1>已到达</h1></main>' });
    return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN" data-theme="lanshare" data-appearance="light"><head><meta charset="utf-8"><title>草稿保护</title><link rel="stylesheet" href="/static/css/tailwind-app.css"></head><body><main id="editor"><h1>编辑草稿</h1><form id="form" action="/saved"><label>正文<input name="draft" value="本地输入"></label><button type="submit">提交原生表单</button><button type="button" id="activate">保持编辑</button></form><a href="/next">下一页</a><a href="#part">当前页目录</a><a href="/other" target="_blank">新窗口</a><a href="/file" download>下载</a><p id="part">正文</p></main><script type="module">
      import {bindDirtyGuard} from '/static/js/lq/dirty-guard.js';
      window.bind=bindDirtyGuard;window.dirty=true;window.prompts=0;window.decisions=[];
      window.create=(extra={})=>window.guard=bindDirtyGuard(document.querySelector('#editor'),{isDirty:()=>dirty,navigation:true,confirmLeave:()=>{prompts++;return new Promise(resolve=>decisions.push(resolve));},...extra});
      window.ready=true;
    </script></body></html>` });
  });
  await page.goto('http://lq-dirty.test/');
  await page.waitForFunction(() => (window as any).ready);
}

test('dirty state remains controller-owned; refresh and duplicate URL bindings share ownership', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(async () => {
    const w = window as any; const root = document.querySelector('#editor');
    const first = w.create(); const second = await import('/static/js/lq/dirty-guard.js?duplicate');
    const same = second.bindDirtyGuard(root, { isDirty: () => true }) === first;
    const event = new Event('beforeunload', { cancelable: true }); dispatchEvent(event);
    w.dirty = false; first.refresh();
    const clean = new Event('beforeunload', { cancelable: true }); dispatchEvent(clean);
    return { same, dirty: event.defaultPrevented, clean: clean.defaultPrevented, allowed: await first.beforeClose('escape') };
  })).toEqual({ same: true, dirty: true, clean: false, allowed: true });
});

test('navigation cancel and duplicate clicks preserve draft, acceptance navigates once without second native prompt', async ({ page }) => {
  await fixture(page); await page.evaluate(() => (window as any).create());
  await page.getByRole('link', { name: '下一页' }).dblclick();
  expect(await page.evaluate(() => (window as any).prompts)).toBe(1);
  await page.evaluate(() => (window as any).decisions.shift()(false));
  await expect(page.getByRole('textbox')).toHaveValue('本地输入');
  expect(page.url()).toBe('http://lq-dirty.test/');
  await page.getByRole('link', { name: '下一页' }).click();
  let nativeDialogs = 0;
  page.on('dialog', async dialog => { nativeDialogs++; await dialog.dismiss(); });
  await page.evaluate(() => (window as any).decisions.shift()(true));
  await expect(page).toHaveURL('http://lq-dirty.test/next');
  expect(nativeDialogs).toBe(0);
});

test('new edits and programmatic refresh invalidate pending approval', async ({ page }) => {
  await fixture(page); await page.evaluate(() => (window as any).create());
  for (const edit of ['input', 'refresh']) {
    await page.getByRole('link', { name: '下一页' }).click();
    if (edit === 'input') await page.getByRole('textbox').fill('继续编辑的内容');
    else await page.evaluate(() => (window as any).guard.refresh());
    await page.evaluate(() => (window as any).decisions.shift()(true));
    await expect(page.getByRole('textbox')).toHaveValue('继续编辑的内容');
    expect(page.url()).toBe('http://lq-dirty.test/');
  }
});

test('native submit and requestSubmit retain actual FormData and do not trigger either confirmation', async ({ page }) => {
  for (const mode of ['click', 'requestSubmit', 'submit']) {
    await fixture(page); await page.evaluate(() => (window as any).create());
    let dialogs = 0;
    const handler = async (dialog: any) => { dialogs++; await dialog.dismiss(); };
    page.on('dialog', handler);
    if (mode === 'click') await page.getByRole('button', { name: '提交原生表单' }).click();
    else {
      await page.getByRole('button', { name: '保持编辑' }).click();
      await page.evaluate(method => (document.querySelector('#form') as HTMLFormElement)[method](), mode as 'submit' | 'requestSubmit');
    }
    await expect(page).toHaveURL(/\/saved\?draft=/);
    expect(new URL(page.url()).searchParams.get('draft')).toBe('本地输入');
    expect(dialogs).toBe(0);
    page.off('dialog', handler);
  }
});

test('canceled submit does not waive later unload', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(async () => {
    const w = window as any; w.create();
    const form = document.querySelector('#form') as HTMLFormElement;
    let result = false;
    form.addEventListener('submit', event => {
      event.preventDefault();
      const unload = new Event('beforeunload', { cancelable: true }); dispatchEvent(unload);
      result = unload.defaultPrevented;
    });
    form.requestSubmit();
    await new Promise(resolve => setTimeout(resolve, 10));
    const after = new Event('beforeunload', { cancelable: true }); dispatchEvent(after);
    return { during: result, after: after.defaultPrevented, prompts: w.prompts };
  })).toEqual({ during: true, after: true, prompts: 0 });
});

test('POST submits native entries and a new-window form keeps this page protected', async ({ page }) => {
  await fixture(page);
  await page.evaluate(() => { (window as any).create(); (document.querySelector('#form') as HTMLFormElement).target = '_blank'; });
  const popup = page.waitForEvent('popup');
  await page.getByRole('button', { name: '提交原生表单' }).click();
  await (await popup).close();
  expect(await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true }); dispatchEvent(event); return event.defaultPrevented;
  })).toBe(true);
  await page.evaluate(() => {
    const form = document.querySelector('#form') as HTMLFormElement; form.target = '_self'; form.method = 'post';
  });
  const request = page.waitForRequest(request => new URL(request.url()).pathname === '/saved' && request.method() === 'POST');
  let dialogs = 0;
  page.on('dialog', async dialog => { dialogs++; await dialog.dismiss(); });
  await page.getByRole('button', { name: '提交原生表单' }).click();
  expect(new URLSearchParams((await request).postData()!).get('draft')).toBe('本地输入');
  await expect(page).toHaveURL('http://lq-dirty.test/saved');
  expect(dialogs).toBe(0);
});

test('a separate dirty owner cannot inherit another owners navigation approval', async ({ page }) => {
  await fixture(page);
  await page.evaluate(() => {
    const w = window as any; w.create();
    const other = document.createElement('section'); document.body.append(other);
    w.otherGuard = w.bind(other, { isDirty: () => true });
  });
  await page.getByRole('link', { name: '下一页' }).click();
  const prompt = page.waitForEvent('dialog').then(async dialog => {
    expect(dialog.type()).toBe('beforeunload'); await dialog.dismiss();
  });
  await Promise.all([page.evaluate(() => (window as any).decisions.shift()(true)), prompt]);
  expect(page.url()).toBe('http://lq-dirty.test/');
  await expect(page.getByRole('textbox')).toHaveValue('本地输入');
});

test('twenty mount cycles remove document/navigation/unload listeners and late prompts', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(async () => {
    const w = window as any;
    const live = new Set<string>(); const functions = new WeakMap<object, number>(); let next = 0;
    const targets = [document, window, navigation]; const originals: Array<() => void> = [];
    targets.forEach((target, index) => {
      const add = target.addEventListener, remove = target.removeEventListener;
      const key = (type: string, listener: object, options: any) => {
        if (!functions.has(listener)) functions.set(listener, ++next);
        return `${index}:${type}:${functions.get(listener)}:${typeof options === 'boolean' ? options : Boolean(options?.capture)}`;
      };
      target.addEventListener = function(type: string, listener: any, options: any) {
        live.add(key(type, listener, options)); return add.call(this, type, listener, options);
      } as any;
      target.removeEventListener = function(type: string, listener: any, options: any) {
        live.delete(key(type, listener, options)); return remove.call(this, type, listener, options);
      } as any;
      originals.push(() => { target.addEventListener = add; target.removeEventListener = remove; });
    });
    let canceled = 0;
    try {
      for (let i = 0; i < 20; i++) {
        const guard = w.create(); const result = guard.requestLeave(); await Promise.resolve();
        guard.destroy(); if (!await result) canceled++;
        w.decisions.shift()(true); await Promise.resolve(); await Promise.resolve();
      }
      return { canceled, live: [...live] };
    } finally { originals.forEach(restore => restore()); }
  })).toEqual({ canceled: 20, live: [] });
});

test('missing Navigation API retains native protection and never cancels a submit event', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(() => {
    const w = window as any; Object.defineProperty(window, 'navigation', { value: undefined, configurable: true }); w.create();
    const submit = new Event('submit', { bubbles: true, cancelable: true }); document.querySelector('#form')!.dispatchEvent(submit);
    const unload = new Event('beforeunload', { cancelable: true }); dispatchEvent(unload);
    w.guard.destroy(); return { submit: submit.defaultPrevented, unload: unload.defaultPrevented };
  })).toEqual({ submit: false, unload: true });
});

test('link exclusions preserve native modifiers, downloads, targets and in-page navigation', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(() => {
    const w = window as any; w.create();
    const events = [];
    // Observe at window after the guard, then suppress real new-window/download
    // defaults only in this fixture; do not manufacture a browser popup result.
    window.addEventListener('click', event => { events.push(event.defaultPrevented); event.preventDefault(); });
    for (const [selector, options] of [
      ['a[href="/next"]', { ctrlKey: true }], ['a[href="/next"]', { metaKey: true }],
      ['a[href="/next"]', { button: 1 }], ['a[download]', {}], ['a[target]', {}], ['a[href="#part"]', {}],
    ] as const) document.querySelector(selector)!.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, ...options }));
    return { events, prompts: w.prompts };
  })).toEqual({ events: [false, false, false, false, false, false], prompts: 0 });
});

test('destroy settles waiting callers, cancels owned confirmation and ignores late approval', async ({ page }) => {
  await fixture(page);
  expect(await page.evaluate(async () => {
    const w = window as any; const guard = w.create();
    const first = guard.requestLeave(); const same = first === guard.requestLeave();
    await Promise.resolve(); guard.destroy(); guard.destroy();
    const result = await first;
    w.decisions.shift()(true); await Promise.resolve();
    const unload = new Event('beforeunload', { cancelable: true }); dispatchEvent(unload);
    return { same, result, blocked: unload.defaultPrevented, after: await guard.requestLeave() };
  })).toEqual({ same: true, result: false, blocked: false, after: false });
});

test('default beforeClose uses one shared-layer safe confirmation and preserves parent input', async ({ page }) => {
  await fixture(page);
  await page.evaluate(async () => {
    const w = window as any;
    const dialogs = await import('/static/js/lq/dialogs.js');
    const input = document.querySelector('#form');
    w.parentRoot = dialogs.createDialog({ title: '编辑内容', body: input });
    document.body.append(w.parentRoot);
    w.parentGuard = w.bind(w.parentRoot, { isDirty: () => true });
    w.parentHandle = dialogs.openDialog(w.parentRoot, { trigger: document.querySelector('a[href="/next"]'), beforeClose: w.parentGuard.beforeClose });
  });
  await page.getByRole('textbox').fill('未保存内容');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '离开当前编辑？' })).toBeVisible();
  await expect(page.getByRole('button', { name: '继续编辑' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: '离开当前编辑？' })).toBeHidden();
  await expect(page.getByRole('textbox')).toHaveValue('未保存内容');
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '放弃并离开' }).click();
  await expect(page.getByRole('dialog', { name: '编辑内容', exact: true })).toBeHidden();
});
