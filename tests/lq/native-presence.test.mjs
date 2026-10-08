import { afterEach, expect, it, vi } from 'vitest';
import { finishNativeDialogClose } from '../../static/js/lq/native-presence.js';

afterEach(() => vi.useRealTimers());

function dialogFixture() {
    let complete;
    const animation = { playState: 'running', effect: { getComputedTiming: () => ({ endTime: 220 }) },
        finished: new Promise(resolve => { complete = resolve; }) };
    return { dialog: { open: false, getAnimations: () => [animation] }, complete };
}

it('keeps preview contents alive through native exit then releases them once', async () => {
    vi.useFakeTimers();
    const { dialog, complete } = dialogFixture(), cleanup = vi.fn();
    const pending = finishNativeDialogClose(dialog, cleanup);
    expect(cleanup).not.toHaveBeenCalled();
    complete(); expect(await pending).toBe(true);
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
});

it('does not clear the iframe of a quickly reopened native dialog', async () => {
    vi.useFakeTimers();
    const { dialog, complete } = dialogFixture(), cleanup = vi.fn();
    const pending = finishNativeDialogClose(dialog, cleanup);
    dialog.open = true; complete();
    expect(await pending).toBe(false); expect(cleanup).not.toHaveBeenCalled();
});

it('supersedes stale exits and bounds cleanup when the engine loses completion', async () => {
    vi.useFakeTimers();
    const { dialog } = dialogFixture(), oldCleanup = vi.fn(), cleanup = vi.fn();
    const old = finishNativeDialogClose(dialog, oldCleanup);
    const current = finishNativeDialogClose(dialog, cleanup);
    expect(await old).toBe(false);
    await vi.advanceTimersByTimeAsync(300);
    expect(await current).toBe(true);
    expect(oldCleanup).not.toHaveBeenCalled(); expect(cleanup).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
});

it('off and unsupported engines keep native closing and cleanup immediate', async () => {
    const cleanup = vi.fn();
    expect(await finishNativeDialogClose({ open: false }, cleanup)).toBe(true);
    expect(cleanup).toHaveBeenCalledTimes(1);
});
