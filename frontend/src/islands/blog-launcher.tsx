import { SquarePen } from 'lucide-react';

import { IconActionLink } from '@/components/action-entry';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function BlogLauncher({ label }: { label: string }) {
  return (
    <IconActionLink
      href="/blog"
      className="message-center-bell"
      title={label}
      aria-label={label}
      icon={<SquarePen size={18} strokeWidth={2} />}
      iconClassName="message-center-bell__icon"
    />
  );
}

mountReactIslandsWhenReady({
  islandName: 'blog-launcher',
  getProps: (mountPoint) => ({
    label: mountPoint.dataset.label || '打开博客中心',
  }),
  render: (props) => <BlogLauncher {...props} />,
});
