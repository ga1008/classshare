import { describe, expect, it } from 'vitest';
import { registerIslandMount, type IslandRegistry } from './island-registry';

describe('registerIslandMount', () => {
  it('keeps island mount ids stable and unique', () => {
    const registry: IslandRegistry = {
      version: 'test',
      mounted: ['app-shell'],
    };

    registerIslandMount(registry, 'app-shell');
    registerIslandMount(registry, '  classroom-dashboard  ');

    expect(registry.mounted).toEqual(['app-shell', 'classroom-dashboard']);
  });
});
