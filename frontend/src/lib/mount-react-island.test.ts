import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { createRoot } = vi.hoisted(() => ({ createRoot: vi.fn() }));
vi.mock('react-dom/client', () => ({ createRoot }));

import { mountReactIslands, mountReactIslandsWhenReady, resolveIslandMountId, unmountReactIsland } from './mount-react-island';

describe('resolveIslandMountId', () => {
  it('uses an explicit data-island-id when present', () => {
    expect(resolveIslandMountId({ dataset: { islandId: 'profile-entry' } } as unknown as HTMLElement, 0, 'profile')).toBe(
      'profile-entry',
    );
  });

  it('falls back to a stable prefix and one-based index', () => {
    expect(resolveIslandMountId({ dataset: {} } as unknown as HTMLElement, 2, 'feedback-launcher')).toBe(
      'feedback-launcher-3',
    );
  });
});

describe('React island root ownership', () => {
  let hosts: HTMLElement[];
  let doc: EventTarget & { readyState: string; querySelectorAll: () => HTMLElement[] };
  let roots: { render: ReturnType<typeof vi.fn>; unmount: ReturnType<typeof vi.fn> }[];
  const options = () => ({ islandName: 'fixture', getProps: () => ({}), render: () => null! });

  beforeEach(() => {
    hosts = [{ dataset: {} }, { dataset: {} }] as HTMLElement[];
    doc = Object.assign(new EventTarget(), { readyState: 'complete', querySelectorAll: () => hosts });
    roots = [];
    createRoot.mockReset().mockImplementation(() => {
      const root = { render: vi.fn(), unmount: vi.fn() };
      roots.push(root);
      return root;
    });
    vi.stubGlobal('document', doc);
    vi.stubGlobal('window', {});
  });

  afterEach(() => {
    hosts.forEach(unmountReactIsland);
    vi.unstubAllGlobals();
  });

  it('disposes only roots it created and allows a clean remount', () => {
    const first = mountReactIslands(options());
    const duplicate = mountReactIslands(options());
    duplicate.dispose();
    expect(createRoot).toHaveBeenCalledTimes(2);
    expect(roots.every(root => root.unmount.mock.calls.length === 0)).toBe(true);
    expect(unmountReactIsland(hosts[0])).toBe(true);
    expect(unmountReactIsland(hosts[0])).toBe(false);
    const replacement = mountReactIslands(options());
    first.dispose();
    first.dispose();
    expect(roots[0].unmount).toHaveBeenCalledTimes(1);
    expect(roots[1].unmount).toHaveBeenCalledTimes(1);
    expect(roots[2].unmount).not.toHaveBeenCalled();
    expect(hosts[0].dataset.reactMounted).toBe('true');
    replacement.dispose();
    expect(roots[2].unmount).toHaveBeenCalledTimes(1);
    expect(hosts[0].dataset.reactMounted).toBeUndefined();
  });

  it('leaves a foreign owner and its mounted marker untouched', () => {
    hosts[0].dataset.reactMounted = 'true';
    const mounted = mountReactIslands(options());
    expect(unmountReactIsland(hosts[0])).toBe(false);
    mounted.dispose();
    expect(createRoot).toHaveBeenCalledTimes(1);
    expect(hosts[0].dataset.reactMounted).toBe('true');
  });

  it('releases earlier roots and markers when a later props factory fails', () => {
    const failure = new Error('invalid island payload');
    expect(() => mountReactIslands({ ...options(), getProps: (_host, index) => {
      if (index === 1) throw failure;
      return {};
    } })).toThrow(failure);
    expect(roots[0].unmount).toHaveBeenCalledTimes(1);
    expect(hosts.map(host => host.dataset.reactMounted)).toEqual([undefined, undefined]);
    mountReactIslands(options()).dispose();
    expect(createRoot).toHaveBeenCalledTimes(3);
  });

  it('continues cleanup when one root unmount throws', () => {
    const mounted = mountReactIslands(options());
    roots[0].unmount.mockImplementation(() => { throw new Error('consumer cleanup failed'); });
    expect(mounted.dispose).toThrow('consumer cleanup failed');
    expect(roots[1].unmount).toHaveBeenCalledTimes(1);
    expect(hosts.map(host => host.dataset.reactMounted)).toEqual([undefined, undefined]);
    expect(mounted.dispose).not.toThrow();
  });

  it('cancels DOMContentLoaded before any root is created', () => {
    doc.readyState = 'loading';
    const waiting = mountReactIslandsWhenReady(options());
    waiting.dispose();
    waiting.dispose();
    doc.dispatchEvent(new Event('DOMContentLoaded'));
    expect(createRoot).not.toHaveBeenCalled();
  });

  it('disposes roots created by its one ready callback', () => {
    doc.readyState = 'loading';
    const waiting = mountReactIslandsWhenReady(options());
    doc.dispatchEvent(new Event('DOMContentLoaded'));
    doc.dispatchEvent(new Event('DOMContentLoaded'));
    expect(createRoot).toHaveBeenCalledTimes(2);
    waiting.dispose();
    expect(roots.every(root => root.unmount.mock.calls.length === 1)).toBe(true);
  });
});
