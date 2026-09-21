import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

async function mount(page: Page) {
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.startsWith('/static/js/')) {
      const file = path.resolve(`.${pathname}`);
      if (file.startsWith(path.resolve('static/js') + path.sep) && fs.existsSync(file)) return route.fulfill({ contentType: 'text/javascript', body: fs.readFileSync(file) });
    }
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync('tests/e2e/components/fixtures/lq-layer-core.html') });
    return route.abort();
  });
  await page.goto('http://lq-layer.test/');
  await expect(page.locator('body')).toHaveAttribute('data-ready', 'true');
}

test.describe('LQ layer core', () => {
test.beforeEach(async ({ page }) => mount(page));

test('document singleton survives duplicate asset imports; repeated open and update keep one handle', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const duplicate = await import('/static/js/lq/layer.js?other-asset');
    const a = w.openLayer('a', { trigger: document.querySelector('#before') });
    const b = w.openLayer('a', { onClose: () => w.events.push('updated') });
    const singleton = duplicate.getLayerSystem(document) === w.system;
    const one = a === b && w.system.top() === a;
    await w.system.close(a);
    return { singleton, one, events: w.events, state: a.state, top: w.system.top() };
  })).toEqual({ singleton: true, one: true, events: ['updated'], state: 'closed', top: null });
});

test('external registration restores borrowed layer attributes and style priority on destruction', async ({ page }) => {
  expect(await page.evaluate(() => {
    const root = document.createElement('aside'); document.body.append(root);
    root.setAttribute('data-lq-layer-state', 'original'); root.style.setProperty('--lq-layer-order', '37', 'important');
    let open = true;
    const registration = (window as any).system.registerExternal({ root: () => root, trigger: () => document.querySelector('#before'),
      isOpen: () => open, dismissTop: () => { open = false; } });
    const during = root.getAttribute('data-lq-layer-state'); registration.destroy();
    return { during, state: root.getAttribute('data-lq-layer-state'), order: root.style.getPropertyValue('--lq-layer-order'),
      priority: root.style.getPropertyPriority('--lq-layer-order') };
  })).toEqual({ during: 'open', state: 'original', order: '37', priority: 'important' });
});

test('nested modals keep lock/inert until the final exit, restore originals and return focus', async ({ page }) => {
  await page.locator('#before').focus();
  await page.evaluate(() => { (window as any).openLayer('a'); });
  await expect(page.locator('#a-first')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(page.locator('#a-last')).toBeFocused();
  await page.evaluate(() => { (window as any).openLayer('b', { trigger: document.querySelector('#a-first') }); });
  await expect(page.locator('#b-first')).toBeFocused();
  expect(await page.evaluate(() => ({ body: document.body.style.overflow, parent: (window as any).handles.b.parentLayer === (window as any).handles.a, inert: (document.querySelector('#a') as HTMLElement).inert }))).toEqual({ body: 'hidden', parent: true, inert: true });
  await page.keyboard.press('Escape');
  await expect(page.locator('#b')).toBeHidden();
  await expect(page.locator('#a-first')).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('hidden');
  await page.keyboard.press('Escape');
  await expect(page.locator('#a')).toBeHidden();
  await expect(page.locator('#before')).toBeFocused();
  expect(await page.evaluate(() => ({ overflow: document.body.style.overflow, padding: document.body.style.paddingRight, aria: document.querySelector('#background')!.getAttribute('aria-hidden'), inert: (document.querySelector('#pre-inert') as HTMLElement).inert }))).toEqual({ overflow: '', padding: '', aria: 'false', inert: true });
});

test('empty modal traps Tab, honors consumed/composing Escape and consumes only the top', async ({ page }) => {
  await page.evaluate(() => { const w = window as any; w.create('empty', { empty: true }); w.openLayer('empty'); });
  await expect(page.locator('#empty-surface')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('#empty-surface')).toBeFocused();
  await page.locator('#empty-surface').dispatchEvent('keydown', { key: 'Escape', isComposing: true });
  await expect(page.locator('#empty')).toBeVisible();
  await page.locator('#empty-surface').evaluate(node => node.addEventListener('keydown', event => event.preventDefault(), { once: true }));
  await page.keyboard.press('Escape');
  await expect(page.locator('#empty')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#empty')).toBeHidden();
});

test('zero-width overlay scrollbar leaves the root compensation variable unowned', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const root = document.documentElement;
    Object.defineProperty(root, 'clientWidth', { configurable: true, get: () => innerWidth });
    const writes: string[] = [];
    const observer = new MutationObserver(records => records.forEach(record => writes.push(record.attributeName!)));
    observer.observe(root, { attributes: true, attributeFilter: ['style'] });
    const parent = w.openLayer('a');
    const child = w.openLayer('b', { parentLayer: parent });
    await w.system.close(child);
    await w.system.close(parent);
    await Promise.resolve(); observer.disconnect();
    const untouched = { writes, gap: root.style.getPropertyValue('--lq-scrollbar-w') };
    const again = w.openLayer('a');
    // The coordinator must not undo a value introduced by another owner while
    // it holds a zero-gap lock that never borrowed this custom property.
    root.style.setProperty('--lq-scrollbar-w', '9px', 'important');
    again.destroy();
    return { untouched, final: root.style.getPropertyValue('--lq-scrollbar-w'),
      priority: root.style.getPropertyPriority('--lq-scrollbar-w'), lock: document.body.style.overflow };
  })).toEqual({ untouched: { writes: [], gap: '' }, final: '9px', priority: 'important', lock: '' });
});

