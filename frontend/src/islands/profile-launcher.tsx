import { AvatarActionLink } from '@/components/action-entry';
import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function ProfileLauncher({ label }: { label: string }) {
  return (
    <AvatarActionLink
      href="/profile"
      className="profile-entry-button"
      avatarClassName="profile-entry-button__avatar"
      title={label}
      aria-label={label}
    />
  );
}

mountReactIslandsWhenReady({
  islandName: 'profile-launcher',
  getProps: (mountPoint) => ({
    label: mountPoint.dataset.label || '打开个人中心',
  }),
  render: (props) => <ProfileLauncher {...props} />,
});
