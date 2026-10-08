/** Native dialog close/returnValue/focus stay synchronous and browser-owned.
 * Only disposable DOM/iframe cleanup waits for the actual CSS exit. */
const exits = new WeakMap();

export function finishNativeDialogClose(dialog, cleanup) {
    exits.get(dialog)?.cancel();
    if (dialog.open) return Promise.resolve(false);
    let resolve, timer, settled = false;
    const promise = new Promise(done => { resolve = done; });
    const finish = completed => {
        if (settled) return;
        settled = true; clearTimeout(timer);
        if (exits.get(dialog) === operation) exits.delete(dialog);
        const current = completed && !dialog.open;
        try { if (current) cleanup(); } finally { resolve(current); }
    };
    const operation = { cancel: () => finish(false) };
    exits.set(dialog, operation);
    const animations = (dialog.getAnimations?.() || []).filter(animation =>
        Number.isFinite(animation.effect?.getComputedTiming().endTime)
        && !['finished', 'idle'].includes(animation.playState));
    if (!animations.length) finish(true);
    else {
        const duration = Math.max(...animations.map(animation => animation.effect.getComputedTiming().endTime));
        timer = setTimeout(() => finish(true), Math.min(duration, 10000) + 34);
        Promise.allSettled(animations.map(animation => animation.finished)).then(() => finish(true));
    }
    return promise;
}