test('initial focus skips ineligible targets and stops geometry inspection at the chosen target', async ({ page }) => {
  expect(await page.evaluate(() => {
    const w = window as any;
    const root = w.create('long-nav');
    const surface = root.firstElementChild;
    surface.innerHTML = '<button data-autofocus hidden>Hidden preference</button><button disabled>Disabled</button><button tabindex="-1">Not in tab order</button><button id="eligible">First eligible</button>'
      + Array.from({ length: 120 }, (_, index) => `<button data-later="${index}">Later ${index}</button>`).join('');
    const original = window.getComputedStyle;
    let laterStyleReads = 0;
    window.getComputedStyle = function (node, pseudo) {
      if (node.hasAttribute('data-later')) laterStyleReads++;
      return original.call(this, node, pseudo);
    };
    try {
      w.openLayer('long-nav');
      return { focus: document.activeElement?.id, laterStyleReads };
    } finally { window.getComputedStyle = original; }
  })).toEqual({ focus: 'eligible', laterStyleReads: 0 });
});

test('native modal captures the original scroll geometry before writes and batches isolation before showModal', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any, root = w.create('batch', { native: true }) as HTMLDialogElement;
    document.querySelector<HTMLElement>('#before')!.focus();
    const observations: unknown[] = [], background = document.querySelector<HTMLElement>('#background')!;
    Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, get() {
      observations.push({ at: 'read', role: root.firstElementChild!.getAttribute('role'),
        overflow: document.body.style.overflow, inert: background.inert });
      return innerWidth - 17;
    } });
    const nativeShow = root.showModal;
    root.showModal = function () {
      observations.push({ at: 'show', overflow: document.body.style.overflow, inert: background.inert,
        aria: background.getAttribute('aria-hidden'), order: this.style.getPropertyValue('--lq-layer-order') });
      nativeShow.call(this);
    };
    const handle = w.openLayer('batch', { trigger: document.querySelector('#before') });
    const focused = document.activeElement?.id;
    await w.system.close(handle);
    return { observations, focused, after: { focus: document.activeElement?.id, overflow: document.body.style.overflow,
      inert: background.inert, aria: background.getAttribute('aria-hidden') } };
  })).toEqual({ observations: [{ at: 'read', role: null, overflow: '', inert: false },
    { at: 'show', overflow: 'hidden', inert: true, aria: 'true', order: '1' }], focused: 'batch-first',
    after: { focus: 'before', overflow: '', inert: false, aria: 'false' } });
});

