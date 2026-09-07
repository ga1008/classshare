import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ focus: vi.fn(), api: vi.fn(), events: [] as string[] }));
vi.mock('../../../static/js/api.js', () => ({ apiFetch: mocks.api }));
vi.mock('../../../static/js/ui.js', () => ({ escapeHtml: (text: string) => text, showToast: vi.fn() }));
vi.mock('../../../static/js/classroom_workspace.js', () => ({ materialOpenUrl: () => '/materials/view/4' }));
vi.mock('../../../static/js/classroom_material_focus.js', () => ({ ownClassroomMaterialFocus: mocks.focus }));

function fixture() {
  const nodes = new Map<string, Element>();
  const roots: Element[] = [];
  const view = {
    matchMedia: () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    getComputedStyle: () => ({ opacity: '0', transitionDuration: '280ms', transitionDelay: '0s', animationDuration: '0s', animationDelay: '0s', animationIterationCount: '1' }),
  };
  const document = {
    defaultView: view,
    createElement: () => new Element(),
    getElementById: (id: string) => nodes.get(id),
    body: { appendChild: (node: Element) => roots.push(node), classList: { add: vi.fn(), remove: vi.fn() } },
  };
  class Element {
    hidden = false;
    className = '';
    dataset: Record<string, string> = {};
    textContent = '';
    disabled = false;
    ownerDocument = document;
    children = new Map<string, Element>();
    attributes = new Map<string, string>();
    listeners = new Map<string, (event: unknown) => void>();
    focus = vi.fn();
    set innerHTML(html: string) {
      for (const id of html.matchAll(/id="([^"]+)"/g)) nodes.set(id[1], new Element());
      for (const selector of ['data-ui-overlay-surface', 'data-close-mat-popup', 'data-cancel-removal', 'data-confirm-removal']) {
        if (html.includes(selector)) this.children.set(`[${selector}]`, new Element());
      }
    }
    setAttribute(name: string, value: string) { this.attributes.set(name, value); }
    addEventListener(name: string, listener: (event: unknown) => void) { this.listeners.set(name, listener); }
    querySelector(selector: string) { return this.children.get(selector); }
    querySelectorAll(selector: string) { const child = this.querySelector(selector); return child ? [child] : []; }
    click(selector: string, target?: object) {
      const button = target || this.querySelector(selector);
      this.listeners.get('click')?.({ target: { closest: (query: string) => query === selector ? button : null }, stopPropagation() {} });
    }
  }
  vi.stubGlobal('document', document);
  vi.stubGlobal('window', { open: vi.fn() });
  mocks.focus.mockImplementation((overlay: Element) => {
    mocks.events.push(`own:${overlay.className}`);
    return () => mocks.events.push(`release:${overlay.className}`);
  });
  return { roots, nodes };
}

const options = { classOfferingId: 7, sessionId: 3, isTeacher: true, initialData: { can_manage: true, materials: [{ material_id: 4, name: '课程笔记', open_url: '/materials/view/4' }] } };
beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); vi.clearAllMocks(); mocks.events.length = 0; });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const load = async () => {
  // @ts-expect-error Native classroom module intentionally lives outside the frontend tree.
  return (await import('../../../static/js/classroom_material_list.js')).openMaterialListPopup;
};

describe('material overlay focus and return timing', () => {
  it('keeps focus owned and details closed until the list exit is complete', async () => {
    const { roots } = fixture();
    const open = await load();
    const onClose = vi.fn(() => mocks.events.push('details'));
    await open({ ...options, onClose });
    const list = roots[0];
    await vi.runAllTimersAsync();
    list.click('[data-close-mat-popup]');
    expect(list.hidden).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
    expect(mocks.events).toEqual(['own:ls-mat-popup']);
    await vi.advanceTimersByTimeAsync(314);
    expect(list.hidden).toBe(true);
    expect(list.attributes.get('aria-hidden')).toBe('true');
    expect(mocks.events).toEqual(['own:ls-mat-popup', 'release:ls-mat-popup', 'details']);
  });

  it('reopening cancels stale detail callbacks and preserves the original focus owner', async () => {
    const { roots } = fixture();
    const open = await load();
    const previousClose = vi.fn();
    const latestClose = vi.fn();
    await open({ ...options, onClose: previousClose });
    await vi.runAllTimersAsync();
    roots[0].click('[data-close-mat-popup]');
    await vi.advanceTimersByTimeAsync(100);
    await open({ ...options, onClose: latestClose });
    await vi.runAllTimersAsync();
    expect(roots[0].hidden).toBe(false);
    expect(previousClose).not.toHaveBeenCalled();
    expect(mocks.focus).toHaveBeenCalledTimes(1);
    roots[0].click('[data-close-mat-popup]');
    roots[0].click('[data-close-mat-popup]');
    await vi.runAllTimersAsync();
    expect(latestClose).toHaveBeenCalledTimes(1);
    expect(mocks.events.filter((event) => event.startsWith('release:'))).toEqual(['release:ls-mat-popup']);
  });

  it('unwinds the confirmation before the list focus owner and only then restores details', async () => {
    const { roots, nodes } = fixture();
    const open = await load();
    await open({ ...options, onClose: () => mocks.events.push('details') });
    nodes.get('lsMatPopupList')!.click('[data-remove-material]', { dataset: { removeMaterial: '4' } });
    await vi.runAllTimersAsync();
    expect(roots[1].hidden).toBe(false);
    roots[0].click('[data-close-mat-popup]');
    expect(roots.every((root) => !root.hidden)).toBe(true);
    await vi.runAllTimersAsync();
    expect(mocks.events).toEqual(['own:ls-mat-popup', 'own:ls-mat-confirm', 'release:ls-mat-confirm', 'release:ls-mat-popup', 'details']);
    expect(roots.every((root) => root.hidden)).toBe(true);
  });

  it('canceling confirmation leaves the material list and its focus owner alive', async () => {
    const { roots, nodes } = fixture();
    const open = await load();
    const onClose = vi.fn();
    await open({ ...options, onClose });
    nodes.get('lsMatPopupList')!.click('[data-remove-material]', { dataset: { removeMaterial: '4' } });
    await vi.runAllTimersAsync();
    roots[1].click('[data-cancel-removal]');
    expect(roots[1].hidden).toBe(false);
    await vi.runAllTimersAsync();
    expect(roots[1].hidden).toBe(true);
    expect(roots[0].hidden).toBe(false);
    expect(onClose).not.toHaveBeenCalled();
    expect(mocks.api).not.toHaveBeenCalled();
    expect(mocks.events).toEqual(['own:ls-mat-popup', 'own:ls-mat-confirm', 'release:ls-mat-confirm']);
  });
});
