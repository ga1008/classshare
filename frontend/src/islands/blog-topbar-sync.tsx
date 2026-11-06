import { useEffect } from 'react';
import { createAmbientPoll } from '../../../static/js/ambient_poll.js';

import {
  blogAriaLabel,
  blogCaption,
  blogCountText,
  blogHasBadge,
  blogTitle,
  normalizeBlogTopbarResponse,
  publishBlogSummary,
  type BlogTopbarSummary,
} from '@/lib/blog-topbar';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

const BLOG_SUMMARY_URL = '/api/blog/summary';
const POLL_INTERVAL_MS = 60_000;

async function fetchBlogTopbarSummary(signal: AbortSignal): Promise<BlogTopbarSummary> {
  const response = await fetch(BLOG_SUMMARY_URL, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw Object.assign(new Error(`Blog summary failed: ${response.status}`), {
      retryAfterSeconds: Number(response.headers.get('retry-after')) || 0,
    });
  }
  return normalizeBlogTopbarResponse(await response.json()).summary;
}

function updateBlogTopbar(
  entries: HTMLElement[],
  countNodes: HTMLElement[],
  captionNodes: HTMLElement[],
  summary: BlogTopbarSummary,
) {
  const hasNewCount = blogHasBadge(summary);

  for (const entry of entries) {
    entry.classList.toggle('has-new-count', hasNewCount);
    entry.setAttribute('aria-label', blogAriaLabel(summary));
    entry.title = blogTitle(summary);
  }

  for (const captionNode of captionNodes) {
    captionNode.textContent = blogCaption(summary);
  }

  for (const countNode of countNodes) {
    countNode.hidden = !hasNewCount;
    countNode.textContent = hasNewCount ? blogCountText(summary) : '+0';
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

    let disposed = false;
    const poll = createAmbientPoll(async signal => {
      const requestedAt = Date.now();
      const summary = await fetchBlogTopbarSummary(signal);
      if (disposed || signal.aborted) return;
      updateBlogTopbar(entries, countNodes, captionNodes, summary);
      publishBlogSummary(summary, requestedAt);
    }, { intervalMs: POLL_INTERVAL_MS });
    const refreshBlogTopbar = () => poll.refresh();
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        void refreshBlogTopbar();
      }
    };

    const previousRefresh = window.refreshBlogTopbar;
    window.refreshBlogTopbar = refreshBlogTopbar;
    void refreshBlogTopbar();
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      disposed = true;
      poll.dispose();
      if (window.refreshBlogTopbar === refreshBlogTopbar) window.refreshBlogTopbar = previousRefresh;
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
