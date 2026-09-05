import { describe, expect, it } from 'vitest';
import { dashboardAgendaDataset, normalizeDashboardItems } from './dashboard-workspace';


describe('dashboard workspace full collection', () => {
  const items = normalizeDashboardItems([
    { key: 'class:1', kind: 'class', title: '网络课', offering_id: 1, starts_at: '2026-09-04T08:00:00+08:00', is_actionable: false },
    { key: 'todo:1', kind: 'todo', title: '实验报告', offering_id: 1, due_at: '2026-09-04T20:00:00+08:00', is_actionable: true },
    { key: 'todo:2', kind: 'todo', title: '无日期事项', offering_id: 2, is_actionable: true },
    { key: 'todo:3', kind: 'todo', title: '已完成报告', offering_id: 1, due_at: '2026-09-06T20:00:00+08:00', is_completed: true },
  ]);

  it('preserves backend completion and actionability without inventing completion for past classes', () => {
    expect(items[0]).toMatchObject({ kind: 'class', is_actionable: false, is_completed: false });
    expect(items[1]).toMatchObject({ is_actionable: true, is_completed: false });
    expect(items[3]).toMatchObject({ is_actionable: false, is_completed: true });
    expect(items).toHaveLength(4);
  });

  it('does not truncate the complete authorized payload and collapses repeated source keys', () => {
    const raw = Array.from({ length: 100 }, (_, id) => ({ key: `manual:${id}`, kind: 'manual', title: `待办 ${id}` }));
    expect(normalizeDashboardItems([...raw, raw[0]])).toHaveLength(100);
    expect(normalizeDashboardItems(null)).toEqual([]);
  });

  it('preserves private todo ownership and reminder values for the legacy editor', () => {
    const [item] = normalizeDashboardItems([{ key: 'todo:8', kind: 'manual', offering_id: 0,
      agenda_data: { is_manual: true, todo_id: 8, class_offering_id: 0, reminder_enabled: false, email_reminder_enabled: true, reminder_lead_minutes: 60 },
    }]);
    expect(dashboardAgendaDataset(item)).toMatchObject({ kind: 'todo', manual: '1', todoId: '8', classOfferingId: '0', reminderEnabled: '0', emailReminderEnabled: '1', reminderLead: '60' });
  });
});
