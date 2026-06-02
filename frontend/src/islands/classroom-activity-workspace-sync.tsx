import { mountReactIslandsWhenReady } from '@/lib/mount-react-island';

function ClassroomActivityWorkspaceIsland() {
  return <span aria-hidden="true" data-classroom-activity-workspace-sync style={{ display: 'none' }} />;
}

mountReactIslandsWhenReady({
  islandName: 'classroom-activity-workspace-sync',
  render: () => <ClassroomActivityWorkspaceIsland />,
  getProps: () => ({}),
});