for (const nativeInert of [true, false]) {
test(`batched native initial focus hook preserves cancellation and return focus with native inert ${nativeInert}`, async ({ page }) => {
  await page.evaluate(nativeInert => {
    if (!nativeInert) delete (HTMLElement.prototype as any).inert;
    const w = window as any; w.create('hook-native', { native: true });
    document.querySelector<HTMLElement>('#before')!.focus();
    w.openLayer('hook-native', { trigger: document.querySelector('#before'), onInitialFocus: (event: Event, handle: any) => {
      event.preventDefault(); w.events.push('initial'); handle.root.querySelector('#hook-native-last').focus();
    } });
  }, nativeInert);
  await expect(page.locator('#hook-native-last')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('#hook-native-first')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#hook-native')).toBeHidden();
  await expect(page.locator('#before')).toBeFocused();
  expect(await page.evaluate(() => ({ events: (window as any).events, tabindex: document.querySelector('#before')!.getAttribute('tabindex'),
    aria: document.querySelector('#background')!.getAttribute('aria-hidden'), overflow: document.body.style.overflow })))
    .toEqual({ events: ['initial'], tabindex: null, aria: 'false', overflow: '' });
});
}

test('batched native children relocate a live companion after native display and preserve its real actions and focus', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any, notification = document.createElement('aside');
    notification.id = 'companion'; notification.innerHTML = '<button id="companion-action">Notification action</button>';
    notification.querySelector('button')!.addEventListener('click', () => w.events.push('action'));
    document.body.append(notification); w.companion = w.system.registerCompanion(notification);
    document.querySelector<HTMLElement>('#before')!.focus();
    w.create('companion-parent', { native: true }); w.openLayer('companion-parent', { trigger: document.querySelector('#before') });
  });
  await expect(page.locator('#companion-parent > #companion')).toBeVisible();
  await page.locator('#companion-action').click();
  await page.evaluate(() => {
    const w = window as any, child = w.create('companion-child', { native: true, attach: false });
    w.handles['companion-parent'].root.append(child);
    w.openLayer('companion-child', { trigger: document.querySelector('#companion-parent-first') });
  });
  await expect(page.locator('#companion-child > #companion')).toBeVisible();
  await page.locator('#companion-action').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#companion-child')).toBeHidden();
  await expect(page.locator('#companion-parent > #companion')).toBeVisible();
  await expect(page.locator('#companion-parent-first')).toBeFocused();
  await page.locator('#companion-action').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('body > #companion')).toBeVisible();
  await expect(page.locator('#before')).toBeFocused();
  expect(await page.evaluate(() => { (window as any).companion.destroy(); return { events: (window as any).events,
    overflow: document.body.style.overflow, top: (window as any).system.top() }; }))
    .toEqual({ events: ['action', 'action', 'action'], overflow: '', top: null });
});

test('failed native display restores batched scroll and isolation without stealing companion ownership', async ({ page }) => {
  expect(await page.evaluate(() => {
    const w = window as any, root = w.create('failed-native', { native: true }) as HTMLDialogElement;
    const notification = document.createElement('aside'); document.body.append(notification);
    const companion = w.system.registerCompanion(notification);
    document.body.style.setProperty('overflow', 'auto', 'important');
    document.documentElement.style.setProperty('--lq-scrollbar-w', '9px', 'important');
    root.showModal = () => { throw new DOMException('display rejected', 'InvalidStateError'); };
    let error = ''; try { w.openLayer('failed-native'); } catch (caught) { error = (caught as Error).name; }
    const result = { error, hidden: root.hidden, open: root.open, top: w.system.top(),
      overflow: document.body.style.overflow, overflowPriority: document.body.style.getPropertyPriority('overflow'),
      gap: document.documentElement.style.getPropertyValue('--lq-scrollbar-w'), priority: document.documentElement.style.getPropertyPriority('--lq-scrollbar-w'),
      inert: (document.querySelector('#background') as HTMLElement).inert, aria: document.querySelector('#background')!.getAttribute('aria-hidden'),
      companionParent: notification.parentElement?.tagName };
    companion.destroy(); return result;
  })).toEqual({ error: 'InvalidStateError', hidden: true, open: false, top: null, overflow: 'auto', overflowPriority: 'important',
    gap: '9px', priority: 'important', inert: false, aria: 'false', companionParent: 'BODY' });
});

for (const mode of ['normal', 'cancel', 'false', 'custom', 'outside', 'destroy']) {
test(`native exit preserves explicit return-focus ownership for ${mode}`, async ({ page }) => {
  expect(await page.evaluate(async mode => {
    const w = window as any, before = document.querySelector<HTMLElement>('#before')!;
    const after = document.querySelector<HTMLElement>('#after')!;
    w.create('native-return', { native: true }); before.focus();
    const hooks: string[] = [], changes: string[] = [];
    const handle = w.openLayer('native-return', { trigger: before,
      returnFocus: mode === 'false' ? false : mode === 'custom' ? () => after : undefined,
      onReturnFocus: (event: Event) => { hooks.push(document.activeElement?.id || 'body'); if (mode === 'cancel') event.preventDefault(); },
    });
    const observed = (event: FocusEvent) => { const id = (event.target as HTMLElement).id; if (id) changes.push(id); };
    document.addEventListener('focusin', observed);
    if (mode === 'destroy') handle.destroy(); else await w.system.close(handle, mode === 'outside' ? 'outside' : 'programmatic');
    document.removeEventListener('focusin', observed);
    return { hooks, changes, focus: document.activeElement?.id || 'body', lock: document.body.style.overflow,
      inert: (document.querySelector('#background') as HTMLElement).inert };
  }, mode)).toEqual({ hooks: ['outside', 'false', 'destroy'].includes(mode) ? [] : ['native-return-first'],
    changes: mode === 'normal' ? ['before'] : mode === 'custom' ? ['after'] : [],
    focus: mode === 'normal' ? 'before' : mode === 'custom' ? 'after' : 'native-return-first', lock: '', inert: false });
});
}

