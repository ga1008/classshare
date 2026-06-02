import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import { readIslandPayload } from './typed-island-payload';

function fakeMountPoint(payloadText: string | null) {
  return {
    querySelector: () => (payloadText === null ? null : { textContent: payloadText }),
  } as unknown as HTMLElement;
}

describe('readIslandPayload', () => {
  it('validates parsed island payloads with the provided schema', () => {
    const payload = readIslandPayload(
      fakeMountPoint('{"title":"Entry","count":2}'),
      '[data-payload]',
      z.object({ title: z.string(), count: z.number() }),
    );

    expect(payload).toEqual({ title: 'Entry', count: 2 });
  });
});
