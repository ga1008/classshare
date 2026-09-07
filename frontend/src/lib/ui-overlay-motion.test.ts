import { afterEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Native module shared by SSR pages outside the frontend tree.
import { setOverlayOpen } from '../../../static/js/ui_overlay_motion.js';

const style = (duration = '280ms') => ({ opacity: '0', transitionDuration: duration, transitionDelay: '0ms', animationDuration: '0s', animationDelay: '0s', animationIterationCount: '1' });
function fixture(reduced = false) {
  const listeners = new Set<(event: { matches: boolean }) => void>();
  const motion = {
    matches: reduced,
    addEventListener: (_: string, callback: (event: { matches: boolean }) => void) => listeners.add(callback),
    removeEventListener: (_: string, callback: (event: { matches: boolean }) => void) => listeners.delete(callback),
  };
  const surface = { style: style() };
  const element = {
    hidden: true, dataset: {} as Record<string, string>, style: style('180ms'),
    querySelectorAll: vi.fn(() => [surface]),
    ownerDocument: { defaultView: { matchMedia: () => motion, getComputedStyle: (node: { style: ReturnType<typeof style> }) => node.style } },
  };
  return { element, surface, reduce() { motion.matches = true; listeners.forEach((callback) => callback({ matches: true })); }, listeners };
}
afterEach(() => vi.useRealTimers());

describe('native overlay presence', () => {
  it('establishes a visible entry frame then keeps both surfaces until exit completes', async () => {
    vi.useFakeTimers();
    const { element } = fixture();
    const opening = setOverlayOpen(element, true);
    expect(element.hidden).toBe(false);
    expect(element.dataset.uiOverlayState).toBe('open');
    await vi.advanceTimersByTimeAsync(314);
    expect(await opening).toBe(true);
    const closing = setOverlayOpen(element, false);
    expect(element.dataset.uiOverlayState).toBe('closed');
    await vi.advanceTimersByTimeAsync(280);
    expect(element.hidden).toBe(false);
    await vi.advanceTimersByTimeAsync(34);
    expect(await closing).toBe(true);
    expect(element.hidden).toBe(true);
  });

  it('cancels stale hide and cleanup when a closing overlay is reopened', async () => {
    vi.useFakeTimers();
    const { element } = fixture();
    const opening = setOverlayOpen(element, true);
    const closing = setOverlayOpen(element, false);
    expect(await opening).toBe(false);
    const reopened = setOverlayOpen(element, true);
    expect(await closing).toBe(false);
    await vi.runAllTimersAsync();
    expect(await reopened).toBe(true);
    expect(element.hidden).toBe(false);
    expect(element.dataset.uiOverlayState).toBe('open');
  });

  it('does not extend an exit or execute cleanup twice on repeated dismissals', async () => {
    vi.useFakeTimers();
    const { element } = fixture();
    element.hidden = false;
    const first = setOverlayOpen(element, false);
    await vi.advanceTimersByTimeAsync(100);
    expect(setOverlayOpen(element, false)).toBe(first);
    await vi.advanceTimersByTimeAsync(214);
    expect(await first).toBe(true);
    expect(element.hidden).toBe(true);
  });

  it('completes synchronously in reduced motion and reacts when that setting changes mid-exit', async () => {
    vi.useFakeTimers();
    const reduced = fixture(true);
    await setOverlayOpen(reduced.element, true);
    const close = setOverlayOpen(reduced.element, false);
    expect(reduced.element.hidden).toBe(true);
    expect(await close).toBe(true);
    const live = fixture();
    live.element.hidden = false;
    const closing = setOverlayOpen(live.element, false);
    live.reduce();
    expect(live.element.hidden).toBe(true);
    expect(await closing).toBe(true);
    expect(live.listeners.size).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('observes finite animations on explicit surfaces, not unrelated descendants', async () => {
    vi.useFakeTimers();
    const { element, surface } = fixture();
    let finishSurface!: () => void;
    const animation = { effect: { getComputedTiming: () => ({ endTime: 280 }) }, playState: 'running', finished: new Promise<void>((resolve) => { finishSurface = resolve; }) };
    Object.assign(element, { getAnimations: () => [] });
    Object.assign(surface, { getAnimations: () => [animation] });
    element.hidden = false;
    const closing = setOverlayOpen(element, false);
    await vi.advanceTimersByTimeAsync(200);
    expect(element.hidden).toBe(false);
    finishSurface();
    expect(await closing).toBe(true);
    expect(element.hidden).toBe(true);
    expect(element.querySelectorAll).toHaveBeenCalledWith('[data-ui-overlay-surface]');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('ignores infinite surface animation and closes immediately when nothing is transitioning', async () => {
    const { element, surface } = fixture();
    const spinner = { effect: { getComputedTiming: () => ({ endTime: Infinity }) }, playState: 'running', finished: new Promise(() => {}) };
    Object.assign(element, { getAnimations: () => [] });
    Object.assign(surface, { getAnimations: () => [spinner] });
    element.hidden = false;
    expect(await setOverlayOpen(element, false)).toBe(true);
    expect(element.hidden).toBe(true);
  });
});
