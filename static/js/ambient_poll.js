/** Bounded polling for ambient GET badges. Domain owners keep response state. */
export function createAmbientPoll(task, { intervalMs, timeoutMs = 8000, active = () => !document.hidden }) {
    let timer = null, deadline = null, pending = null, controller = null, cancelWait = null;
    let disposed = false, failures = 0, notBefore = 0;
    const delay = value => Math.round(value * (0.9 + Math.random() * 0.2));
    const clearScheduled = () => { window.clearTimeout(timer); timer = null; };
    const schedule = milliseconds => {
        clearScheduled();
        if (!disposed) timer = window.setTimeout(() => { timer = null; void refresh(); }, Math.max(0, milliseconds));
    };

    function refresh() {
        if (disposed) return Promise.resolve();
        if (pending) return pending;
        if (!active()) {
            schedule(Math.max(delay(intervalMs), notBefore - Date.now()));
            return Promise.resolve();
        }
        if (Date.now() < notBefore) {
            schedule(notBefore - Date.now());
            return Promise.resolve();
        }
        clearScheduled();
        const request = new AbortController();
        controller = request;
        const timeout = new Promise((_, reject) => {
            cancelWait = reject;
            deadline = window.setTimeout(() => {
                request.abort();
                reject(new Error('Ambient request timed out'));
            }, timeoutMs);
        });
        pending = Promise.race([Promise.resolve().then(() => task(request.signal)), timeout])
            .then(() => { failures = 0; notBefore = 0; }, error => {
                if (disposed) return;
                failures = Math.min(failures + 1, 4);
                const retryAfter = Number(error?.retryAfterSeconds);
                const retryMs = Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter * 1000 : 0;
                notBefore = Date.now() + Math.max(retryMs, delay(Math.min(intervalMs * 2 ** failures, 120000)));
            })
            .finally(() => {
                window.clearTimeout(deadline); deadline = null;
                pending = null; controller = null; cancelWait = null;
                schedule(Math.max(0, notBefore - Date.now()) || delay(intervalMs));
            });
        return pending;
    }

    return {
        refresh,
        start: (initialDelayMs = 0) => schedule(initialDelayMs),
        dispose() {
            disposed = true;
            clearScheduled(); window.clearTimeout(deadline); deadline = null;
            controller?.abort(); cancelWait?.(new Error('Ambient owner disposed'));
        },
        get inFlight() { return pending !== null; },
    };
}
