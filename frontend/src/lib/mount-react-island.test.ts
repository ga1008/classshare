import { describe, expect, it } from 'vitest';

import { resolveIslandMountId } from './mount-react-island';

describe('resolveIslandMountId', () => {
  it('uses an explicit data-island-id when present', () => {
    expect(resolveIslandMountId({ dataset: { islandId: 'profile-entry' } } as unknown as HTMLElement, 0, 'profile')).toBe(
      'profile-entry',
    );
  });

  it('falls back to a stable prefix and one-based index', () => {
    expect(resolveIslandMountId({ dataset: {} } as unknown as HTMLElement, 2, 'feedback-launcher')).toBe(
      'feedback-launcher-3',
    );
  });
});