test('native close with a removed last-focus node still returns only to the connected explicit trigger', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any, previous = document.querySelector<HTMLElement>('#before')!;
    previous.focus(); w.create('removed-focus', { native: true });
    const handle = w.openLayer('removed-focus', { trigger: document.querySelector('#after') });
    previous.remove();
    const changes: string[] = [];
    const observe = (event: FocusEvent) => changes.push((event.target as HTMLElement).id);
    document.addEventListener('focusin', observe);
    const closed = await w.system.close(handle);
    document.removeEventListener('focusin', observe);
    return { closed, changes, focus: document.activeElement?.id, lock: document.body.style.overflow,
      inert: (document.querySelector('#background') as HTMLElement).inert };
  })).toEqual({ closed: true, changes: ['after'], focus: 'after', lock: '', inert: false });
});

test('external native close destroys its lease once and leaves its companion actionable outside the closed host', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any, notification = document.createElement('aside');
    notification.id = 'native-close-notice'; notification.innerHTML = '<button id="native-close-action">Action</button>';
    notification.querySelector('button')!.addEventListener('click', () => w.events.push('action'));
    document.body.append(notification); w.noticeLease = w.system.registerCompanion(notification);
    w.create('external-native', { native: true }); w.openLayer('external-native', {
      onDestroy: (reason: string) => w.events.push(reason), onClose: () => w.events.push('user-close'),
    });
  });
  await page.locator('#native-close-action').focus();
  await page.locator('#external-native').evaluate((node: HTMLDialogElement) => node.close());
  await expect(page.locator('#external-native')).toHaveAttribute('data-lq-layer-state', 'destroyed');
  await expect(page.locator('body > #native-close-notice')).toBeVisible();
  await page.locator('#native-close-action').click();
  expect(await page.evaluate(() => { const w = window as any; w.handles['external-native'].destroy(); w.noticeLease.destroy();
    return { events: w.events, top: w.system.top(), lock: document.body.style.overflow, inert: (document.querySelector('#background') as HTMLElement).inert }; }))
    .toEqual({ events: ['native-closed', 'action'], top: null, lock: '', inert: false });
});

for (const native of [false, true]) for (const stable of [false, true]) {
test(`nonzero scrollbar compensation survives nested ${native ? 'native' : 'fallback'} modals with stable gutter ${stable}`, async ({ page }) => {
  expect(await page.evaluate(async ({ native, stable }) => {
    const w = window as any;
    const root = document.documentElement;
    Object.defineProperty(root, 'clientWidth', { configurable: true, get: () => innerWidth - 17 });
    root.style.scrollbarGutter = stable ? 'stable' : 'auto';
    root.style.setProperty('--lq-scrollbar-w', '9px', 'important');
    document.body.style.setProperty('padding-right', '11px', 'important');
    document.body.style.setProperty('overflow', 'auto', 'important');
    const before = { body: document.body.style.cssText, root: root.style.cssText };
    w.create('comp-parent', { native }); w.create('comp-child', { native });
    const parent = w.openLayer('comp-parent');
    const child = w.openLayer('comp-child', { parentLayer: parent });
    const during = { padding: document.body.style.paddingRight, gap: root.style.getPropertyValue('--lq-scrollbar-w'),
      lock: document.body.style.overflow };
    await w.system.close(child);
    const nested = { padding: document.body.style.paddingRight, gap: root.style.getPropertyValue('--lq-scrollbar-w'),
      lock: document.body.style.overflow };
    parent.destroy();
    return { during, nested, restored: before.body === document.body.style.cssText && before.root === root.style.cssText,
      bodyPriority: document.body.style.getPropertyPriority('padding-right'), gapPriority: root.style.getPropertyPriority('--lq-scrollbar-w') };
  }, { native, stable })).toEqual({ during: { padding: stable ? '11px' : '28px', gap: '17px', lock: 'hidden' },
    nested: { padding: stable ? '11px' : '28px', gap: '17px', lock: 'hidden' }, restored: true, bodyPriority: 'important', gapPriority: 'important' });
});
}

