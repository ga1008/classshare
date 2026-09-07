import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('/static/js/api.js', () => ({ apiFetch: vi.fn() }));
vi.mock('/static/js/ui.js', () => ({ showToast: vi.fn() }));
vi.mock('/static/js/learning_certificate_reveal.js?v=cultivation-certificate-20260612', () => ({ initLearningCertificateReveal: vi.fn() }));
// @ts-expect-error Learning progress is a native SSR module outside the frontend tree.
import { initLearningProgressModal, initStudentInsightModal } from '../../../static/js/learning_progress.js';

function fixture(reduced = false) {
  const classes = () => {
    const values = new Set<string>();
    return { add: (value: string) => values.add(value), remove: (value: string) => values.delete(value), contains: (value: string) => values.has(value) };
  };
  const nodes = new Map<string, Node>();
  const view = {
    matchMedia: () => ({ matches: reduced, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    getComputedStyle: () => ({ opacity: '0', transitionDuration: '280ms', transitionDelay: '0s', animationDuration: '0s', animationDelay: '0s', animationIterationCount: '1' }),
  };
  const document = {
    defaultView: view, activeElement: null as Node | null, body: { classList: classes() },
    getElementById: (id: string) => nodes.get(id),
    querySelector: (selector: string) => selector === '[data-learning-panel]' ? panel : null,
    querySelectorAll: (selector: string) => selector === '[data-student-insight-open]' ? [student, secondStudent] : [trigger],
    addEventListener: vi.fn(),
  };
  class Node {
    hidden = true; src = ''; textContent = ''; offsetWidth = 400; offsetParent = {};
    ownerDocument = document; classList = classes(); dataset: Record<string, string> = {};
    attributes = new Map<string, string>(); selectors = new Map<string, Node>();
    listeners = new Map<string, (event?: object) => void>();
    focus = vi.fn(() => { document.activeElement = this; });
    setAttribute(name: string, value: string) { this.attributes.set(name, value); }
    getAttribute(name: string) { return this.attributes.get(name); }
    querySelector(selector: string) { return this.selectors.get(selector); }
    querySelectorAll(selector: string) { return selector === '[data-ui-overlay-surface]' ? [...this.selectors.values()].filter(node => node.attributes.has('data-ui-overlay-surface')) : []; }
    addEventListener(name: string, listener: (event?: object) => void) { this.listeners.set(name, listener); }
    click() { this.listeners.get('click')?.({ preventDefault() {}, target: this }); }
  }
  const trigger = new Node(), student = new Node(), secondStudent = new Node(), panel = new Node();
  student.dataset.studentInsightUrl = '/student/1?embed=1';
  secondStudent.dataset.studentInsightUrl = '/student/2?embed=1';
  for (const [id, shellClass] of [['learning-progress-modal', '.learning-modal-shell'], ['student-insight-modal', '.student-insight-modal-shell']]) {
    const modal = new Node(); modal.selectors.set(shellClass, new Node()); nodes.set(id, modal);
  }
  const frame = new Node(), loading = new Node();
  nodes.get('student-insight-modal')!.selectors.set('[data-student-insight-frame]', frame);
  nodes.get('student-insight-modal')!.selectors.set('[data-student-insight-loading]', loading);
  ['learning-modal-close', 'student-insight-modal-close', 'student-insight-modal-title'].forEach(id => nodes.set(id, new Node()));
  const browser = { clearTimeout, setTimeout, requestAnimationFrame: vi.fn() };
  vi.stubGlobal('document', document); vi.stubGlobal('window', browser);
  initLearningProgressModal(); initStudentInsightModal();
  return { document, nodes, trigger, student, secondStudent, frame, browser };
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('cultivation and member insight presence', () => {
  it('keeps scrolling locked and focus inside cultivation until the complete exit', async () => {
    vi.useFakeTimers();
    const { trigger, nodes, document } = fixture();
    trigger.click();
    await vi.advanceTimersByTimeAsync(314);
    nodes.get('learning-modal-close')!.click();
    expect(nodes.get('learning-progress-modal')!.hidden).toBe(false);
    expect(document.body.classList.contains('has-learning-modal')).toBe(true);
    expect(trigger.focus).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(314);
    expect(nodes.get('learning-progress-modal')!.hidden).toBe(true);
    expect(document.body.classList.contains('has-learning-modal')).toBe(false);
    expect(trigger.focus).toHaveBeenCalledTimes(1);
  });

  it('cancels immediate close/open without a stale animation-frame callback or focus return', async () => {
    vi.useFakeTimers();
    const { trigger, nodes, browser } = fixture();
    trigger.click();
    nodes.get('learning-modal-close')!.click();
    trigger.click();
    await vi.runAllTimersAsync();
    expect(nodes.get('learning-progress-modal')!.hidden).toBe(false);
    expect(nodes.get('learning-progress-modal')!.dataset.uiOverlayState).toBe('open');
    expect(trigger.focus).not.toHaveBeenCalled();
    expect(browser.requestAnimationFrame).not.toHaveBeenCalled();
  });

  it('closes member insight after its exit while retaining the parent and iframe document', async () => {
    vi.useFakeTimers();
    const { trigger, student, nodes, document, frame } = fixture();
    trigger.click(); student.click();
    await vi.advanceTimersByTimeAsync(314);
    nodes.get('student-insight-modal-close')!.click();
    expect(document.body.classList.contains('has-student-insight-modal')).toBe(true);
    await vi.advanceTimersByTimeAsync(314);
    expect(nodes.get('student-insight-modal')!.hidden).toBe(true);
    expect(nodes.get('learning-progress-modal')!.hidden).toBe(false);
    expect(document.body.classList.contains('has-learning-modal')).toBe(true);
    expect(document.body.classList.contains('has-student-insight-modal')).toBe(false);
    expect(frame.src).toBe('/student/1?embed=1');
    expect(student.focus).toHaveBeenCalledTimes(1);
  });

  it('opening another member during exit cannot be hidden or refocused by the first member', async () => {
    vi.useFakeTimers();
    const { student, secondStudent, nodes, frame } = fixture();
    student.click();
    nodes.get('student-insight-modal-close')!.click();
    await vi.advanceTimersByTimeAsync(100);
    secondStudent.click();
    await vi.advanceTimersByTimeAsync(314);
    expect(nodes.get('student-insight-modal')!.hidden).toBe(false);
    expect(frame.src).toBe('/student/2?embed=1');
    expect(student.focus).not.toHaveBeenCalled();
    nodes.get('student-insight-modal-close')!.click();
    await vi.runAllTimersAsync();
    expect(secondStudent.focus).toHaveBeenCalledTimes(1);
  });

  it('reduced motion hides both overlays immediately without the former 240/260ms waits', async () => {
    vi.useFakeTimers();
    const { trigger, student, nodes } = fixture(true);
    trigger.click(); student.click();
    nodes.get('student-insight-modal-close')!.click();
    expect(nodes.get('student-insight-modal')!.hidden).toBe(true);
    nodes.get('learning-modal-close')!.click();
    expect(nodes.get('learning-progress-modal')!.hidden).toBe(true);
    await vi.runAllTimersAsync();
    expect(vi.getTimerCount()).toBe(0);
  });
});
