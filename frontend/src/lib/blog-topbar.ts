export type BlogTopbarSummary = {
  todayNewCount: number;
  // New posts from followed authors/sections since the feed was last opened
  // (docs/blog-closed-loop-plan-2026-10-12.md §6.4); shown before today's count.
  followingUnreadCount: number;
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

function capped(count: number): string {
  return count > 99 ? '99+' : String(count);
}

export function blogHasBadge(summary: BlogTopbarSummary): boolean {
  return summary.followingUnreadCount > 0 || summary.todayNewCount > 0;
}

export function blogCountText(summary: BlogTopbarSummary): string {
  if (summary.followingUnreadCount > 0) return capped(summary.followingUnreadCount);
  return summary.todayNewCount > 99 ? '+99' : `+${Math.max(0, summary.todayNewCount)}`;
}

export function blogCaption(summary: BlogTopbarSummary): string {
  if (summary.followingUnreadCount > 0) return `关注更新 ${capped(summary.followingUnreadCount)}`;
  return summary.todayNewCount > 0 ? `今日新增 ${capped(summary.todayNewCount)} 篇` : '观点与交流';
}

export function blogAriaLabel(summary: BlogTopbarSummary): string {
  if (summary.followingUnreadCount > 0) return `打开博客，关注更新 ${summary.followingUnreadCount} 篇`;
  return summary.todayNewCount > 0 ? `打开博客，今日新增 ${summary.todayNewCount} 篇` : '打开博客';
}

export function blogTitle(summary: BlogTopbarSummary): string {
  if (summary.followingUnreadCount > 0) return `博客：关注更新 ${summary.followingUnreadCount} 篇`;
  return summary.todayNewCount > 0 ? `博客：今日新增 ${summary.todayNewCount} 篇` : '博客';
}

export function normalizeBlogTopbarResponse(value: unknown): BlogTopbarResponse {
  const response = asRecord(value);
  return {
    summary: normalizeBlogTopbarSummary(response.summary),
  };
}

export const BLOG_SUMMARY_EVENT = 'blog:summary';

export type BlogSummaryDetail = BlogTopbarSummary & {
  // When the request started: a consumer drops answers older than its own
  // last state change (e.g. the following feed was just marked seen).
  requestedAt: number;
};

/** One summary fetch feeds the topbar and the blog page (plan §9.3 P7). */
export function publishBlogSummary(summary: BlogTopbarSummary, requestedAt: number): BlogSummaryDetail {
  const detail = { ...summary, requestedAt };
  window.__lanshareBlogSummary = detail;
  window.dispatchEvent(new CustomEvent(BLOG_SUMMARY_EVENT, { detail }));
  return detail;
}

export function normalizeBlogTopbarSummary(value: unknown): BlogTopbarSummary {
  const summary = asRecord(value);
  return {
    todayNewCount: Math.max(0, numberFrom(summary.today_new_count)),
    followingUnreadCount: Math.max(0, numberFrom(summary.following_unread_count)),
  };
}
