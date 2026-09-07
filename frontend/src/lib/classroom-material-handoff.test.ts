import { describe, expect, it, vi } from 'vitest';
vi.mock('/static/js/api.js', () => ({ apiFetch: vi.fn() }));
vi.mock('/static/js/learning_material_selector.js', () => ({ initLearningMaterialSelector: vi.fn() }));
vi.mock('/static/js/session_material_ai_assistant.js', () => ({ initSessionMaterialAiAssistant: vi.fn() }));
vi.mock('/static/js/assignment_time.js?v=classroom-workspace-20260905', () => ({ initAssignmentClocks: vi.fn() }));
vi.mock('/static/js/ui.js', () => ({ showToast: vi.fn() }));
vi.mock('/static/js/classroom_material_list.js', () => ({ openMaterialListPopup: vi.fn() }));
// @ts-expect-error Native classroom controller outside the frontend tree.
import { createClassroomMaterialHandoff } from '../../../static/js/classroom_page.js';

function fixture() {
  const events = new EventTarget();
  const view = Object.assign(new EventTarget(), {
    requestAnimationFrame: (callback: () => void) => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: (id: number) => { frames.delete(id); },
  });
  let frameId = 0;
  const frames = new Map<number, () => void>();
  const handoff = createClassroomMaterialHandoff(events, view);
  const closed = () => events.dispatchEvent(new Event('classroom:workspace-closed'));
  const flush = () => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback()); };
  const signal = (name: string, detail = {}) => events.dispatchEvent(new CustomEvent(name, { detail }));
  return { events, view, frames, handoff, closed, flush, signal };
}

describe('classroom material handoff ownership', () => {
  it('waits for its workspace exit and one frame, then opens only once', () => {
    const { events, handoff, closed, flush } = fixture();
    const closeRequests = vi.fn();
    events.addEventListener('classroom:workspace-panel', closeRequests);
    const show = vi.fn();
    handoff.open(show);
    expect(closeRequests).toHaveBeenCalledTimes(1);
    expect(closeRequests.mock.calls[0][0].detail).toEqual({ panel: null, handoff: true });
    expect(show).not.toHaveBeenCalled();
    closed();
    expect(show).not.toHaveBeenCalled();
    flush();
    expect(show).toHaveBeenCalledTimes(1);
    closed(); flush();
    expect(show).toHaveBeenCalledTimes(1);
  });

  it.each(['classroom:workspace-panel', 'classroom:select-session', 'classroom:session-selected', 'classroom:workspace-surface-visible'])(
    'discards an exit listener superseded by %s before another dialog closes', name => {
      const { handoff, closed, flush, signal, frames } = fixture();
      const oldList = vi.fn();
      handoff.open(oldList);
      signal(name, { panel: 'tasks', order: 3 });
      closed(); flush();
      expect(oldList).not.toHaveBeenCalled();
      expect(frames.size).toBe(0);
    },
  );

  it('cancels the queued frame when a newer panel opens after the exit notification', () => {
    const { handoff, closed, flush, signal, frames } = fixture();
    const oldList = vi.fn();
    handoff.open(oldList);
    closed();
    expect(frames.size).toBe(1);
    signal('classroom:workspace-panel', { panel: 'session-detail' });
    expect(frames.size).toBe(0);
    flush(); closed(); flush();
    expect(oldList).not.toHaveBeenCalled();
  });

  it('a direct workspace trigger also supersedes the pending list before React changes surfaces', () => {
    const { events, handoff, closed, flush } = fixture();
    const oldList = vi.fn();
    handoff.open(oldList);
    const click = new Event('click');
    Object.defineProperty(click, 'target', { value: { closest: (selector: string) => selector.includes('[data-cw-history]') ? {} : null } });
    events.dispatchEvent(click);
    closed(); flush();
    expect(oldList).not.toHaveBeenCalled();
  });

  it('a later material request replaces both the former listener and queued frame', () => {
    const { handoff, closed, flush } = fixture();
    const first = vi.fn();
    const second = vi.fn();
    const latest = vi.fn();
    handoff.open(first);
    handoff.open(second);
    closed();
    handoff.open(latest);
    flush();
    expect(first).not.toHaveBeenCalled();
    expect(second).not.toHaveBeenCalled();
    expect(latest).not.toHaveBeenCalled();
    closed(); flush();
    expect(latest).toHaveBeenCalledTimes(1);
  });

  it('opens directly without a workspace and cancels outstanding work when leaving the page', () => {
    const { view, handoff, closed, flush } = fixture();
    const stale = vi.fn();
    const direct = vi.fn();
    handoff.open(stale);
    handoff.open(direct, false);
    expect(direct).toHaveBeenCalledTimes(1);
    closed(); flush();
    expect(stale).not.toHaveBeenCalled();
    handoff.open(stale);
    closed();
    view.dispatchEvent(new Event('pagehide'));
    flush(); closed(); flush();
    expect(stale).not.toHaveBeenCalled();
  });
});
