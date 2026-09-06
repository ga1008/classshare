import { afterEach, describe, expect, it, vi } from 'vitest';
// The legacy classroom controller also imports these native browser helpers.
// @ts-expect-error Native JavaScript module ships outside the Vite declaration graph.
import { bindClassroomLessonRail, materialEntryDecision, materialOpenUrl, sessionMaterialScope } from '../../../static/js/classroom_workspace.js';

describe('authorized session material navigation', () => {
  const origin = 'https://classroom.example';
  it('keeps home zero, real session IDs and no material scope for academic exams', () => {
    expect(sessionMaterialScope({ is_home_entry: true, id: -1 })).toBe(0);
    expect(sessionMaterialScope({ id: 42 })).toBe(42);
    expect(sessionMaterialScope({ id: 42, entry_type: 'academic_exam' })).toBeNull();
    expect(sessionMaterialScope(null)).toBeNull();
  });
  it('routes zero, one and multiple authorized entries without inferring from a primary ID', () => {
    expect(materialEntryDecision([], 7, 12, origin).kind).toBe('empty');
    expect(materialEntryDecision([{ material_id: 9, open_url: '/materials/render-view/9?path=lesson_3%2Findex.html' }], 7, 12, origin)).toMatchObject({ kind: 'reader', url: '/materials/render-view/9?path=lesson_3%2Findex.html&class_offering_id=7&session_id=12&classroom_reader_tab=1' });
    expect(materialEntryDecision([{ open_url: '/materials/view/1' }, { open_url: '/materials/view/2' }], 7, 12, origin).kind).toBe('list');
    expect(materialEntryDecision([{ open_url: '' }], 7, 12, origin).kind).toBe('unavailable');
    expect(materialEntryDecision(undefined, 7, 12, origin).kind).toBe('unavailable');
    expect(materialEntryDecision([null], 7, 12, origin).kind).toBe('unavailable');
  });
  it('preserves reader query and hash, clears stale home attribution, and rejects external destinations', () => {
    expect(materialOpenUrl('/materials/view/1?variant=optimized&session_id=99#part', 7, 0, origin)).toBe('/materials/view/1?variant=optimized&class_offering_id=7&classroom_reader_tab=1#part');
    expect(materialOpenUrl('https://elsewhere.example/materials/view/1', 7, 12, origin)).toBe('');
    expect(materialOpenUrl('javascript:alert(1)', 7, 12, origin)).toBe('');
  });
});

class Surface {
  listeners = new Map<string, Function[]>();
  dataset: Record<string, string> = {};
  classList = { add: vi.fn(), remove: vi.fn() };
  scrollLeft = 80; clientWidth = 300; scrollWidth = 1000; disabled = false; tabIndex = 0;
  setPointerCapture = vi.fn(); releasePointerCapture = vi.fn(); hasPointerCapture = vi.fn(() => false);
  focus = vi.fn(); scrollBy = vi.fn();
  addEventListener(type: string, callback: Function) { this.listeners.set(type, [...(this.listeners.get(type) || []), callback]); }
  emit(type: string, event: Record<string, unknown> = {}) { const value = { preventDefault: vi.fn(), stopImmediatePropagation: vi.fn(), ...event }; this.listeners.get(type)?.forEach(callback => callback(value)); return value; }
  getBoundingClientRect() { return { left: 0, width: this.clientWidth }; }
}

describe('lesson rail input separates browsing from selection', () => {
  afterEach(() => vi.unstubAllGlobals());
  const setup = () => {
    const surface = new Surface(), rail = new Surface(), buttons = [new Surface(), new Surface()];
    buttons.forEach((button, index) => button.dataset.sessionOrder = String(index + 1));
    vi.stubGlobal('window', Object.assign(surface, { matchMedia: () => ({ matches: false }) }));
    vi.stubGlobal('requestAnimationFrame', (callback: Function) => callback());
    const select = vi.fn(), activate = vi.fn();
    bindClassroomLessonRail({ rail, buttons, sessions: [], select, activate });
    return { surface, rail, buttons, select, activate };
  };
  it('dragging suppresses the following click and never activates or selects a lesson', () => {
    const { surface, rail, select, activate } = setup();
    rail.emit('pointerdown', { pointerType: 'mouse', button: 0, isPrimary: true, pointerId: 1, clientX: 100 });
    rail.emit('pointermove', { pointerId: 1, buttons: 1, clientX: 50 });
    expect(rail.scrollLeft).toBe(130);
    surface.emit('pointerup');
    const click = rail.emit('click');
    expect(click.stopImmediatePropagation).toHaveBeenCalled();
    expect(select).not.toHaveBeenCalled(); expect(activate).not.toHaveBeenCalled();
  });
  it('releasing outside before the threshold prevents a later hover from dragging', () => {
    const { surface, rail } = setup();
    rail.emit('pointerdown', { pointerType: 'mouse', button: 0, isPrimary: true, pointerId: 1, clientX: 100 });
    surface.emit('pointerup');
    rail.emit('pointermove', { pointerId: 1, buttons: 0, clientX: 40 });
    expect(rail.scrollLeft).toBe(80); expect(rail.setPointerCapture).not.toHaveBeenCalled();
  });
  it('a drag ending outside does not swallow later keyboard activation', () => {
    const { surface, rail } = setup();
    rail.emit('pointerdown', { pointerType: 'mouse', button: 0, isPrimary: true, pointerId: 1, clientX: 100 });
    rail.emit('pointermove', { pointerId: 1, buttons: 1, clientX: 50 });
    surface.emit('pointerup');
    expect(rail.emit('click', { detail: 0 }).stopImmediatePropagation).not.toHaveBeenCalled();
  });
  it('arrow keys only move focus, while activation comes from the native button click', () => {
    const { buttons, select, activate } = setup();
    buttons[0].emit('keydown', { key: 'ArrowRight' });
    expect(buttons[1].focus).toHaveBeenCalled(); expect(select).not.toHaveBeenCalled(); expect(activate).not.toHaveBeenCalled();
    expect(buttons.map(button => button.tabIndex)).toEqual([-1, 0]);
    buttons[1].emit('click');
    expect(activate).toHaveBeenCalledWith('2', buttons[1]);
  });
});
