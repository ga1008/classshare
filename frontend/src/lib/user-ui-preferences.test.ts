import { afterEach, describe, expect, it, vi } from 'vitest';
// This shared SSR module deliberately ships as native JavaScript outside Vite.
// @ts-expect-error Native JavaScript module has no generated declaration file.
import { createPaletteController, normalizePalette } from '../../../static/js/user_ui_preferences.js';

const initial = { palette_key: 'indigo', version: 0, context_token: 'account-a', available: true };
const preferences = (palette_key: string, version: number) => ({ ...initial, palette_key, version });
const deferred = () => {
  let resolve!: (value: ReturnType<typeof preferences>) => void;
  const promise = new Promise<ReturnType<typeof preferences>>(done => { resolve = done; });
  return { promise, resolve };
};
const controllers: Array<{ dispose: () => void }> = [];
const setup = (request: ReturnType<typeof vi.fn>, overrides = {}) => {
  const onPreview = vi.fn();
  const onStatus = vi.fn();
  const controller = createPaletteController({ initial, request, onPreview, onStatus, ...overrides });
  controllers.push(controller);
  return { controller, onPreview, onStatus };
};
afterEach(() => {
  controllers.splice(0).forEach(controller => controller.dispose());
  vi.useRealTimers();
});

describe('account palette synchronization', () => {
  it('coalesces rapid choices and immediately previews only user intent', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockResolvedValue(preferences('rose', 1));
    const { controller, onPreview, onStatus } = setup(request);
    controller.select('sky');
    controller.select('mint');
    controller.select('rose');
    expect(onPreview.mock.calls.map(call => call[0])).toEqual(['sky', 'mint', 'rose']);
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenCalledExactlyOnceWith('PATCH', { palette_key: 'rose', version: 0 });
    expect(controller.snapshot().confirmed).toMatchObject({ palette_key: 'rose', version: 1 });
    expect(onStatus).toHaveBeenLastCalledWith('saved', expect.any(String));
  });

  it('serializes writes and never lets an earlier reply repaint a newer selection', async () => {
    vi.useFakeTimers();
    const first = deferred();
    const request = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(preferences('violet', 2));
    const { controller, onPreview } = setup(request);
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    controller.select('rose');
    controller.select('violet');
    await vi.advanceTimersByTimeAsync(1000);
    expect(request).toHaveBeenCalledTimes(1);
    first.resolve(preferences('mint', 1));
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { palette_key: 'violet', version: 1 });
    expect(onPreview).toHaveBeenLastCalledWith('violet');
    expect(onPreview).toHaveBeenCalledTimes(3);
    expect(controller.snapshot()).toMatchObject({ desired: 'violet', dirty: false, confirmed: { palette_key: 'violet', version: 2 } });
  });

  it('keeps preview on ambiguous failure and refreshes before an explicit retry', async () => {
    vi.useFakeTimers();
    const request = vi.fn()
      .mockRejectedValueOnce(new Error('response lost after server commit'))
      .mockResolvedValueOnce(preferences('mint', 1))
      .mockResolvedValueOnce(preferences('mint', 2));
    const { controller, onPreview, onStatus } = setup(request);
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    expect(controller.snapshot()).toMatchObject({ desired: 'mint', needsRetry: true });
    expect(onStatus).toHaveBeenLastCalledWith('error', expect.any(String));
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledTimes(1);
    controller.retry();
    await vi.advanceTimersByTimeAsync(240);
    expect(request.mock.calls).toEqual([
      ['PATCH', { palette_key: 'mint', version: 0 }], ['GET'], ['PATCH', { palette_key: 'mint', version: 1 }],
    ]);
    expect(onPreview.mock.calls.every(call => call[0] === 'mint')).toBe(true);
    expect(controller.snapshot().needsRetry).toBe(false);
  });

  it('reads another device version on conflict and waits for a new choice', async () => {
    vi.useFakeTimers();
    const request = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('conflict'), { status: 409 }))
      .mockResolvedValueOnce(preferences('sky', 6))
      .mockResolvedValueOnce(preferences('rose', 7));
    const { controller, onPreview, onStatus } = setup(request);
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('GET');
    expect(onPreview).toHaveBeenLastCalledWith('mint');
    expect(onStatus).toHaveBeenLastCalledWith('conflict', expect.any(String));
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledTimes(2);
    controller.select('rose');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { palette_key: 'rose', version: 6 });
    expect(controller.snapshot().confirmed.palette_key).toBe('rose');
  });

  it('uses the latest choice made while recovering an unavailable SSR read', async () => {
    vi.useFakeTimers();
    const refresh = deferred();
    const request = vi.fn().mockReturnValueOnce(refresh.promise).mockResolvedValueOnce(preferences('rose', 4));
    const { controller } = setup(request, { initial: { ...initial, available: false } });
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenCalledExactlyOnceWith('GET');
    controller.select('rose');
    refresh.resolve(preferences('sky', 3));
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { palette_key: 'rose', version: 3 });
    expect(request).toHaveBeenCalledTimes(2);
  });

  it('stops stale-account writes when cookie identity changes', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockRejectedValueOnce(Object.assign(new Error('identity'), { status: 409, code: 'identity_changed' }));
    const { controller, onStatus } = setup(request);
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    expect(controller.snapshot().identityChanged).toBe(true);
    expect(onStatus).toHaveBeenLastCalledWith('identity_changed', expect.any(String));
    controller.select('rose');
    controller.retry();
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it('rejects mismatched identity even during a successful recovery response', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockResolvedValue({ ...preferences('sky', 1), context_token: 'account-b' });
    const { controller } = setup(request, { initial: { ...initial, available: false } });
    controller.select('mint');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenCalledExactlyOnceWith('GET');
    expect(controller.snapshot().identityChanged).toBe(true);
  });

  it('falls back safely when a palette has been removed', () => {
    expect(normalizePalette('retired')).toBe('indigo');
    expect(normalizePalette('rose')).toBe('rose');
  });
});
