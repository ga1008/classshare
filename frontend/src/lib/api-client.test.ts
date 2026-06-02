import { afterEach, describe, expect, it, vi } from 'vitest';
import { z } from 'zod';

import { ApiError, apiRequest } from './api-client';

const okResponse = (payload: unknown, init: ResponseInit = {}) =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'content-type': 'application/json' },
    ...init,
  });

describe('apiRequest', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends same-origin JSON requests and validates the response', async () => {
    const fetchMock = vi.fn(async () => okResponse({ ok: true, count: 2 }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await apiRequest(
      '/api/example',
      z.object({ ok: z.boolean(), count: z.number() }),
      { method: 'POST', body: { filter: 'today' } },
    );

    expect(result).toEqual({ ok: true, count: 2 });
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      RequestInfo | URL,
      RequestInit & { headers: Headers },
    ];
    expect(init.credentials).toBe('same-origin');
    expect(init.body).toBe(JSON.stringify({ filter: 'today' }));
    expect(init.headers.get('Content-Type')).toBe('application/json');
    expect(init.headers.get('Accept')).toBe('application/json');
  });

  it('preserves FormData bodies without forcing JSON content type', async () => {
    const fetchMock = vi.fn(async () => okResponse({ uploaded: true }));
    vi.stubGlobal('fetch', fetchMock);
    const body = new FormData();
    body.set('file', new Blob(['x']), 'demo.txt');

    await apiRequest('/api/upload', z.object({ uploaded: z.boolean() }), { method: 'POST', body });

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      RequestInfo | URL,
      RequestInit & { headers: Headers },
    ];
    expect(init.body).toBe(body);
    expect(init.headers.has('Content-Type')).toBe(false);
    expect(init.headers.get('Accept')).toBe('application/json');
  });

  it('throws ApiError with backend detail when the response is not ok', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: '没有权限' }), {
          status: 403,
          headers: { 'content-type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(apiRequest('/api/forbidden', z.object({}), { method: 'GET' })).rejects.toMatchObject({
      name: 'ApiError',
      message: '没有权限',
      status: 403,
    } satisfies Partial<ApiError>);
  });
});