test('async checking shares its promise; veto and rejected checks retain ownership; closeAll stops', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any; let answer: (value: boolean) => void;
    const a = w.openLayer('a', { trigger: document.querySelector('#before') });
    await Promise.allSettled(a.root.getAnimations({ subtree: true }).map((animation: Animation) => animation.finished));
    const b = w.openLayer('b', { trigger: document.querySelector('#before'), beforeClose: () => new Promise(resolve => { answer = resolve; }), onCloseRequested: () => w.events.push('requested') });
    const first = w.system.close(b); const second = w.system.close(b, 'escape');
    const checking = b.state; answer!(false);
    const veto = await first;
    b.update({ beforeClose: () => Promise.reject(new Error('unsaved')) });
    const rejected = await w.system.close(b);
    b.update({ beforeClose: () => false });
    const all = await w.system.closeAll();
    return { same: first === second, checking, veto, rejected, all, top: w.system.top() === b, state: b.state, aState: a.state, events: w.events, locked: document.body.style.overflow };
  })).toMatchObject({ same: true, checking: 'checking', veto: false, rejected: false, all: false, top: true, state: 'open', aState: 'open', events: [], locked: 'hidden' });
});

test('reopen supersedes checking/closing without late hide, callback or focus theft', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const a = w.openLayer('a', { onCloseRequested: () => w.events.push('requested'), onClose: () => w.events.push('closed') });
    await new Promise(resolve => setTimeout(resolve, 100));
    const pending = w.system.close(a);
    await new Promise(resolve => setTimeout(resolve, 10));
    const same = w.openLayer('a') === a;
    const closed = await pending;
    await new Promise(resolve => setTimeout(resolve, 150));
    return { same, closed, state: a.state, hidden: a.root.hidden, events: w.events, locked: document.body.style.overflow };
  })).toEqual({ same: true, closed: false, state: 'open', hidden: false, events: ['requested'], locked: 'hidden' });
});

test('owner removal force-destroys a pending parent and portaled descendants without user close', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const owner = document.createElement('div'); document.body.append(owner);
    const a = w.openLayer('a', { owner, beforeClose: () => new Promise(() => {}), onClose: () => w.events.push('closed'), onDestroy: (reason: string) => w.events.push(`a:${reason}`) });
    const b = w.openLayer('b', { parentLayer: a, onDestroy: (reason: string) => w.events.push(`b:${reason}`) });
    const pending = w.system.close(a); owner.remove();
    const result = await pending;
    await new Promise(resolve => setTimeout(resolve, 0));
    return { result, states: [a.state, b.state], events: w.events, top: w.system.top(), lock: document.body.style.overflow };
  })).toEqual({ result: false, states: ['destroyed', 'destroyed'], events: ['b:parent-destroyed', 'a:parent-destroyed'], top: null, lock: '' });
});

test('native dialog provides a legal child portal, parent remains interactive after child exit', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any; w.create('native', { native: true });
    const parent = w.openLayer('native', { trigger: document.querySelector('#before') });
    const root = w.create('child', { attach: false }); root.className = 'floating';
    w.childHost = w.system.getPortalHost({ trigger: document.querySelector('#native-first'), parentLayer: parent });
    w.handles.child = w.system.open(root, { type: 'popover', parentLayer: parent, trigger: document.querySelector('#native-first') });
  });
  expect(await page.evaluate(() => ({ modal: document.querySelector('#native')!.matches(':modal'), host: (window as any).childHost.id, parent: document.querySelector('#child')!.parentElement!.id }))).toEqual({ modal: true, host: 'native', parent: 'native' });
  await page.locator('#child-last').click();
  await page.keyboard.press('Escape');
  await expect(page.locator('#child')).toHaveCount(0);
  await expect(page.locator('#native-first')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#native')).toBeHidden();
  await expect(page.locator('#before')).toBeFocused();
});

test('outside click requires both start and end outside; preserves clicked focus for popover', async ({ page }) => {
  await page.evaluate(() => { (window as any).openLayer('a', { type: 'popover', trigger: document.querySelector('#before') }); document.querySelector('#a')!.className = 'floating'; });
  await page.locator('#a-first').dispatchEvent('pointerdown', { button: 0 });
  await page.locator('#after').dispatchEvent('click', { button: 0 });
  await expect(page.locator('#a')).toBeVisible();
  await page.locator('#after').click();
  await expect(page.locator('#a')).toBeHidden();
  await expect(page.locator('#after')).toBeFocused();
  expect(await page.evaluate(() => document.body.style.overflow)).toBe('');
});

