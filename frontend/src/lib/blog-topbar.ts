export type BlogTopbarSummary = {
  todayNewCount: number;
};

export type BlogTopbarResponse = {
  summary: BlogTopbarSummary;
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function numberFrom(value: unknown, fallback = 0): number {
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : fallback;
}

export function blogCountText(todayNewCount: number): string {
  return todayNewCount > 99 ? '+99' : `+${Math.max(0, todayNewCount)}`;
}

export function blogCaption(todayNewCount: number): string {
  return todayNewCount > 0
    ? `今日新增 ${todayNewCount > 99 ? '99+' : todayNewCount} 篇`
    : '观点与交流';
}

export function blogAriaLabel(todayNewCount: number): string {
  return todayNewCount > 0
    ? `打开博客，今日新增 ${todayNewCount} 篇`
    : '打开博客';
}

export function blogTitle(todayNewCount: number): string {
  return todayNewCount > 0
    ? `博客：今日新增 ${todayNewCount} 篇`
    : '博客';
}

export function normalizeBlogTopbarResponse(value: unknown): BlogTopbarResponse {
  const response = asRecord(value);
  return {
    summary: normalizeBlogTopbarSummary(response.summary),
  };
}

export function normalizeBlogTopbarSummary(value: unknown): BlogTopbarSummary {
  const summary = asRecord(value);
  return {
    todayNewCount: Math.max(0, numberFrom(summary.today_new_count)),
  };
}
