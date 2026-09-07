import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('/static/js/api.js', () => ({ apiFetch: vi.fn() }));
vi.mock('/static/js/learning_material_selector.js', () => ({ initLearningMaterialSelector: vi.fn() }));
vi.mock('/static/js/session_material_ai_assistant.js', () => ({ initSessionMaterialAiAssistant: vi.fn() }));
vi.mock('/static/js/assignment_time.js?v=classroom-workspace-20260905', () => ({ initAssignmentClocks: vi.fn() }));
vi.mock('/static/js/ui.js', () => ({ showToast: vi.fn() }));
vi.mock('/static/js/classroom_material_list.js', () => ({ openMaterialListPopup: vi.fn() }));
// @ts-expect-error Classroom controller is a native module outside the frontend tree.
import { createClassroomActivityPresence } from '../../../static/js/classroom_page.js';

function fixture(reduced = false) {
  const classList = () => {
    const values = new Set<string>();
    return { add: (name: string) => values.add(name), remove: (name: string) => values.delete(name), contains: (name: string) => values.has(name), toggle: (name: string, value: boolean) => value ? values.add(name) : values.delete(name) };
  };
  const view = {
    matchMedia: () => ({ matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    getComputedStyle: () => ({ height: '420px', opacity: '0', transitionDuration: reduced ? '0ms' : '280ms', transitionDelay: '0s', animationDuration: '0s', animationDelay: '0s', animationIterationCount: '1' }),
  };
  const document = { defaultView: view, activeElement: { name: 'chat-input' } };
  const panel = (name: string, height: number) => ({
    hidden: name !== 'discussion', inert: false, ownerDocument: document, classList: classList(), dataset: {} as Record<string, string>, attributes: new Map<string, string>(),
    contains: (element: object) => name === 'discussion' && element === document.activeElement,
    querySelectorAll: () => [], setAttribute(key: string, value: string) { this.attributes.set(key, value); },
    getBoundingClientRect: () => ({ height }),
    // These represent live child state, which a presence switch must never replace.
    draft: '尚未发送的讨论', scrollTop: 187, connection: { connected: true },
  });
  const panels = new Map([['discussion', panel('discussion', 640)], ['polls', panel('polls', 280)], ['resources', panel('resources', 420)]]);
  const style = { height: '', removeProperty: vi.fn((name: 'height') => { style[name] = ''; }) };
  const container = { ownerDocument: document, classList: classList(), style, getBoundingClientRect: () => ({ height: 480 }), getAnimations: () => [] };
  const focus = { focus: vi.fn() };
  return { panels, container, focus, select: createClassroomActivityPresence(panels, container) };
}
afterEach(() => vi.useRealTimers());

describe('persistent classroom activity transitions', () => {
  it('initializes the SSR selection without an entry animation or height placeholder', async () => {
    vi.useFakeTimers();
    const { select, panels, container } = fixture();
    expect(await select('discussion')).toBe(true);
    expect(panels.get('discussion')!.hidden).toBe(false);
    expect(panels.get('polls')!.hidden).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(container.style.height).toBe('');
  });

  it('makes the outgoing panel inert while crossfading and preserves its live state', async () => {
    vi.useFakeTimers();
    const { select, panels, container, focus } = fixture();
    await select('discussion');
    const discussion = panels.get('discussion')!;
    const connection = discussion.connection;
    const switched = select('polls', focus);
    expect(discussion.hidden).toBe(false);
    expect(discussion.inert).toBe(true);
    expect(discussion.attributes.get('aria-hidden')).toBe('true');
    expect(focus.focus).toHaveBeenCalledWith({ preventScroll: true });
    expect(panels.get('polls')!.hidden).toBe(false);
    expect(panels.get('polls')!.inert).toBe(false);
    expect(container.style.height).toBe('280px');
    await vi.runAllTimersAsync();
    expect(await switched).toBe(true);
    expect(discussion.hidden).toBe(true);
    expect(discussion.draft).toBe('尚未发送的讨论');
    expect(discussion.scrollTop).toBe(187);
    expect(discussion.connection).toBe(connection);
    expect(container.style.height).toBe('');
    expect(container.classList.contains('is-switching')).toBe(false);
  });

  it('cancels stale switch completions when switching through several panels back to discussion', async () => {
    vi.useFakeTimers();
    const { select, panels, focus } = fixture();
    await select('discussion');
    const polls = select('polls', focus);
    await vi.advanceTimersByTimeAsync(80);
    const resources = select('resources', focus);
    await vi.advanceTimersByTimeAsync(80);
    const discussion = select('discussion', focus);
    await vi.runAllTimersAsync();
    expect(await polls).toBe(false);
    expect(await resources).toBe(false);
    expect(await discussion).toBe(true);
    expect([...panels.values()].filter(panel => !panel.hidden)).toEqual([panels.get('discussion')]);
    expect(panels.get('discussion')!.inert).toBe(false);
    expect(panels.get('discussion')!.attributes.get('aria-hidden')).toBe('false');
  });

  it('keeps height containment until its own animation finishes, including a repeated tab click', async () => {
    vi.useFakeTimers();
    const { select, container, focus } = fixture();
    let finishHeight!: () => void;
    const height = { effect: { getComputedTiming: () => ({ endTime: 400 }) }, finished: new Promise<void>(resolve => { finishHeight = resolve; }) };
    Object.assign(container, { getAnimations: () => [height] });
    await select('discussion');
    const first = select('polls', focus);
    const repeated = select('polls', focus);
    await vi.runAllTimersAsync();
    expect(container.classList.contains('is-switching')).toBe(true);
    finishHeight();
    expect(await first).toBe(false);
    expect(await repeated).toBe(true);
    expect(container.style.height).toBe('');
  });

  it('switches immediately with reduced motion and never leaves an inert active panel', async () => {
    const { select, panels, container } = fixture(true);
    await select('discussion');
    const switched = select('resources');
    expect(panels.get('discussion')!.hidden).toBe(true);
    expect(panels.get('resources')!.hidden).toBe(false);
    expect(panels.get('resources')!.inert).toBe(false);
    expect(await switched).toBe(true);
    expect(container.classList.contains('is-switching')).toBe(false);
  });
});
