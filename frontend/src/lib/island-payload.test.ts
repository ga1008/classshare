import { describe, expect, it } from 'vitest';

import { readIslandJsonPayload } from './island-payload';

function fakeMountPoint(payloadText: string | null) {
  return {
    querySelector: () => (payloadText === null ? null : { textContent: payloadText }),
  } as unknown as HTMLElement;
}

describe('readIslandJsonPayload', () => {
  it('parses JSON payloads from a script tag', () => {
    const payload = readIslandJsonPayload(fakeMountPoint('{"title":"Entry","count":2}'), '[data-payload]');

    expect(payload).toEqual({ title: 'Entry', count: 2 });
  });

  it('raises when the payload node is missing', () => {
    expect(() => readIslandJsonPayload(fakeMountPoint(null), '[data-payload]')).toThrow('Missing island payload');
  });
});
