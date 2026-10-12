import { describe, expect, it } from 'vitest';

import {
  blogAriaLabel,
  blogCaption,
  blogCountText,
  blogHasBadge,
  blogTitle,
  normalizeBlogTopbarResponse,
  normalizeBlogTopbarSummary,
} from '@/lib/blog-topbar';

const today = (todayNewCount: number) => ({ todayNewCount, followingUnreadCount: 0 });

describe('blog-topbar helpers', () => {
  it('formats the blog topbar count and labels', () => {
    expect(blogCountText(today(0))).toBe('+0');
    expect(blogCountText(today(4))).toBe('+4');
    expect(blogCountText(today(120))).toBe('+99');
    expect(blogCaption(today(0))).toBe('观点与交流');
    expect(blogCaption(today(3))).toBe('今日新增 3 篇');
    expect(blogCaption(today(120))).toBe('今日新增 99+ 篇');
    expect(blogAriaLabel(today(0))).toBe('打开博客');
    expect(blogAriaLabel(today(6))).toBe('打开博客，今日新增 6 篇');
    expect(blogTitle(today(0))).toBe('博客');
    expect(blogTitle(today(2))).toBe('博客：今日新增 2 篇');
    expect(blogHasBadge(today(0))).toBe(false);
  });

  it('prefers following updates over the daily count', () => {
    const summary = { todayNewCount: 9, followingUnreadCount: 3 };
    expect(blogHasBadge(summary)).toBe(true);
    expect(blogCountText(summary)).toBe('3');
    expect(blogCaption(summary)).toBe('关注更新 3');
    expect(blogAriaLabel(summary)).toBe('打开博客，关注更新 3 篇');
    expect(blogTitle({ todayNewCount: 0, followingUnreadCount: 120 })).toBe('博客：关注更新 120 篇');
    expect(blogCaption({ todayNewCount: 0, followingUnreadCount: 120 })).toBe('关注更新 99+');
  });

  it('normalizes blog summary payloads defensively', () => {
    expect(normalizeBlogTopbarResponse({ summary: { today_new_count: '8' } })).toEqual({
      summary: { todayNewCount: 8, followingUnreadCount: 0 },
    });
    expect(normalizeBlogTopbarSummary({ today_new_count: -2, following_unread_count: '4' })).toEqual({
      todayNewCount: 0,
      followingUnreadCount: 4,
    });
  });
});
