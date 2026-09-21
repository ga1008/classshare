import { StrictMode, type ReactElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';

import { ensureIslandRegistry, registerIslandMount } from '@/lib/island-registry';

export type IslandPropsFactory<Props> = (mountPoint: HTMLElement, index: number) => Props;

export type MountReactIslandsOptions<Props> = {
  islandName: string;
  defaultMountIdPrefix?: string;
  getProps: IslandPropsFactory<Props>;
  render: (props: Props) => ReactElement;
};

export type ReactIslandMount = { dispose: () => void };

type IslandOwner = {
  mountPoint: HTMLElement;
  root?: Root;
  disposed: boolean;
};

const owners = new WeakMap<HTMLElement, IslandOwner>();

function disposeOwner(owner: IslandOwner) {
  if (owner.disposed) return;
  owner.disposed = true;
  try {
    owner.root?.unmount();
  } finally {
    // A retained disposer must never clear a later mount's marker.
    if (owners.get(owner.mountPoint) === owner) {
      owners.delete(owner.mountPoint);
      delete owner.mountPoint.dataset.reactMounted;
    }
  }
}

/** Call before removing an island's host. This only unmounts React-owned work,
 * not native page controllers that happen to share the document. */
export function unmountReactIsland(mountPoint: HTMLElement): boolean {
  const owner = owners.get(mountPoint);
  if (!owner || owner.disposed) return false;
  disposeOwner(owner);
  return true;
}

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
}: MountReactIslandsOptions<Props>): ReactIslandMount {
  const mountPoints = Array.from(
    document.querySelectorAll<HTMLElement>(`[data-lanshare-island="${islandName}"]`),
  );
  const registry = ensureIslandRegistry(window);
  const created: IslandOwner[] = [];
  const dispose = () => {
    // Run every cleanup even if a consumer's synchronous cleanup fails.
    let failure: unknown;
    let failed = false;
    for (const owner of created) {
      try { disposeOwner(owner); } catch (error) { failed = true; failure = error; }
    }
    if (failed) throw failure;
  };

  try {
    mountPoints.forEach((mountPoint, index) => {
      if (owners.has(mountPoint) || mountPoint.dataset.reactMounted === 'true') {
        return;
      }

      const owner: IslandOwner = { mountPoint, disposed: false };
      owners.set(mountPoint, owner);
      created.push(owner);
      mountPoint.dataset.reactMounted = 'true';
      const element = render(getProps(mountPoint, index));
      owner.root = createRoot(mountPoint);
      owner.root.render(<StrictMode>{element}</StrictMode>);

      const mountId = resolveIslandMountId(mountPoint, index, defaultMountIdPrefix);
      registerIslandMount(registry, mountId);
    });
  } catch (error) {
    // Preserve the mounting failure while releasing earlier roots in this batch.
    try { dispose(); } catch { /* the original failure is the actionable one */ }
    throw error;
  }
  return { dispose };
}

export function mountReactIslandsWhenReady<Props>(options: MountReactIslandsOptions<Props>): ReactIslandMount {
  let disposed = false;
  let mounted: ReactIslandMount | undefined;
  const mount = () => {
    if (disposed || mounted) return;
    document.removeEventListener('DOMContentLoaded', mount);
    mounted = mountReactIslands(options);
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true });
  } else {
    mount();
  }
  return {
    dispose: () => {
      if (disposed) return;
      disposed = true;
      document.removeEventListener('DOMContentLoaded', mount);
      mounted?.dispose();
    },
  };
}