test('focus handoff is cancellable; opening a new layer during exit prevents stale return', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const a = w.openLayer('a', { onReturnFocus: (event: Event) => { event.preventDefault(); w.events.push('cancelled'); }, onClose: () => document.querySelector<HTMLElement>('#after')!.focus() });
    await w.system.close(a);
    const handed = document.activeElement!.id;
    const b = w.openLayer('b'); await new Promise(resolve => setTimeout(resolve, 100));
    const pending = w.system.close(b);
    await new Promise(resolve => setTimeout(resolve, 10));
    w.openLayer('c', { trigger: document.querySelector('#before') });
    await pending;
    return { handed, events: w.events, focus: document.activeElement!.id };
  })).toEqual({ handed: 'after', events: ['cancelled'], focus: 'c-first' });
});

test('close timeout bounds a never-ending surface; destroy during pending check is immediate', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const a = w.openLayer('a', { closeTimeoutMs: 30 });
    const surface = a.root.firstElementChild;
    surface.style.transitionDuration = '60s';
    surface.getAnimations = () => [{ effect: { getComputedTiming: () => ({ endTime: 60000 }) }, playState: 'running', finished: new Promise(() => {}) }];
    const start = performance.now(); const closed = await w.system.close(a); const elapsed = performance.now() - start;
    const b = w.openLayer('b', { beforeClose: () => new Promise(() => {}), onClose: () => w.events.push('closed'), onDestroy: () => w.events.push('destroyed') });
    const pending = w.system.close(b); b.destroy(); b.destroy();
    return { closed, bounded: elapsed < 1000, pending: await pending, state: b.state, lock: document.body.style.overflow, events: w.events };
  })).toEqual({ closed: true, bounded: true, pending: false, state: 'destroyed', lock: '', events: ['destroyed'] });
});

test('inert fallback restores tabindex/aria, blocks background actions and handles added nodes', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any; delete (HTMLElement.prototype as any).inert;
    document.querySelector('#before')!.setAttribute('tabindex', '4');
    let clicked = 0; document.querySelector('#after')!.addEventListener('click', () => clicked++);
    const a = w.openLayer('a', { closeOnOutside: false });
    const added = document.createElement('button'); added.textContent = 'late'; document.querySelector('#background')!.append(added);
    await new Promise(resolve => setTimeout(resolve, 0));
    document.querySelector<HTMLElement>('#after')!.click();
    document.querySelector<HTMLElement>('#after')!.focus();
    const during = { tabindex: document.querySelector('#before')!.getAttribute('tabindex'), late: added.tabIndex, clicked, focus: document.activeElement!.id };
    await w.system.close(a);
    return { during, tabindex: document.querySelector('#before')!.getAttribute('tabindex'), late: added.getAttribute('tabindex'), aria: document.querySelector('#background')!.getAttribute('aria-hidden') };
  })).toEqual({ during: { tabindex: '-1', late: -1, clicked: 0, focus: 'a-first' }, tabindex: '4', late: null, aria: 'false' });
});

test('system destroy removes listeners and allows a fresh singleton without duplicate Escape handlers', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any; let destroyed = 0;
    w.openLayer('a', { onDestroy: () => destroyed++ });
    const old = w.system; old.destroy(); old.destroy();
    const fresh = w.getLayerSystem(document); w.system = fresh;
    w.openLayer('b', { onClose: () => w.events.push('closed') });
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    await new Promise(resolve => setTimeout(resolve, 150));
    return { fresh: fresh !== old, destroyed, top: fresh.top(), events: w.events, locked: document.body.style.overflow };
  })).toEqual({ fresh: true, destroyed: 1, top: null, events: ['closed'], locked: '' });
});

test('closing top retains stack/lock and shares repeated Escape until presence finishes', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any;
    w.openLayer('a', { trigger: document.querySelector('#before'), onClose: () => w.events.push('a') });
    w.openLayer('b', { trigger: document.querySelector('#a-first'), onClose: () => w.events.push('b') });
  });
  await expect(page.locator('#b')).toHaveAttribute('data-lq-layer-state', 'open');
  expect(await page.evaluate(async () => {
    const w = window as any; const handle = w.handles.b;
    const first = w.system.close(handle, 'escape');
    await Promise.resolve(); await Promise.resolve();
    const state = handle.state; const second = w.system.close(handle, 'escape');
    document.activeElement!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    const owned = w.system.top() === handle && document.body.style.overflow === 'hidden';
    const complete = await first;
    return { state, same: first === second, owned, complete, events: w.events, parentVisible: !w.handles.a.root.hidden };
  })).toEqual({ state: 'closing', same: true, owned: true, complete: true, events: ['b'], parentVisible: true });
});

