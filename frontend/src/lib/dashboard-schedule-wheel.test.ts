import { describe, expect, it } from 'vitest';
import { scheduleWheelIntent } from '../../../static/js/course_schedule_deck.js';

describe('course schedule wheel ownership', () => {
  it('normalizes line/page delta while a fine trackpad accumulates one step', () => {
    expect(scheduleWheelIntent({ deltaY: 2, deltaMode: 1, index: 1, length: 20 }).step).toBe(1);
    expect(scheduleWheelIntent({ deltaY: -1, deltaMode: 2, index: 1, length: 20 }).step).toBe(-1);
    const partial = scheduleWheelIntent({ deltaY: 12, index: 1, length: 20 });
    expect(partial).toEqual({ consume: true, step: 0, pending: 12 });
    expect(scheduleWheelIntent({ deltaY: 20, index: 1, length: 20, pending: partial.pending }).step).toBe(1);
    expect(scheduleWheelIntent({ deltaY: -12, index: 1, length: 20, pending: 25 }).pending).toBe(-12);
  });
  it('releases outer scrolling at boundaries and never consumes zoom or horizontal input', () => {
    for (const options of [
      { deltaY: -120, index: 0, length: 20 }, { deltaY: 120, index: 19, length: 20 },
      { deltaY: 120, ctrlKey: true, index: 1, length: 20 }, { deltaY: 120, metaKey: true, index: 1, length: 20 },
      { deltaY: 0, index: 1, length: 20 }, { deltaY: 120, index: 0, length: 1 },
    ]) expect(scheduleWheelIntent(options).consume).toBe(false);
  });
});
