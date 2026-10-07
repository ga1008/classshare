import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const hooks = vi.hoisted(() => ({ effect: undefined as undefined | (() => undefined | (() => void)) }));
vi.mock('react', () => ({ useEffect: (effect: typeof hooks.effect) => { hooks.effect = effect; }, useRef: (current: unknown) => ({ current }) }));
vi.mock('react/jsx-runtime', () => ({ jsx: (type: () => void) => ({ type }) }));
vi.mock('react/jsx-dev-runtime', () => ({ jsxDEV: (type: () => void) => ({ type }) }));
vi.mock('@/lib/mount-react-island', () => ({ mountReactIslandsWhenReady: (options: { render: () => { type: () => void } }) => { options.render().type(); } }));

class AuthoredNode extends EventTarget {
  dataset: Record<string, string> = {};
  isConnected = true;
  hidden = true;
  textContent = '0';
  attributes = new Map<string, string>();
  children = new Map<string, AuthoredNode>();
  ownerDocument: { getElementById: (id: string) => AuthoredNode | null } = { getElementById: () => null };
  classList = { toggle: vi.fn() };
  querySelector(selector: string) { return this.children.get(selector) || null; }
  getAttribute(name: string) { return this.attributes.get(name) || null; }
  setAttribute(name: string, value: string) { this.attributes.set(name, value); }
}

function bellFixture(portalled = false) {
  const shell = new AuthoredNode(), panel = new AuthoredNode(), trigger = new AuthoredNode();
  const bell = new AuthoredNode(), count = new AuthoredNode(), caption = new AuthoredNode();
  panel.children.set('[data-message-center-bell]', bell);
  panel.children.set('[data-message-center-bell-count]', count);
  panel.children.set('[data-message-center-bell-caption]', caption);
  if (!portalled) for (const [selector, node] of panel.children) shell.children.set(selector, node);
  shell.children.set('[data-lq-nav-trigger][aria-controls]', trigger);
  trigger.attributes.set('aria-controls', 'personal-menu');
  shell.ownerDocument = { getElementById: id => id === 'personal-menu' ? panel : null };
  return { shell, panel, trigger, bell, count, caption };
}

async function mount(shells: AuthoredNode[], fetcher: typeof fetch) {
  const previousRefresh = vi.fn();
  const win = Object.assign(new EventTarget(), { setInterval: vi.fn(() => 1), clearInterval: vi.fn(), setTimeout: vi.fn(() => 2), clearTimeout: vi.fn(), refreshMessageCenterBell: previousRefresh as unknown });
  const doc = Object.assign(new EventTarget(), { querySelectorAll: () => shells, hidden: false });
  vi.stubGlobal('window', win); vi.stubGlobal('document', doc); vi.stubGlobal('fetch', fetcher);
  await import('./message-center-sync');
  const cleanup = hooks.effect?.();
  return { win, previousRefresh, cleanup: cleanup as () => void };
}

describe('React message bell portal ownership', () => {
  beforeEach(() => { vi.resetModules(); hooks.effect = undefined; });
  afterEach(() => { vi.unstubAllGlobals(); });

  it('updates original nodes after portal movement and removes listeners from those nodes on cleanup', async () => {
    const first = bellFixture(true), second = bellFixture();
    const add = vi.spyOn(first.bell, 'addEventListener'), remove = vi.spyOn(first.bell, 'removeEventListener');
    const app = await mount([first.shell, second.shell, new AuthoredNode()], vi.fn().mockResolvedValue({ ok: true, json: async () => ({ summary: { unread_total: 1 } }) }));
    await vi.waitFor(() => expect(first.count.textContent).toBe('1'));
    // The owner mounted while the first menu was already outside its shell.
    app.win.dispatchEvent(new CustomEvent('message-center:summary-updated', { detail: { unread_total: 7 } }));
    expect(first.count.textContent).toBe('7'); expect(second.count.textContent).toBe('7');
    expect(first.caption.textContent).toBe('未读 7 条');
    expect(first.bell.attributes.get('aria-label')).toBe('打开通知中心，7 条未读消息');
    const clickHandler = add.mock.calls.find(([name]) => name === 'click')?.[1];
    app.cleanup();
    expect(remove).toHaveBeenCalledWith('click', clickHandler);
    expect(first.shell.dataset.messageCenterBellManaged).toBeUndefined();
    expect(app.win.refreshMessageCenterBell).toBe(app.previousRefresh);
    app.win.dispatchEvent(new CustomEvent('message-center:summary-updated', { detail: { unread_total: 8 } }));
    expect(first.count.textContent).toBe('7');
  });

  it('does not write a pending refresh into retired nodes or replace a newer global refresh owner', async () => {
    const { shell, count } = bellFixture(true);
    let finish: (value: unknown) => void = () => {};
    const response = new Promise(resolve => { finish = resolve; });
    const app = await mount([shell], vi.fn().mockReturnValue(response));
    const newerRefresh = vi.fn(); app.win.refreshMessageCenterBell = newerRefresh;
    app.cleanup();
    finish({ ok: true, json: async () => ({ summary: { unread_total: 99 } }) });
    await response; await Promise.resolve(); await Promise.resolve();
    expect(count.textContent).toBe('0');
    expect(app.win.refreshMessageCenterBell).toBe(newerRefresh);
  });
});