test('iOS capability branch restores exact inline body styles and scroll after the last modal', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    Object.defineProperty(navigator, 'platform', { configurable: true, value: 'iPhone' });
    Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'iPhone Safari' });
    document.body.style.overflow = 'auto'; document.body.style.paddingRight = '11px'; document.body.style.width = '91%';
    window.scrollTo(0, 450);
    const before = { y: scrollY, css: document.body.style.cssText };
    const a = w.openLayer('a', { trigger: document.querySelector('#before') });
    const b = w.openLayer('b', { trigger: document.querySelector('#a-first') });
    const locked = { position: document.body.style.position, top: document.body.style.top };
    await w.system.close(b); const stillLocked = document.body.style.position;
    await w.system.close(a);
    return { before, locked, stillLocked, after: { y: scrollY, css: document.body.style.cssText } };
  })).toMatchObject({ before: { y: 450 }, locked: { position: 'fixed', top: '-450px' }, stillLocked: 'fixed', after: { y: 450, css: 'overflow: auto; padding-right: 11px; width: 91%;' } });
});

test('destroy during the beforeClose callback creates no late timeout or user close callback', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any; const live = new Set<number>();
    const originalSet = window.setTimeout.bind(window), originalClear = window.clearTimeout.bind(window);
    window.setTimeout = ((callback: TimerHandler, ms?: number) => { const id = originalSet(callback, ms); live.add(id); return id; }) as typeof window.setTimeout;
    window.clearTimeout = (id?: number) => { live.delete(id!); originalClear(id); };
    const a = w.openLayer('a', { beforeClose: (_reason: string, handle: any) => { handle.destroy(); return new Promise(() => {}); }, onClose: () => w.events.push('closed') });
    const result = await w.system.close(a);
    const outstanding = live.size;
    window.setTimeout = originalSet; window.clearTimeout = originalClear;
    return { result, outstanding, state: a.state, top: w.system.top(), events: w.events };
  })).toEqual({ result: false, outstanding: 0, state: 'destroyed', top: null, events: [] });
});

test('dialog capability absence falls back to a visible focusable modal and check timeout vetoes', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any; (HTMLDialogElement.prototype as any).showModal = undefined;
    w.create('fallback', { native: true });
    w.openLayer('fallback', { checkTimeoutMs: 20, beforeClose: () => new Promise(() => {}) });
  });
  await expect(page.locator('#fallback-first')).toBeFocused();
  await expect(page.locator('#fallback')).toBeVisible();
  expect(await page.evaluate(async () => {
    const w = window as any; const result = await w.system.close(w.handles.fallback);
    const state = w.handles.fallback.state; w.handles.fallback.destroy();
    return { result, state, open: document.querySelector('#fallback')!.hasAttribute('open'), lock: document.body.style.overflow };
  })).toEqual({ result: false, state: 'open', open: false, lock: '' });
});

test('root removal and system destroy detach all coordinator listeners and its bounded observer', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const types = new Set(['keydown', 'focusin', 'pointerdown', 'pointercancel', 'click', 'scroll', 'resize']);
    const activeListeners = new Set<EventListenerOrEventListenerObject>();
    const originals = [document, window].map(target => ({ target, add: target.addEventListener.bind(target), remove: target.removeEventListener.bind(target) }));
    for (const entry of originals) {
      entry.target.addEventListener = ((name: string, listener: EventListenerOrEventListenerObject, options?: any) => { if (types.has(name)) activeListeners.add(listener); entry.add(name, listener, options); }) as any;
      entry.target.removeEventListener = ((name: string, listener: EventListenerOrEventListenerObject, options?: any) => { activeListeners.delete(listener); entry.remove(name, listener, options); }) as any;
    }
    const NativeObserver = window.MutationObserver; let observerActive = 0;
    window.MutationObserver = class extends NativeObserver {
      observe(...args: Parameters<MutationObserver['observe']>) { observerActive++; super.observe(...args); }
      disconnect() { observerActive--; super.disconnect(); }
    };
    const a = w.openLayer('a'); a.root.remove();
    await new Promise(resolve => setTimeout(resolve, 0));
    const afterRemoval = { listeners: activeListeners.size, observerActive, state: a.state };
    w.system.destroy();
    for (const entry of originals) { entry.target.addEventListener = entry.add as any; entry.target.removeEventListener = entry.remove as any; }
    window.MutationObserver = NativeObserver;
    return afterRemoval;
  })).toEqual({ listeners: 0, observerActive: 0, state: 'destroyed' });
});

