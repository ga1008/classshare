import { afterEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error The SSR reader module ships as native JavaScript outside Vite.
import { returnFromClassroomReader } from '../../../static/js/material_reader_return.js';

const source = { url: '/classroom/7', close_tab: true };
const browser = () => ({
  location: { origin: 'https://classroom.example', assign: vi.fn() },
  history: { length: 2, back: vi.fn() }, close: vi.fn(), setTimeout,
});
afterEach(() => vi.useRealTimers());

describe('verified classroom reader return', () => {
  it('closes the new reader tab and has a classroom fallback when closing is blocked', async () => {
    vi.useFakeTimers();
    const windowRef = browser();
    expect(returnFromClassroomReader(source, windowRef)).toBe(true);
    expect(windowRef.close).toHaveBeenCalledTimes(1);
    expect(windowRef.location.assign).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(160);
    expect(windowRef.location.assign).toHaveBeenCalledWith('/classroom/7');
  });

  it('uses only the validated classroom destination for a same-tab failure', () => {
    const windowRef = browser();
    expect(returnFromClassroomReader({ ...source, close_tab: false }, windowRef)).toBe(true);
    expect(windowRef.close).not.toHaveBeenCalled();
    expect(windowRef.location.assign).toHaveBeenCalledWith('/classroom/7');
  });

  it('does not intercept an ordinary reader or accept an external return URL', () => {
    const windowRef = browser();
    expect(returnFromClassroomReader(null, windowRef)).toBe(false);
    expect(returnFromClassroomReader({ url: 'https://elsewhere.example/' }, windowRef)).toBe(false);
    expect(returnFromClassroomReader({ url: '//elsewhere.example/' }, windowRef)).toBe(false);
    expect(windowRef.close).not.toHaveBeenCalled();
    expect(windowRef.location.assign).not.toHaveBeenCalled();
  });

  it('backs out of package navigation then closes at the entry even though history.length stays large', () => {
    vi.useFakeTimers();
    const windowRef = browser();
    const frame = { src: '/materials/render/9/lesson_3.html', contentWindow: { location: { href: 'https://classroom.example/materials/render/9/lesson_4.html' } } };
    returnFromClassroomReader(source, windowRef, frame);
    expect(windowRef.history.back).toHaveBeenCalledTimes(1);
    expect(windowRef.close).not.toHaveBeenCalled();
    frame.contentWindow.location.href = 'https://classroom.example/materials/render/9/lesson_3.html';
    returnFromClassroomReader(source, windowRef, frame);
    expect(windowRef.history.back).toHaveBeenCalledTimes(1);
    expect(windowRef.close).toHaveBeenCalledTimes(1);
  });

  it('can leave a cross-origin package navigation without reading that document', () => {
    const windowRef = browser();
    const frame = { src: '/materials/render/9/', get contentWindow(): never { throw new Error('cross-origin'); } };
    expect(returnFromClassroomReader(source, windowRef, frame)).toBe(true);
    expect(windowRef.history.back).toHaveBeenCalledTimes(1);
    expect(windowRef.close).not.toHaveBeenCalled();
  });
});
