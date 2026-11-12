/** Display-only server clock. Decisions and deadlines remain on the server. */
export function createAttendanceClock({ active, onTick }) {
    let serverAnchor = null, monotonicAnchor = 0, disposed = false;
    const now = () => serverAnchor == null ? null : serverAnchor + Math.max(0, performance.now() - monotonicAnchor);
    const tick = () => { if (!disposed && active() && now() != null) onTick(now()); };
    const timer = window.setInterval(tick, 1000);
    return {
        now,
        sync(value) {
            if (disposed || value == null || !Number.isFinite(Number(value))) return;
            // A slow earlier response must never move the displayed clock back.
            serverAnchor = Math.max(Number(value), now() ?? Number(value));
            monotonicAnchor = performance.now(); tick();
        },
        tick,
        destroy() { disposed = true; window.clearInterval(timer); },
    };
}
