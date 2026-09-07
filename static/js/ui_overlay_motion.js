/** Native overlay presence. Focus, aria and dismissal remain with each caller. */
const operations = new WeakMap();

const milliseconds = (value) => {
    const text = String(value || '').trim();
    return (parseFloat(text) || 0) * (text.endsWith('ms') ? 1 : 1000);
};

function styleDuration(style) {
    const longest = (durations, delays, iterations = '1') => {
        const times = String(durations || '0s').split(',').map(milliseconds);
        const waits = String(delays || '0s').split(',').map(milliseconds);
        const repeats = String(iterations || '1').split(',');
        return Math.max(0, ...times.map((time, index) => {
            const count = Number(repeats[index % repeats.length]);
            return Number.isFinite(count) ? time * count + waits[index % waits.length] : 0;
        }));
    };
    return Math.max(
        longest(style.transitionDuration, style.transitionDelay),
        longest(style.animationDuration, style.animationDelay, style.animationIterationCount),
    );
}

/**
 * Resolve true after this operation completes, false if superseded. CSS uses
 * [data-ui-overlay-state="open"|"closed"] on the root. Only the root and explicit
 * [data-ui-overlay-surface] descendants are observed, never a loading spinner.
 */
export function setOverlayOpen(element, open) {
    const previous = operations.get(element);
    if (previous?.open === open) return previous.promise;
    previous?.cancel();

    const view = element.ownerDocument?.defaultView || globalThis;
    const motion = view.matchMedia?.('(prefers-reduced-motion: reduce)');
    let resolve;
    let timer;
    let settled = false;
    const promise = new Promise((done) => { resolve = done; });
    const operation = { open, promise, cancel: () => finish(false) };
    const finish = (completed) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        motion?.removeEventListener?.('change', onMotionChange);
        if (operations.get(element) === operation) operations.delete(element);
        if (completed && !open) element.hidden = true;
        resolve(completed);
    };
    const onMotionChange = (event) => { if (event.matches) finish(true); };
    operations.set(element, operation);

    if (open && element.hidden) {
        element.dataset.uiOverlayState = 'closed';
        element.hidden = false;
        // Establish the closed first frame before transitioning from display:none.
        view.getComputedStyle(element).opacity;
        element.querySelectorAll('[data-ui-overlay-surface]').forEach((surface) => {
            view.getComputedStyle(surface).opacity;
        });
    }
    element.dataset.uiOverlayState = open ? 'open' : 'closed';
    if (motion?.matches || (!open && element.hidden)) {
        finish(true);
        return promise;
    }

    const surfaces = [element, ...element.querySelectorAll('[data-ui-overlay-surface]')];
    let duration = 0;
    let canObserveAnimations = true;
    const animations = [];
    surfaces.forEach((surface) => {
        duration = Math.max(duration, styleDuration(view.getComputedStyle(surface)));
        if (typeof surface.getAnimations !== 'function') {
            canObserveAnimations = false;
            return;
        }
        surface.getAnimations().forEach((animation) => {
            const endTime = animation.effect?.getComputedTiming().endTime;
            if (Number.isFinite(endTime) && animation.playState !== 'finished' && animation.playState !== 'idle') {
                animations.push(animation.finished);
            }
        });
    });
    if (!duration || (canObserveAnimations && !animations.length)) {
        finish(true);
        return promise;
    }
    motion?.addEventListener?.('change', onMotionChange);
    // The timeout is a fallback for detached nodes and engines that omit end events.
    timer = setTimeout(() => finish(true), duration + 34);
    if (canObserveAnimations) Promise.allSettled(animations).then(() => finish(true));
    return promise;
}
