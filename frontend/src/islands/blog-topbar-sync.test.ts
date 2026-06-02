import { describe, expect, it } from 'vitest';

import {
  blogAriaLabel,
  blogCaption,
  blogCountText,
  blogTitle,
  normalizeBlogTopbarResponse,
  normalizeBlogTopbarSummary,
} from '@/lib/blog-topbar';

describe('blog-topbar helpers', () => {
  it('formats the blog topbar count and labels', () => {
    expect(blogCountText(0)).toBe('+0');
    expect(blogCountText(4)).toBe('+4');
    expect(blogCountText(120)).toBe('+99');
    expect(blogCaption(0)).toBe('观点与交流');
    expect(blogCaption(3)).toBe('今日新增 3 篇');
    expect(blogCaption(120)).toBe('今日新增 99+ 篇');
    expect(blogAriaLabel(0)).toBe('打开博客');
    expect(blogAriaLabel(6)).toBe('打开博客，今日新增 6 篇');
    expect(blogTitle(0)).toBe('博客');
    expect(blogTitle(2)).toBe('博客：今日新增 2 篇');
  });

  it('normalizes blog summary payloads defensively', () => {
    expect(normalizeBlogTopbarResponse({ summary: { today_new_count: '8' } })).toEqual({
      summary: { todayNewCount: 8 },
    });
    expect(normalizeBlogTopbarSummary({ today_new_count: -2 })).toEqual({ todayNewCount: 0 });
  });
});
