import { CircleAlert } from 'lucide-react';

import { IconActionButton } from '@/components/action-entry';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function FeedbackLauncher({ label }: { label: string }) {
  return (
    <IconActionButton
      className="feedback-entry-button"
      data-open-feedback
      title={label}
      aria-label={label}
      icon={<CircleAlert size={19} strokeWidth={2} />}
    >
      {null}
    </IconActionButton>
  );
}

mountReactIslandsWhenReady({
  islandName: 'feedback-launcher',
  getProps: (mountPoint) => ({
    label: mountPoint.dataset.label || '打开问题反馈',
  }),
  render: (props) => <FeedbackLauncher {...props} />,
});
