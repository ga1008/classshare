import { afterEach, describe, expect, it, vi } from 'vitest';
// This shared SSR module deliberately ships as native JavaScript outside Vite.
// @ts-expect-error Native JavaScript module has no generated declaration file.
import { createPaletteController, createUIPreferencesController, normalizePalette } from '../../../static/js/user_ui_preferences.js';

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

  it('recovers a lost successful reply with GET and no duplicate version bump', async () => {
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
      ['PATCH', { palette_key: 'mint', version: 0 }], ['GET'],
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

describe('three-field preference intent and whole-row CAS', () => {
  const start = { ...initial, appearance: 'auto', glass: 'tinted' };
  const saved = (changes: Record<string, unknown> = {}, version = 1) => ({ ...start, ...changes, version });
  const make = (request: ReturnType<typeof vi.fn>, overrides = {}) => {
    const onPreview = vi.fn();
    const onStatus = vi.fn();
    const controller = createUIPreferencesController({ initial: start, request, onPreview, onStatus, ...overrides });
    controllers.push(controller);
    return { controller, onPreview, onStatus };
  };
  it('coalesces all fields into one row-CAS write and accepts the sixth palette', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockResolvedValue(saved({ palette_key: 'teal', appearance: 'dark', glass: 'off' }));
    const { controller } = make(request);
    controller.select('palette_key', 'teal');
    controller.select('appearance', 'dark');
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenCalledExactlyOnceWith('PATCH', { palette_key: 'teal', appearance: 'dark', glass: 'off', version: 0 });
    expect(controller.snapshot()).toMatchObject({ dirty: false, confirmed: { version: 1, appearance: 'dark', glass: 'off' } });
    expect(normalizePalette('teal')).toBe('teal');
  });
  it('preserves newer field intent while merging non-dirty server fields', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const request = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValueOnce(saved({ palette_key: 'sky', appearance: 'light', glass: 'off' }, 3));
    const { controller, onPreview } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    controller.select('appearance', 'light');
    controller.select('glass', 'off');
    pending.resolve(saved({ palette_key: 'sky', appearance: 'dark' }, 2));
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { appearance: 'light', glass: 'off', version: 2 });
    expect(onPreview).toHaveBeenLastCalledWith({ palette_key: 'sky', appearance: 'light', glass: 'off', backdrop: 'scene', backdrop_color: '#ffffff' });
    expect(controller.snapshot().dirty).toBe(false);
  });
  it('does not confirm a conflicted appearance when the user chooses glass', async () => {
    vi.useFakeTimers();
    const request = vi.fn()
      .mockRejectedValueOnce(Object.assign(new Error('conflict'), { status: 409 }))
      .mockResolvedValueOnce(saved({ appearance: 'light' }, 7))
      .mockResolvedValueOnce(saved({ appearance: 'light', glass: 'off' }, 8))
      .mockResolvedValueOnce(saved({ appearance: 'dark', glass: 'off' }, 9));
    const { controller, onStatus } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    expect(controller.snapshot().conflicts).toEqual({ appearance: 'light' });
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { glass: 'off', version: 7 });
    expect(controller.snapshot()).toMatchObject({ desired: { appearance: 'dark' }, conflicts: { appearance: 'light' } });
    expect(onStatus).toHaveBeenLastCalledWith('conflict', expect.stringContaining('浅色'));
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledTimes(3);
    controller.retry('appearance');
    await vi.advanceTimersByTimeAsync(240);
    expect(request).toHaveBeenLastCalledWith('PATCH', { appearance: 'dark', version: 8 });
    expect(controller.snapshot()).toMatchObject({ dirty: false, conflicts: {} });
  });
  it('also waits after a different-field row version conflict', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockRejectedValueOnce(Object.assign(new Error('conflict'), { status: 409 }))
      .mockResolvedValueOnce(saved({ palette_key: 'rose' }, 4));
    const { controller } = make(request);
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(2240);
    expect(request.mock.calls).toEqual([['PATCH', { glass: 'off', version: 0 }], ['GET']]);
    expect(controller.snapshot()).toMatchObject({ desired: { palette_key: 'rose', glass: 'off' }, conflicts: { glass: 'tinted' } });
  });
  it('keeps an unresolved conflict visible throughout an unrelated slow save', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const request = vi.fn().mockRejectedValueOnce(Object.assign(new Error('conflict'), { status: 409 }))
      .mockResolvedValueOnce(saved({ appearance: 'light' }, 7)).mockReturnValueOnce(pending.promise);
    const { controller, onStatus } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(2000);
    expect(controller.snapshot()).toMatchObject({ busy: true, conflicts: { appearance: 'light' } });
    expect(onStatus).toHaveBeenLastCalledWith('conflict', expect.stringContaining('浅色'));
    pending.resolve(saved({ appearance: 'light', glass: 'off' }, 8));
    await vi.advanceTimersByTimeAsync(240);
  });
  it('refreshes older conflict values when a later whole-row GET finds another update', async () => {
    vi.useFakeTimers();
    const conflict = Object.assign(new Error('conflict'), { status: 409 });
    const request = vi.fn().mockRejectedValueOnce(conflict).mockResolvedValueOnce(saved({ appearance: 'light' }, 7))
      .mockRejectedValueOnce(conflict).mockResolvedValueOnce(saved({ appearance: 'auto' }, 8));
    const { controller, onStatus } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(240);
    expect(controller.snapshot()).toMatchObject({ confirmed: { appearance: 'auto', version: 8 }, conflicts: { appearance: 'auto', glass: 'tinted' } });
    expect(onStatus).toHaveBeenLastCalledWith('conflict', expect.stringContaining('跟随系统'));
  });
  it('does not overwrite a different value discovered after an ambiguous failure', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValueOnce(saved({ appearance: 'light' }, 4));
    const { controller } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    controller.retry('appearance');
    await vi.advanceTimersByTimeAsync(2240);
    expect(request.mock.calls).toEqual([['PATCH', { appearance: 'dark', version: 0 }], ['GET']]);
    expect(controller.snapshot()).toMatchObject({ desired: { appearance: 'dark' }, conflicts: { appearance: 'light' } });
  });
  it('retries a failed request only after GET proves the field is unchanged', async () => {
    vi.useFakeTimers();
    const request = vi.fn().mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(saved({ palette_key: 'rose' }, 4))
      .mockResolvedValueOnce(saved({ palette_key: 'rose', glass: 'off' }, 5));
    const { controller } = make(request);
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(240);
    controller.retry('glass');
    await vi.advanceTimersByTimeAsync(240);
    expect(request.mock.calls).toEqual([['PATCH', { glass: 'off', version: 0 }], ['GET'], ['PATCH', { glass: 'off', version: 4 }]]);
  });
  it('keeps every queued field stopped after an identity change', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const request = vi.fn().mockReturnValueOnce(pending.promise);
    const { controller, onPreview } = make(request);
    controller.select('appearance', 'dark');
    await vi.advanceTimersByTimeAsync(240);
    controller.select('glass', 'off');
    pending.resolve({ ...saved({ appearance: 'dark' }), context_token: 'other-account' });
    await vi.advanceTimersByTimeAsync(240);
    const calls = onPreview.mock.calls.length;
    controller.select('palette_key', 'rose');
    controller.retry('glass');
    await vi.advanceTimersByTimeAsync(2000);
    expect(controller.snapshot().identityChanged).toBe(true);
    expect(request).toHaveBeenCalledTimes(1);
    expect(onPreview).toHaveBeenCalledTimes(calls);
  });
  it('does not continue recovery or repaint after disposal', async () => {
    vi.useFakeTimers();
    const pending = deferred();
    const request = vi.fn().mockReturnValueOnce(pending.promise);
    const { controller, onPreview } = make(request, { initial: { ...start, available: false } });
    controller.select('glass', 'off');
    await vi.advanceTimersByTimeAsync(240);
    controller.dispose();
    pending.resolve(saved({ palette_key: 'rose' }, 4));
    await vi.advanceTimersByTimeAsync(2000);
    expect(request).toHaveBeenCalledExactlyOnceWith('GET');
    expect(onPreview).toHaveBeenCalledTimes(1);
  });
});
