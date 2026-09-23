import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const theme = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock('../../../static/js/lq/theme.js', () => ({ initTheme: () => theme }));
// @ts-expect-error SSR native module is intentionally shared without generated types.
import { initUserUIPreferences } from '../../../static/js/user_ui_preferences.js';

class NodeDouble extends EventTarget {
  dataset: Record<string, string> = {};
  attrs = new Map<string, string>();
  classes = new Set<string>();
  classList = { add: (key: string) => this.classes.add(key), remove: (key: string) => this.classes.delete(key),
    toggle: (key: string, value: boolean) => value ? this.classes.add(key) : this.classes.delete(key) };
  tagName = 'INPUT'; type = ''; value = ''; checked = false; disabled = false; hidden = false; textContent = '';
  hasAttribute(key: string) { return this.attrs.has(key); }
  getAttribute(key: string) { return this.attrs.get(key) ?? null; }
  setAttribute(key: string, value: string) { this.attrs.set(key, value); }
  removeAttribute(key: string) { this.attrs.delete(key); }
  querySelector(_query: string) { return null; }
}
const owned: Array<{ dispose(): void }> = [];
const initial = { palette_key: 'indigo', appearance: 'auto', glass: 'tinted', version: 7, context_token: 'person-a' };
const reply = (preferences = initial, status = 200) => ({ ok: status < 400, status, json: async () => ({ preferences }) });
function fixture({ selects = true, primary = true, available = true } = {}) {
  const legacy = ['palette_key', 'appearance', 'glass'].map(field => {
    const node = new NodeDouble(); node.tagName = 'SELECT'; node.dataset.uiPreferenceSelect = field; return node;
  });
  const radios = ['auto', 'light', 'dark'].map(value => {
    const node = new NodeDouble(); node.type = 'radio'; node.value = value; node.dataset.uiPreferenceInput = 'appearance'; return node;
  });
  const glass = new NodeDouble(); glass.type = 'checkbox'; glass.dataset.uiPreferenceInput = 'glass';
  const chips = ['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'].map(value => {
    const node = new NodeDouble(); node.tagName = 'BUTTON'; node.type = 'button';
    node.dataset = { uiPreferenceChoice: 'palette_key', uiPreferenceValue: value }; return node;
  });
  const mainStatus = new NodeDouble(); mainStatus.setAttribute('data-ui-preference-primary-status', ''); mainStatus.setAttribute('aria-live', 'polite');
  const oldStatus = new NodeDouble(); oldStatus.setAttribute('aria-live', 'polite');
  const summary = new NodeDouble();
  const body = new NodeDouble(); body.setAttribute('data-ui-palette', initial.palette_key);
  body.dataset = { uiPalette: initial.palette_key, appearancePreference: initial.appearance, glassPreference: initial.glass,
    uiPaletteVersion: '7', uiPaletteContext: initial.context_token, uiPaletteAvailable: String(available) };
  const lists: Record<string, NodeDouble[]> = {
    '[data-ui-palette-select], [data-ui-preference-select]': selects ? legacy : [],
    '[data-ui-preference-input]': [...radios, glass], '[data-ui-preference-choice]': chips,
    '[data-ui-palette-status]': primary ? [oldStatus, mainStatus] : [oldStatus],
    '[data-ui-preferences-summary-status]': [summary], '[data-ui-preferences-details]': [],
  };
  const doc = Object.assign(new EventTarget(), { body: Object.assign(body, { querySelectorAll: (query: string) => lists[query] || [] }),
    defaultView: { CustomEvent, requestAnimationFrame: vi.fn(() => 1), cancelAnimationFrame: vi.fn() } });
  const mount = () => { const owner = initUserUIPreferences(doc); if (owner) owned.push(owner); return owner; };
  return { doc, body, radios, glass, chips, legacy, mainStatus, oldStatus, summary, mount };
}
function change(node: NodeDouble, value?: string | boolean) {
  if (typeof value === 'boolean') node.checked = value;
  else if (value !== undefined) node.value = value;
  node.dispatchEvent(new Event('change'));
}
const tick = () => vi.advanceTimersByTimeAsync(240);
beforeEach(() => { vi.useFakeTimers(); theme.refresh.mockClear(); });
afterEach(() => { owned.splice(0).forEach(owner => owner.dispose()); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('LQ Profile appearance adapters share the existing preferences owner', () => {
  it('initializes SSR radio, switch and six chips without requiring hidden selects or requests', () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
    const env = fixture({ selects: false }); const owner = env.mount();
    expect(owner.snapshot().desired).toEqual({ palette_key: 'indigo', appearance: 'auto', glass: 'tinted', backdrop: 'scene', backdrop_color: '#ffffff' });
    expect(env.radios.map(node => node.checked)).toEqual([true, false, false]);
    expect(env.glass.checked).toBe(true);
    expect(env.chips.filter(node => node.getAttribute('aria-pressed') === 'true')).toEqual([env.chips[1]]);
    expect(env.mount()).toBeNull(); expect(fetch).not.toHaveBeenCalled();
  });
  it('coalesces all three new controls into one 240ms CAS and synchronizes every old select', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ ...initial, appearance: 'dark', glass: 'off', palette_key: 'rose', version: 8 })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); env.mount();
    change(env.radios[2], true); change(env.glass, false); env.chips[5].dispatchEvent(new Event('click'));
    expect(env.legacy.map(node => node.value)).toEqual(['rose', 'dark', 'off']);
    expect(theme.refresh).toHaveBeenLastCalledWith({ palette_key: 'rose', appearance: 'dark', glass: 'off' });
    await vi.advanceTimersByTimeAsync(239); expect(fetch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1); expect(fetch).toHaveBeenCalledTimes(1);
    const request = fetch.mock.calls[0][1];
    expect(JSON.parse(request.body)).toEqual({ version: 7, appearance: 'dark', glass: 'off', palette_key: 'rose' });
    expect(request.headers['X-UI-Preferences-Context']).toBe('person-a');
    expect(request.credentials).toBe('same-origin'); expect(request.cache).toBe('no-store');
    expect(env.body.dataset.uiPaletteVersion).toBe('8');
  });
  it('reflects old navbar choices into native controls, ignores unchecked radio events and disabled clicks', () => {
    const env = fixture(); const owner = env.mount();
    change(env.legacy[1], 'light'); change(env.legacy[2], 'off'); change(env.legacy[0], 'mint');
    expect(env.radios.map(node => node.checked)).toEqual([false, true, false]); expect(env.glass.checked).toBe(false);
    expect(env.chips[3].getAttribute('aria-pressed')).toBe('true');
    change(env.radios[0], false); env.chips[0].disabled = true; env.chips[0].dispatchEvent(new Event('click'));
    expect(owner.snapshot().intents).toEqual({ appearance: 1, glass: 1, palette_key: 1, backdrop: 0, backdrop_color: 0 });
  });
  it('keeps the latest radio intent while an older CAS reply is in flight', async () => {
    let release: (value: unknown) => void = () => {};
    const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => { release = resolve; }))
      .mockResolvedValueOnce(reply({ ...initial, appearance: 'light', version: 9 })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); env.mount(); change(env.radios[2], true); await tick();
    change(env.radios[1], true); release(reply({ ...initial, appearance: 'dark', version: 8 }));
    await vi.advanceTimersByTimeAsync(0);
    expect(env.radios.map(node => node.checked)).toEqual([false, true, false]); expect(env.legacy[1].value).toBe('light');
    await tick(); expect(fetch).toHaveBeenCalledTimes(2); expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ version: 8, appearance: 'light' });
  });
  it('limits a 409 to the affected field and confirms it only by a new choice', async () => {
    const remote = { ...initial, palette_key: 'sky', version: 8 };
    const fetch = vi.fn().mockResolvedValueOnce(reply(initial, 409)).mockResolvedValueOnce(reply(remote))
      .mockResolvedValueOnce(reply({ ...remote, appearance: 'dark', version: 9 }))
      .mockResolvedValueOnce(reply({ ...remote, appearance: 'dark', palette_key: 'rose', version: 10 })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); const owner = env.mount(); env.chips[5].dispatchEvent(new Event('click')); await tick();
    expect(owner.snapshot().conflicts).toEqual({ palette_key: 'sky' });
    expect(env.chips.every(node => node.getAttribute('aria-invalid') === 'true')).toBe(true);
    expect(env.radios[0].getAttribute('aria-invalid')).toBe('false');
    change(env.radios[2], true); await tick(); expect(owner.snapshot().conflicts).toEqual({ palette_key: 'sky' });
    expect(env.mainStatus.textContent).toContain('服务器：晴空');
    env.chips[5].dispatchEvent(new Event('click')); await tick();
    expect(owner.snapshot().conflicts).toEqual({}); expect(fetch).toHaveBeenCalledTimes(4);
  });
  it('retains failure text and Enter retries with GET, preventing accidental form submission', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(reply(initial))
      .mockResolvedValueOnce(reply({ ...initial, glass: 'off', version: 8 })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); env.mount(); change(env.glass, false); await tick(); await vi.advanceTimersByTimeAsync(15000);
    expect(env.mainStatus.textContent).toContain('未同步'); expect(env.mainStatus.classes.has('sr-only')).toBe(false);
    const event = Object.assign(new Event('keydown', { cancelable: true }), { key: 'Enter' }); env.glass.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true); await tick();
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(['PATCH', 'GET', 'PATCH']);
    expect(env.glass.checked).toBe(false); expect(env.mainStatus.textContent).toBe('界面偏好已保存');
  });
  it('does not duplicate a successful write when its response was lost', async () => {
    const fetch = vi.fn().mockRejectedValueOnce(new Error('lost response'))
      .mockResolvedValueOnce(reply({ ...initial, palette_key: 'rose', version: 8 })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); env.mount(); env.chips[5].dispatchEvent(new Event('click')); await tick();
    env.chips[5].dispatchEvent(new Event('click')); await tick();
    expect(fetch.mock.calls.map(call => call[1].method)).toEqual(['PATCH', 'GET']); expect(env.mainStatus.textContent).toBe('界面偏好已保存');
  });
  it('uses one persistent live region and restores only the live attributes it owns on disposal', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(reply({ ...initial, glass: 'off', version: 8 })));
    const env = fixture(); const owner = env.mount();
    expect(env.mainStatus.getAttribute('aria-live')).toBe('polite'); expect(env.oldStatus.getAttribute('aria-live')).toBe('off'); expect(env.summary.getAttribute('aria-live')).toBe('off');
    change(env.glass, false); await tick(); await vi.advanceTimersByTimeAsync(2000);
    expect(env.mainStatus.classes.has('sr-only')).toBe(false); expect(env.oldStatus.classes.has('sr-only')).toBe(true);
    owner.dispose(); expect(env.oldStatus.getAttribute('aria-live')).toBe('polite'); expect(env.summary.hasAttribute('aria-live')).toBe(false);
  });
  it('disables all input types after identity mismatch and never sends a later change', async () => {
    const fetch = vi.fn().mockResolvedValue(reply({ ...initial, context_token: 'person-b' })); vi.stubGlobal('fetch', fetch);
    const env = fixture(); env.mount(); change(env.glass, false); await tick();
    expect([...env.legacy, ...env.radios, env.glass, ...env.chips].every(node => node.disabled)).toBe(true);
    expect(env.mainStatus.textContent).toContain('登录账号已变化'); env.chips[5].dispatchEvent(new Event('click')); await tick(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('preserves unavailable SSR recovery and does not intercept Enter in a clean state', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply(initial)).mockResolvedValueOnce(reply({ ...initial, glass: 'off', version: 8 })); vi.stubGlobal('fetch', fetch);
    const env = fixture({ available: false }); env.mount();
    const event = Object.assign(new Event('keydown', { cancelable: true }), { key: 'Enter' }); env.radios[0].dispatchEvent(event); expect(event.defaultPrevented).toBe(false);
    change(env.glass, false); await tick(); expect(fetch.mock.calls.map(call => call[1].method)).toEqual(['GET', 'PATCH']);
  });
  it('disposes late replies and supports 20 clean mounts without duplicated handlers', async () => {
    const env = fixture();
    for (let i = 0; i < 20; i++) { const owner = env.mount(); expect(env.mount()).toBeNull(); owner.dispose(); owner.dispose(); }
    let release: (value: unknown) => void = () => {};
    vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { release = resolve; })));
    const owner = env.mount(); env.chips[5].dispatchEvent(new Event('click')); expect(owner.snapshot().intents.palette_key).toBe(1); await tick();
    owner.dispose(); const text = env.mainStatus.textContent; release(reply({ ...initial, palette_key: 'rose', version: 8 })); await vi.advanceTimersByTimeAsync(0);
    expect(env.body.dataset.uiPaletteVersion).toBe('7'); expect(env.mainStatus.textContent).toBe(text); expect(vi.getTimerCount()).toBe(0);
    env.chips[0].dispatchEvent(new Event('click')); expect(owner.snapshot().intents.palette_key).toBe(1);
  });
});
