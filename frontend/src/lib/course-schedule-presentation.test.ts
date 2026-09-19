import { describe, expect, it } from 'vitest';
import { compactClassroomName, adjustmentActionText, classroomChangeState } from '../../../static/js/course_schedule_presentation.js';

describe('compact classroom display', () => {
  it.each([
    ['大成楼C108教室', 'C108教室'],
    ['（大成楼C108）AI数智财经创新中心', 'C108教室'],
    ['(知新楼B416-1) 国际贸易综合实验室', 'B416-1教室'],
    ['B416-1', 'B416-1教室'],
    ['C108教室', 'C108教室'],
    ['大成楼  C108', 'C108教室'],
    ['知新楼Ｂ４１６－１', 'B416-1教室'],
    ['（C108）AI数智财经创新中心', 'C108教室'],
    ['教室：B310', 'B310教室'],
    ['大成楼108教室', '108教室'],
    ['101教室', '101教室'],
  ])('formats %s without losing its room number', (value, expected) => {
    expect(compactClassroomName(value)).toBe(expected);
  });

  it.each([
    '人工智能2601班（专升本）', 'AI2601班', 'A2601班', '2026-2027第一学期',
    '五合校区体育场1号门', 'AI数智财经创新中心', '设备型号C108',
    'C108/C109', 'C108 / C109教室', 'C108教室或C109教室', '大成楼C108教室、大成楼C109教室', '学号2024010932',
  ])('preserves ambiguous or non-classroom location %s', value => {
    expect(compactClassroomName(value)).toBe(value);
  });

  it('does not turn non-text values into invented location names', () => {
    for (const value of [undefined, null, {}, 108]) expect(compactClassroomName(value)).toBe('');
    expect(compactClassroomName('  体育馆  ')).toBe('体育馆');
  });
});

function change(original: any, proposed: any, kind = 'move') {
  return { adjustment: { phase: 'pending', endpoint: 'original', kind, original, proposed } };
}
const old = { date: '2026-09-25', sections: [2, 3], room: '（大成楼C108）AI数智财经创新中心' };

describe('adjustment actions describe the actual changed facts', () => {
  it('exposes the same three-state room comparison for connection captions', () => {
    expect(classroomChangeState(old.room, 'C108教室')).toBe(false);
    expect(classroomChangeState(old.room, '知新楼C108教室')).toBe(true);
    expect(classroomChangeState(old.room, '')).toBeNull();
    expect(classroomChangeState('教室待定', old.room)).toBeNull();
    expect(classroomChangeState(null, null)).toBeNull();
  });

  it('uses room-only text even when the source kind is move', () => {
    expect(adjustmentActionText(change(old, { ...old, room: '知新楼B416-1' }))).toBe('改教室');
  });

  it('detects date and period changes independently of the source kind', () => {
    expect(adjustmentActionText(change(old, { ...old, date: '2026-09-26' }, 'room'))).toBe('改时间');
    expect(adjustmentActionText(change(old, { ...old, sections: [6, 7] }))).toBe('改时间');
    expect(adjustmentActionText(change(old, { ...old, date: '2026-09-26', room: 'B416-1' }))).toBe('教室+时间');
  });

  it('normalizes room formatting and short names without inventing another change', () => {
    for (const room of ['大成楼C108教室', '大成楼 Ｃ１０８', 'C108', 'C108教室']) {
      expect(adjustmentActionText(change(old, { ...old, date: '2026-09-26', room }))).toBe('改时间');
    }
    expect(adjustmentActionText(change(old, { ...old, room: '知新楼C108教室' }))).toBe('改教室');
  });

  it('does not treat section ordering or duplicates as another time', () => {
    expect(adjustmentActionText(change(old, { ...old, sections: ['3', '2', '3'], room: 'B416-1' }))).toBe('改教室');
  });

  it('keeps cancellations distinct and hides actions outside pending state', () => {
    expect(adjustmentActionText(change(old, null, 'cancel'))).toBe('停课');
    expect(adjustmentActionText({ adjustment: { phase: 'approved', kind: 'cancel', endpoint: 'original' } })).toBe('');
    expect(adjustmentActionText(null)).toBe('');
  });

  it.each([
    { ...old, room: '' }, { ...old, room: '教室待定' }, { ...old, date: '2026-02-30' },
    { ...old, sections: [] }, { ...old, sections: [true, 3] }, null,
  ])('does not infer a specific action with incomplete facts', proposed => {
    expect(adjustmentActionText(change(old, proposed))).toBe('待审变更');
  });

  it('uses a neutral label if the two supplied positions are identical', () => {
    expect(adjustmentActionText(change(old, structuredClone(old)))).toBe('待审变更');
  });
});
