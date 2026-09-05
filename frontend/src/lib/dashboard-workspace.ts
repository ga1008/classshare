export type DashboardItem = {
  key: string;
  kind: string;
  offering_id: number;
  title: string;
  subtitle: string;
  href: string;
  starts_at: string;
  due_at: string;
  effective_due_at: string;
  status: string;
  is_completed: boolean;
  is_actionable: boolean;
  date_label: string;
  time_label: string;
  type_label: string;
  status_label: string;
  action_label: string;
  date_bucket: string;
  agenda_data: Record<string, unknown>;
};

export type DashboardFilters = {
  query: string;
  offering: string;
  kind: string;
  date: string;
  state: string;
};

export const dashboardKindLabels: Record<string, string> = {
  class: '上课', invigilation: '监考', exam: '考试安排', assignment: '作业',
  exam_task: '考试', stage: '个人试炼', manual: '个人待办', material: '继续阅读',
  review: '复盘', teacher_work: '教学工作', poll: '投票',
};

const recordOf = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown, fallback = '') => typeof value === 'string' ? value : fallback;

export function normalizeDashboardItems(value: unknown): DashboardItem[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.flatMap((raw, index) => {
    const item = recordOf(raw);
    const key = text(item.key, `item-${index}`);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{
      key, kind: text(item.kind, 'todo'), offering_id: Number(item.offering_id) || 0,
      title: text(item.title, '待办事项'), subtitle: text(item.subtitle), href: text(item.href),
      starts_at: text(item.starts_at), due_at: text(item.due_at), effective_due_at: text(item.effective_due_at),
      status: text(item.status), is_completed: item.is_completed === true, is_actionable: item.is_actionable === true,
      date_label: text(item.date_label), time_label: text(item.time_label),
      type_label: text(item.type_label, dashboardKindLabels[text(item.kind)] || '事项'),
      status_label: text(item.status_label), action_label: text(item.action_label),
      date_bucket: text(item.date_bucket), agenda_data: recordOf(item.agenda_data),
    }];
  });
}

/** The existing agenda controller remains the authority for editing and reminders. */
export function dashboardAgendaDataset(item: DashboardItem): Record<string, string> {
  const source = item.agenda_data;
  const detail = recordOf(source.detail);
  const value = (key: string, fallback = '') => source[key] == null ? fallback : String(source[key]);
  const flag = (key: string) => source[key] ? '1' : '0';
  return {
    kind: value('kind', item.kind === 'manual' ? 'todo' : item.kind), kindLabel: item.type_label, title: item.title, subtitle: item.subtitle,
    when: [item.date_label, item.time_label].filter(Boolean).join(' '), relative: '', status: item.status,
    href: item.href, manual: flag('is_manual'), todoId: value('todo_id'),
    classOfferingId: value('class_offering_id', String(item.offering_id)), notes: value('notes'),
    dueAt: value('due_at_raw', item.due_at), startAt: value('start_at_raw', item.starts_at),
    reminderEnabled: flag('reminder_enabled'), emailReminderEnabled: flag('email_reminder_enabled'),
    reminderLead: value('reminder_lead_minutes', '1440'), priority: value('priority', 'normal'),
    eventId: value('event_id'), canReminder: flag('can_email_reminder'),
    subject: text(detail.subject), date: text(detail.date_label), time: text(detail.time_label),
    campus: text(detail.campus), classroom: text(detail.classroom),
    teachingClass: text(detail.teaching_class), invigilators: text(detail.invigilators), role: text(detail.role),
  };
}
