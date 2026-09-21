import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { afterEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Native SSR module has no generated declarations.
import { applyTheme, detectCapabilities, initTheme } from '../../../static/js/lq/theme.js';
// @ts-expect-error Native SSR module has no generated declarations.
import { initUserUIPreferences } from '../../../static/js/user_ui_preferences.js';

// Execute the exact inline source used by Jinja, rather than a copy of the
// resolver. The DOM double deliberately omits APIs absent on older browsers.
const bootstrap = readFileSync('templates/partials/lq_theme_core.js', 'utf8');
class ElementDouble extends EventTarget {
  attrs = new Map<string, string>();
  style: Record<string, string> = {};
  ownerDocument: any;
  contentWindow: any;
  dataset: Record<string, string> = {};
  classList = { add: vi.fn(), remove: vi.fn(), toggle: vi.fn() };
  hidden = false;
  textContent = '';
  value = '';
  open = false;
  disabled = false;
  children: ElementDouble[] = [];
  querySelectorAll(_query: string): ElementDouble[] { return []; }
  querySelector(_query: string): ElementDouble | null { return null; }
  contains(node: any): boolean { return this === node || this.children.some(child => child.contains(node)); }
  focus() { this.ownerDocument.activeElement = this; }
  constructor(attrs: Record<string, string> = {}) {
    super();
    for (const [key, value] of Object.entries(attrs)) this.attrs.set(key, value);
  }
  getAttribute(name: string) { return this.attrs.get(name) ?? null; }
  setAttribute(name: string, value: string) { this.attrs.set(name, value); }
  hasAttribute(name: string) { return this.attrs.has(name); }
  removeAttribute(name: string) { this.attrs.delete(name); }
}
const cleanups: Array<() => void> = [];
afterEach(() => { cleanups.splice(0).forEach(fn => fn()); vi.unstubAllGlobals(); vi.useRealTimers(); });

function environment({ attributes = {}, media = {}, full = true, nav = {}, cssThrows = false }:
  { attributes?: Record<string, string>; media?: Record<string, boolean>; full?: boolean; nav?: Record<string, unknown>; cssThrows?: boolean } = {}) {
  const root = new ElementDouble({ 'data-theme': 'lanshare', 'data-ui-palette': 'teal', 'data-appearance-preference': 'auto', 'data-glass-preference': 'tinted', ...attributes });
  const body = new ElementDouble({ 'data-ui-palette': 'teal', 'data-ui-palette-context': 'secret-account-context', 'data-ui-palette-version': '7' });
  Object.assign(body, { dataset: { uiPalette: 'teal' }, querySelectorAll: () => [] });
  const frames: ElementDouble[] = [];
  const doc: any = Object.assign(new EventTarget(), { documentElement: root, body, querySelectorAll: () => frames });
  const win: any = new EventTarget();
  const queries = new Map<string, any>();
  const storage = vi.fn(() => { throw new Error('No account cache access'); });
  Object.defineProperty(win, 'localStorage', { get: storage });
  const fetch = vi.fn();
  Object.assign(win, { document: doc, CustomEvent, navigator: nav, location: { origin: 'https://school.test', href: 'https://school.test/dashboard' }, fetch, postMessage: vi.fn() });
  win.parent = win;
  if (full) {
    win.CSS = { supports: () => { if (cssThrows) throw new Error('unsupported API'); return true; } };
    win.HTMLElement = function () {};
    win.HTMLElement.prototype = { popover: '', inert: false };
    win.HTMLDialogElement = function () {};
    doc.startViewTransition = () => {};
    win.matchMedia = (query: string) => {
      if (!queries.has(query)) queries.set(query, Object.assign(new EventTarget(), { matches: media[query] || false }));
      return queries.get(query);
    };
  }
  doc.defaultView = win;
  root.ownerDocument = doc;
  body.ownerDocument = doc;
  runInNewContext(bootstrap, { window: win, document: doc });
  const change = (query: string, value: boolean) => {
    const matcher = win.matchMedia(query);
    matcher.matches = value;
    matcher.dispatchEvent(new Event('change'));
  };
  const message = (origin: string, source: any, data: any) => win.dispatchEvent(Object.assign(new Event('message'), { origin, source, data }));
  return { root, body, doc, win, frames, queries, storage, fetch, change, message };
}
function install(env: ReturnType<typeof environment>) {
  const controller = initTheme(env.doc);
  cleanups.push(() => controller?.dispose());
  return controller;
}

describe('synchronous theme bootstrap and display-only runtime', () => {
  it('resolves auto before CSS without touching storage, fetch, or identity metadata', () => {
    const env = environment({ media: { '(prefers-color-scheme: dark)': true } });
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
    expect(env.root.getAttribute('data-lq-tier')).toBe('A');
    expect(env.root.getAttribute('data-lq-glass')).toBe('tinted');
    expect(env.root.style.colorScheme).toBe('dark');
    applyTheme({ preferences: { palette_key: 'rose', appearance: 'light', glass: 'off' }, capabilities: detectCapabilities(env.win) }, env.root);
    expect(env.body.getAttribute('data-ui-palette')).toBe('teal');
    expect(env.body.getAttribute('data-ui-palette-context')).toBe('secret-account-context');
    expect(env.body.getAttribute('data-ui-palette-version')).toBe('7');
    expect(env.fetch).not.toHaveBeenCalled();
    expect(env.storage).not.toHaveBeenCalled();
  });
  it('keeps explicit dark with missing matchMedia/CSS, and falls back conservatively', () => {
    const env = environment({ full: false, attributes: { 'data-appearance-preference': 'dark' } });
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
    expect(env.root.getAttribute('data-lq-tier')).toBe('C');
    expect(env.root.getAttribute('data-lq-glass')).toBe('off');
    expect(env.root.getAttribute('data-glass-preference')).toBe('tinted');
    expect(() => install(env)).not.toThrow();
    const throwing = environment({ cssThrows: true });
    expect(throwing.root.getAttribute('data-lq-tier')).toBe('C');
  });
  it('supports WebKit backdrop and independent optional capabilities', () => {
    const env = environment();
    env.win.CSS.supports = (property: string) => property === '-webkit-backdrop-filter';
    const caps = detectCapabilities(env.win);
    expect(caps.tier).toBe('B');
    expect(caps.features.backdrop).toBe(true);
    expect(caps.features.linear).toBe(false);
    expect(caps.features.dialog).toBe(true);
  });
  it('updates all accessibility/media state with no preference controls or writes', () => {
    const env = environment();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    expect(initUserUIPreferences(env.doc)).toBeNull();
    const installation = install(env);
    expect(initTheme(env.doc)).toBe(installation);
    env.change('(prefers-color-scheme: dark)', true);
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
    env.change('(prefers-reduced-transparency: reduce)', true);
    expect(env.root.getAttribute('data-lq-glass')).toBe('off');
    expect(env.root.getAttribute('data-glass-preference')).toBe('tinted');
    env.change('(prefers-reduced-transparency: reduce)', false);
    expect(env.root.getAttribute('data-lq-glass')).toBe('tinted');
    env.change('(prefers-contrast: more)', true);
    expect(env.root.getAttribute('data-lq-contrast')).toBe('more');
    env.change('(forced-colors: active)', true);
    expect(env.root.getAttribute('data-lq-glass')).toBe('off');
    expect(env.root.getAttribute('data-lq-forced-colors')).toBe('true');
    env.change('(prefers-reduced-motion: reduce)', true);
    expect(env.root.getAttribute('data-lq-reduced-motion')).toBe('true');
    expect(fetch).not.toHaveBeenCalled();
    expect(env.fetch).not.toHaveBeenCalled();
    expect(env.storage).not.toHaveBeenCalled();
    installation.dispose();
    env.change('(prefers-color-scheme: dark)', false);
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
  });
  it('does not change explicit appearance or saved glass off on media changes', () => {
    const env = environment({ attributes: { 'data-appearance-preference': 'light', 'data-glass-preference': 'off' } });
    install(env);
    env.change('(prefers-color-scheme: dark)', true);
    env.change('(prefers-reduced-transparency: reduce)', false);
    expect(env.root.getAttribute('data-appearance')).toBe('light');
    expect(env.root.getAttribute('data-lq-glass')).toBe('off');
  });
  it('uses coarse pointer plus a known device limit, and detects data saving/X5', () => {
    const desktop = environment({ nav: { hardwareConcurrency: 2 } });
    expect(desktop.root.getAttribute('data-lq-low-end')).toBe('false');
    const phone = environment({ nav: { hardwareConcurrency: 4, connection: { effectiveType: '2g' } }, media: { '(pointer: coarse)': true } });
    expect(phone.root.getAttribute('data-lq-low-end')).toBe('true');
    expect(phone.root.getAttribute('data-lq-save-data')).toBe('true');
    const unknown = environment({ media: { '(pointer: coarse)': true } });
    expect(unknown.root.getAttribute('data-lq-low-end')).toBe('false');
    const x5 = environment({ nav: { userAgent: 'Chrome/95.0 MQQBrowser/6.2 TBS/046' } });
    expect(x5.root.getAttribute('data-lq-tier')).toBe('C');
  });
  it('does not let an old double dispose remove a fresh installation', () => {
    const env = environment();
    const old = install(env);
    old.dispose();
    const fresh = install(env);
    old.dispose();
    expect(initTheme(env.doc)).toBe(fresh);
    expect(old.registerFrame(new ElementDouble({ src: '/app', 'data-lq-theme-bridge': 'app' }))).toBe(false);
  });
});

describe('explicit app-only iframe theme bridge', () => {
  function frame(env: ReturnType<typeof environment>, attrs: Record<string, string>) {
    const element = new ElementDouble(attrs);
    element.ownerDocument = env.doc;
    element.contentWindow = { postMessage: vi.fn() };
    env.frames.push(element);
    return element;
  }
  it('sends presentation only to registered app frames on init/load/ready and changes', () => {
    const env = environment();
    const app = frame(env, { src: '/manage/assignments?embed=1', 'data-lq-theme-bridge': 'app', hidden: '' });
    const pdf = frame(env, { src: '/preview.pdf' });
    const remote = frame(env, { src: 'https://other.test/app', 'data-lq-theme-bridge': 'app' });
    const srcdoc = frame(env, { src: '/app', srcdoc: '<p>student work</p>', 'data-lq-theme-bridge': 'app' });
    const api = install(env);
    expect(app.contentWindow.postMessage).toHaveBeenCalledWith({ type: 'lq:theme-sync', preferences: { palette_key: 'teal', appearance: 'auto', glass: 'tinted' } }, 'https://school.test');
    expect(pdf.contentWindow.postMessage).not.toHaveBeenCalled();
    expect(remote.contentWindow.postMessage).not.toHaveBeenCalled();
    expect(srcdoc.contentWindow.postMessage).not.toHaveBeenCalled();
    app.contentWindow.postMessage.mockClear();
    env.message('https://evil.test', app.contentWindow, { type: 'lq:theme-ready' });
    env.message('https://school.test', {}, { type: 'lq:theme-ready' });
    expect(app.contentWindow.postMessage).not.toHaveBeenCalled();
    env.message('https://school.test', app.contentWindow, { type: 'lq:theme-ready' });
    expect(app.contentWindow.postMessage).toHaveBeenCalledTimes(1);
    app.dispatchEvent(new Event('load'));
    expect(app.contentWindow.postMessage).toHaveBeenCalledTimes(2);
    api.refresh({ palette_key: 'rose', appearance: 'dark', glass: 'off' });
    expect(app.contentWindow.postMessage.mock.lastCall[0].preferences).toEqual({ palette_key: 'rose', appearance: 'dark', glass: 'off' });
    app.removeAttribute('data-lq-theme-bridge');
    app.contentWindow.postMessage.mockClear();
    api.refresh();
    expect(app.contentWindow.postMessage).not.toHaveBeenCalled();
  });
  it('requires the same-origin parent source and opt-in frame before accepting a theme', () => {
    const env = environment();
    const embedding = frame(env, { src: '/manage/app?embed=1', 'data-lq-theme-bridge': 'app' });
    env.frames.length = 0;
    env.win.parent = { postMessage: vi.fn() };
    env.win.frameElement = embedding;
    install(env);
    expect(env.win.parent.postMessage).toHaveBeenCalledWith({ type: 'lq:theme-ready' }, 'https://school.test');
    const payload = { type: 'lq:theme-sync', preferences: { palette_key: 'rose', appearance: 'dark', glass: 'off', context_token: 'never-copy-this' } };
    env.message('https://other.test', env.win.parent, payload);
    env.message('https://school.test', {}, payload);
    expect(env.root.getAttribute('data-appearance')).toBe('light');
    env.message('https://school.test', env.win.parent, payload);
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
    expect(env.root.getAttribute('data-ui-palette')).toBe('rose');
    expect(env.body.getAttribute('data-ui-palette-context')).toBe('secret-account-context');
    expect(env.root.hasAttribute('data-ui-palette-context')).toBe(false);
    embedding.removeAttribute('data-lq-theme-bridge');
    env.message('https://school.test', env.win.parent, { ...payload, preferences: { ...payload.preferences, appearance: 'light' } });
    expect(env.root.getAttribute('data-appearance')).toBe('dark');
  });
  it('releases detached and deauthorized frame listeners on discovery', () => {
    const env = environment();
    let discover!: () => void;
    env.win.MutationObserver = class { constructor(callback: () => void) { discover = callback; } observe() {} disconnect() {} };
    const app = frame(env, { src: '/app', 'data-lq-theme-bridge': 'app' });
    const remove = vi.spyOn(app, 'removeEventListener');
    const api = install(env);
    env.frames.length = 0;
    discover();
    expect(remove).toHaveBeenCalledWith('load', expect.any(Function));
    app.contentWindow.postMessage.mockClear();
    app.dispatchEvent(new Event('load'));
    api.refresh();
    expect(app.contentWindow.postMessage).not.toHaveBeenCalled();
    env.frames.push(app);
    discover();
    app.removeAttribute('data-lq-theme-bridge');
    discover();
    expect(remove).toHaveBeenCalledTimes(2);
  });
});

describe('preference controls lifecycle and collapsed failure feedback', () => {
  function controls() {
    const env = environment();
    const palette = new ElementDouble();
    const appearance = new ElementDouble(); appearance.dataset.uiPreferenceSelect = 'appearance';
    const glass = new ElementDouble(); glass.dataset.uiPreferenceSelect = 'glass';
    const select = [palette, appearance, glass];
    const details = new ElementDouble();
    const toggle = new ElementDouble();
    const badge = new ElementDouble();
    const status = new ElementDouble();
    details.children = [toggle, ...select];
    details.querySelector = () => toggle;
    [details, toggle, badge, status, ...select].forEach(node => { node.ownerDocument = env.doc; });
    env.body.children = [details, badge, status];
    env.body.dataset = { uiPalette: 'teal', appearancePreference: 'auto', glassPreference: 'tinted', uiPaletteVersion: '0', uiPaletteContext: 'account-a' };
    env.body.querySelectorAll = query => query.includes('select') ? select : query.includes('summary-status') ? [badge] : query.includes('palette-status') ? [status] : query.includes('details') ? [details] : [];
    let frameId = 0;
    env.win.requestAnimationFrame = vi.fn(() => ++frameId);
    env.win.cancelAnimationFrame = vi.fn();
    const controller = initUserUIPreferences(env.doc);
    cleanups.push(() => controller.dispose());
    install(env);
    return { ...env, controller, palette, appearance, glass, details, toggle, badge, status };
  }
  it('removes DOM listeners and timers, supports reinit, and safely repeats disposal', () => {
    const env = controls();
    const removed = vi.spyOn(env.palette, 'removeEventListener');
    env.controller.dispose();
    expect(removed).toHaveBeenCalledWith('change', expect.any(Function));
    expect(removed).toHaveBeenCalledWith('keydown', expect.any(Function));
    expect(env.win.cancelAnimationFrame).toHaveBeenCalledWith(1);
    expect(env.body.dataset.uiPaletteMounted).toBeUndefined();
    const next = initUserUIPreferences(env.doc);
    cleanups.push(() => next.dispose());
    expect(next).not.toBeNull();
    env.controller.dispose();
    expect(env.body.dataset.uiPaletteMounted).toBe('true');
    env.glass.value = 'off';
    env.glass.dispatchEvent(new Event('change'));
    expect(next.snapshot().intents.glass).toBe(1);
    expect(env.controller.snapshot().intents.glass).toBe(0);
  });
  it('retains a visible summary error after collapse and time passing', async () => {
    vi.useFakeTimers();
    const env = controls();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    env.details.open = true;
    env.appearance.value = 'dark';
    env.appearance.dispatchEvent(new Event('change'));
    await vi.advanceTimersByTimeAsync(240);
    env.details.open = false;
    await vi.advanceTimersByTimeAsync(15000);
    expect(env.badge.hidden).toBe(false);
    expect(env.badge.textContent).toBe('未保存');
    expect(env.status.textContent).toContain('未同步');
    expect(env.status.classList.toggle).toHaveBeenLastCalledWith('is-visible', true);
    expect(env.appearance.getAttribute('aria-invalid')).toBe('true');
  });
  it('closes on Escape with focus return and does not steal an outside target focus', () => {
    const env = controls();
    env.details.open = true;
    env.appearance.focus();
    env.details.dispatchEvent(Object.assign(new Event('keydown', { cancelable: true }), { key: 'Escape' }));
    expect(env.details.open).toBe(false);
    expect(env.doc.activeElement).toBe(env.toggle);
    const outside = new ElementDouble(); outside.ownerDocument = env.doc;
    env.details.open = true;
    outside.focus();
    env.doc.dispatchEvent(new Event('click'));
    expect(env.details.open).toBe(false);
    expect(env.doc.activeElement).toBe(outside);
  });
  it('disables every field when the server reports an account change', async () => {
    vi.useFakeTimers();
    const env = controls();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, json: async () => ({ code: 'identity_changed' }) }));
    env.glass.value = 'off'; env.glass.dispatchEvent(new Event('change'));
    await vi.advanceTimersByTimeAsync(240);
    expect([env.palette, env.appearance, env.glass].every(select => select.disabled)).toBe(true);
    expect(env.badge.textContent).toBe('账号已变化');
    expect(env.badge.hidden).toBe(false);
  });
});
