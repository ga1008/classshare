export type IslandRegistry = {
  version: string;
  mounted: string[];
};

type IslandWindow = Window & typeof globalThis;

export function ensureIslandRegistry(
  targetWindow: IslandWindow,
  version = 'vite-react-islands-v1',
): IslandRegistry {
  const existing = targetWindow.__LANSHARE_REACT_ISLANDS__;
  if (existing && Array.isArray(existing.mounted)) {
    existing.version = version;
    return existing;
  }

  const registry: IslandRegistry = {
    version,
    mounted: [],
  };
  targetWindow.__LANSHARE_REACT_ISLANDS__ = registry;
  return registry;
}

export function registerIslandMount(registry: IslandRegistry, mountId: string): IslandRegistry {
  const normalizedId = mountId.trim();
  if (normalizedId && !registry.mounted.includes(normalizedId)) {
    registry.mounted.push(normalizedId);
  }
  return registry;
}
