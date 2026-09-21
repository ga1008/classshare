/** The synchronous SSR bootstrap owns the resolver. This module adds listeners
 * and an opt-in, presentation-only bridge; it never reads or writes accounts. */
// A versioned module URL and a relative native import can coexist. Keep the
// installation on the document so either URL still installs listeners once.
const installationKey = Symbol.for('lanshare.theme.installation');
const runtime = root => root?.ownerDocument?.defaultView?.LanShareTheme || globalThis.LanShareTheme;
export const detectCapabilities = (win = window) => win.LanShareTheme.detectCapabilities(win);
export const applyTheme = (input, root = document.documentElement) => runtime(root)?.applyTheme(input, root);
export const resolveTheme = (input, root = document.documentElement) => runtime(root)?.resolveTheme(input);

export function initTheme(documentRoot = document) {
    if (documentRoot[installationKey]) return documentRoot[installationKey];
    const win = documentRoot.defaultView;
    const root = documentRoot.documentElement;
    const core = runtime(root);
    if (!win || !core || root.getAttribute('data-theme') !== 'lanshare') return null;
    const cleanups = [];
    const frames = new Map();
    const origin = win.location.origin;
    let disposed = false;

    // No selectors for arbitrary/user-content frames. Both endpoints must be
    // this app's theme runtime and the parent explicitly marks each app frame.
    function allowed(frame) {
        if (frame?.getAttribute('data-lq-theme-bridge') !== 'app' || frame.hasAttribute('srcdoc') || frame.hasAttribute('sandbox')) return false;
        try { return new URL(frame.getAttribute('src') || '', win.location.href).origin === origin && !!frame.getAttribute('src'); }
        catch (_) { return false; }
    }
    function send(frame) {
        if (!disposed && frame.isConnected !== false && allowed(frame)) frame.contentWindow?.postMessage({ type: 'lq:theme-sync', preferences: core.readPreferences(root) }, origin);
    }
    function broadcast() { for (const frame of frames.keys()) send(frame); }
    function refresh(preferences = core.readPreferences(root)) {
        if (disposed) return;
        const resolved = core.applyTheme({ preferences, capabilities: core.detectCapabilities(win) }, root);
        root.dispatchEvent(new win.CustomEvent('lq:theme-change', { detail: resolved, bubbles: true }));
        broadcast();
        return resolved;
    }
    function registerFrame(frame) {
        if (disposed || !allowed(frame) || frames.has(frame)) return false;
        const loaded = () => send(frame);
        frame.addEventListener('load', loaded);
        frames.set(frame, () => frame.removeEventListener('load', loaded));
        send(frame);
        return true;
    }
    const onMessage = event => {
        if (disposed || event.origin !== origin || origin === 'null') return;
        if (event.data?.type === 'lq:theme-ready') {
            for (const frame of frames.keys()) if (frame.contentWindow === event.source && allowed(frame)) send(frame);
        } else if (event.data?.type === 'lq:theme-sync' && win.parent !== win && event.source === win.parent) {
            // A same-origin child can inspect its own embedding element. This
            // prevents a theme-aware document being used as a content preview.
            let frame;
            try { frame = win.frameElement; } catch (_) { return; }
            if (!allowed(frame)) return;
            const value = event.data.preferences;
            if (!value || !['teal', 'indigo', 'sky', 'mint', 'violet', 'rose'].includes(value.palette_key) || !['auto', 'light', 'dark'].includes(value.appearance) || !['off', 'tinted'].includes(value.glass)) return;
            refresh({ palette_key: value.palette_key, appearance: value.appearance, glass: value.glass });
        }
    };
    win.addEventListener('message', onMessage);
    cleanups.push(() => win.removeEventListener('message', onMessage));
    for (const query of ['(prefers-color-scheme: dark)', '(prefers-reduced-transparency: reduce)', '(prefers-contrast: more)', '(forced-colors: active)', '(prefers-reduced-motion: reduce)', '(pointer: coarse)']) {
        try {
            const media = win.matchMedia?.(query);
            const change = () => refresh();
            if (media?.addEventListener) { media.addEventListener('change', change); cleanups.push(() => media.removeEventListener('change', change)); }
            else if (media?.addListener) { media.addListener(change); cleanups.push(() => media.removeListener(change)); }
        } catch (_) { /* Missing media APIs keep the conservative bootstrap. */ }
    }
    const connection = win.navigator?.connection;
    const connectionChange = () => refresh();
    if (connection?.addEventListener) { connection.addEventListener('change', connectionChange); cleanups.push(() => connection.removeEventListener('change', connectionChange)); }
    const discover = () => {
        if (disposed) return;
        const current = new Set(documentRoot.querySelectorAll('iframe[data-lq-theme-bridge="app"]'));
        for (const [frame, cleanup] of frames) if (!current.has(frame) || !allowed(frame)) { cleanup(); frames.delete(frame); }
        current.forEach(registerFrame);
    };
    discover();
    if (win.MutationObserver) {
        const observer = new win.MutationObserver(discover);
        observer.observe(root, { subtree: true, childList: true, attributes: true, attributeFilter: ['data-lq-theme-bridge', 'src', 'srcdoc', 'sandbox'] });
        cleanups.push(() => observer.disconnect());
    }
    const api = { refresh, registerFrame, dispose() {
        if (disposed) return;
        disposed = true;
        cleanups.forEach(fn => fn());
        frames.forEach(cleanup => cleanup()); frames.clear();
        if (documentRoot[installationKey] === api) delete documentRoot[installationKey];
    } };
    documentRoot[installationKey] = api;
    refresh();
    if (win.parent !== win && origin !== 'null') win.parent.postMessage({ type: 'lq:theme-ready' }, origin);
    return api;
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initTheme(), { once: true });
    else initTheme();
}
