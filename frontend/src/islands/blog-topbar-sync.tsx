import { useEffect } from 'react';

import {
  blogAriaLabel,
  blogCaption,
  blogCountText,
  blogTitle,
  normalizeBlogTopbarResponse,
  type BlogTopbarSummary,
} from '@/lib/blog-topbar';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

const BLOG_SUMMARY_URL = '/api/blog/summary';
const POLL_INTERVAL_MS = 60_000;

async function fetchBlogTopbarSummary(): Promise<BlogTopbarSummary> {
  const response = await fetch(BLOG_SUMMARY_URL, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`Blog summary failed: ${response.status}`);
  }
  return normalizeBlogTopbarResponse(await response.json()).summary;
}

function updateBlogTopbar(
  entries: HTMLElement[],
  countNodes: HTMLElement[],
  captionNodes: HTMLElement[],
  summary: BlogTopbarSummary,
) {
  const todayNewCount = summary.todayNewCount;
  const hasNewCount = todayNewCount > 0;

  for (const entry of entries) {
    entry.classList.toggle('has-new-count', hasNewCount);
    entry.setAttribute('aria-label', blogAriaLabel(todayNewCount));
    entry.title = blogTitle(todayNewCount);
  }

  for (const captionNode of captionNodes) {
    captionNode.textContent = blogCaption(todayNewCount);
  }

  for (const countNode of countNodes) {
    countNode.hidden = !hasNewCount;
    countNode.textContent = hasNewCount ? blogCountText(todayNewCount) : '+0';
  }
}

function useBlogTopbarSync() {
  useEffect(() => {
    const entries = Array.from(document.querySelectorAll<HTMLElement>('[data-blog-topbar-entry]'));
    const countNodes = Array.from(document.querySelectorAll<HTMLElement>('[data-blog-today-count]'));
    const captionNodes = Array.from(document.querySelectorAll<HTMLElement>('[data-blog-topbar-caption]'));
    if (entries.length === 0 && countNodes.length === 0 && captionNodes.length === 0) {
      return undefined;
    }

    for (const node of [...entries, ...countNodes, ...captionNodes]) {
      node.dataset.blogTopbarManaged = 'react';
    }

    const refreshBlogTopbar = async () => {
      try {
        updateBlogTopbar(entries, countNodes, captionNodes, await fetchBlogTopbarSummary());
      } catch {
        // Blog badges are ambient status; keep navigation usable if polling fails.
      }
    };
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        void refreshBlogTopbar();
      }
    };

    window.refreshBlogTopbar = refreshBlogTopbar;
    void refreshBlogTopbar();
    const intervalId = window.setInterval(() => {
      if (!document.hidden) {
        void refreshBlogTopbar();
      }
    }, POLL_INTERVAL_MS);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      window.clearInterval(intervalId);
      for (const node of [...entries, ...countNodes, ...captionNodes]) {
        delete node.dataset.blogTopbarManaged;
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);
}

function BlogTopbarSyncIsland() {
  useBlogTopbarSync();
  return null;
}

mountReactIslandsWhenReady({
  islandName: 'blog-topbar-sync',
  defaultMountIdPrefix: 'blog-topbar-sync',
  getProps: () => ({}),
  render: () => <BlogTopbarSyncIsland />,
});
