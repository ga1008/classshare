import { StrictMode, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';

import { ensureIslandRegistry, registerIslandMount } from '@/lib/island-registry';

export type IslandPropsFactory<Props> = (mountPoint: HTMLElement, index: number) => Props;

export type MountReactIslandsOptions<Props> = {
  islandName: string;
  defaultMountIdPrefix?: string;
  getProps: IslandPropsFactory<Props>;
  render: (props: Props) => ReactElement;
};

export function resolveIslandMountId(
  mountPoint: Pick<HTMLElement, 'dataset'>,
  index: number,
  defaultMountIdPrefix: string,
) {
  return mountPoint.dataset.islandId || `${defaultMountIdPrefix}-${index + 1}`;
}

export function mountReactIslands<Props>({
  islandName,
  defaultMountIdPrefix = islandName,
  getProps,
  render,
}: MountReactIslandsOptions<Props>) {
  const mountPoints = Array.from(
    document.querySelectorAll<HTMLElement>(`[data-lanshare-island="${islandName}"]`),
  );
  const registry = ensureIslandRegistry(window);

  mountPoints.forEach((mountPoint, index) => {
    if (mountPoint.dataset.reactMounted === 'true') {
      return;
    }

    mountPoint.dataset.reactMounted = 'true';
    const mountId = resolveIslandMountId(mountPoint, index, defaultMountIdPrefix);
    registerIslandMount(registry, mountId);

    createRoot(mountPoint).render(
      <StrictMode>
        {render(getProps(mountPoint, index))}
      </StrictMode>,
    );
  });
}

export function mountReactIslandsWhenReady<Props>(options: MountReactIslandsOptions<Props>) {
  const mount = () => mountReactIslands(options);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
}