test('legacy createPopoverSystem retains facade, parent replacement and independent prefix', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const { createPopoverSystem } = await import('/static/js/ui_popover.js');
    const { createPopover, popoverManager } = createPopoverSystem({ prefix: 'twb' });
    const panel = document.createElement('div'), nested = document.createElement('button'); panel.append(nested);
    const parent = createPopover({ panel, anchor: document.querySelector('#before'), preserveOnResize: true }); parent.open();
    const child = createPopover({ panel: document.createElement('div'), anchor: nested, parent: 'anchor' }); child.open();
    const replacement = createPopover({ panel: document.createElement('div'), anchor: nested, parent: 'anchor' }); replacement.open();
    const result = { count: popoverManager.stack.length, parent: replacement.parent === parent, childClosed: !child.isOpen, prefix: panel.classList.contains('twb-popover'), methods: ['open', 'close', 'toggle', 'reposition', 'destroy'].every(key => typeof (parent as any)[key] === 'function') };
    popoverManager.closeAll(); return result;
  })).toEqual({ count: 2, parent: true, childClosed: true, prefix: true, methods: true });
});

test('completion cleanup cannot change a successful close result or emit close twice', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const a = w.openLayer('a', { onClose: (_reason: string, handle: any) => { w.events.push('closed'); handle.destroy(); }, onDestroy: () => w.events.push('destroyed') });
    const result = await w.system.close(a);
    return { result, state: a.state, events: w.events, top: w.system.top() };
  })).toEqual({ result: true, state: 'destroyed', events: ['closed', 'destroyed'], top: null });
});

test('handle update uses latest trigger/callback and modality; preserves explicit no parent', async ({ page }) => {
  expect(await page.evaluate(async () => {
    const w = window as any;
    const a = w.openLayer('a', { trigger: document.querySelector('#before'), onClose: () => w.events.push('old') });
    a.update({ modality: 'non-modal', type: 'menu', trigger: document.querySelector('#after'), onClose: () => w.events.push('new') });
    const updated = { lock: document.body.style.overflow, role: a.root.firstElementChild.getAttribute('role'), aria: a.root.firstElementChild.getAttribute('aria-modal') };
    const b = w.openLayer('b', { parentLayer: null });
    const detached = b.parentLayer === null;
    b.destroy(); await w.system.close(a);
    return { updated, detached, events: w.events, focus: document.activeElement!.id };
  })).toEqual({ updated: { lock: '', role: 'menu', aria: null }, detached: true, events: ['new'], focus: 'after' });
});

test('cancellable initial focus supports React autofocus while the modal trap stays authoritative', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as any;
    w.openLayer('a', { onInitialFocus: (event: Event, handle: any) => { w.events.push('initial'); event.preventDefault(); handle.root.querySelector('#a-last').focus(); } });
  });
  await expect(page.locator('#a-last')).toBeFocused();
  await page.locator('#after').evaluate(node => (node as HTMLElement).focus());
  // Native inert may reject focus before focusin; force the fallback path to verify its hook does not re-run.
  await page.evaluate(() => {
    (document.querySelector('#background') as HTMLElement).inert = false;
    document.querySelector<HTMLElement>('#after')!.focus();
  });
  await expect(page.locator('#a-first')).toBeFocused();
  expect(await page.evaluate(() => (window as any).events)).toEqual(['initial']);
});

test('destroy callbacks cannot resurrect a child under a dying owner or a disposed system', async ({ page }) => {
  expect(await page.evaluate(() => {
    const w = window as any;
    const a = w.openLayer('a');
    w.openLayer('b', { parentLayer: a, onDestroy: () => w.openLayer('orphan', { parentLayer: a }), onError: () => w.events.push('parent-rejected') });
    a.destroy();
    w.openLayer('c', { onDestroy: () => w.openLayer('late'), onError: () => w.events.push('system-rejected') });
    w.system.destroy();
    return { events: w.events, lock: document.body.style.overflow, top: w.system.top() };
  })).toEqual({ events: ['parent-rejected', 'system-rejected'], lock: '', top: null });
});
});
