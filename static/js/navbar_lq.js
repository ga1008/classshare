import { enhanceNavbarShell } from './lq/navbar-shell.js';
import { enhanceDock } from './lq/shells.js';

const OWNER = Symbol.for('lanshare.navbar-lq');

/** No business fetches or navigation interception; keep SSR links and nodes. */
export function initNavbarLq(doc = document) {
    const topbar = doc.querySelector('[data-lq-navbar-topbar]');
    if (!topbar) return null;
    if (topbar[OWNER]) return topbar[OWNER];
    const win = doc.defaultView, shell = enhanceNavbarShell(topbar);
    const dockRoot = doc.querySelector('[data-navbar-dock]');
    const contentRoot = doc.querySelector('[data-lq-navbar-content]');
    let dock;
    try { dock = dockRoot && contentRoot ? enhanceDock(dockRoot, { contentRoot }) : null; }
    catch (error) { shell.destroy(); throw error; }
    let destroyed = false;
    const onPageHide = event => { if (!event.persisted) destroy(); };
    const onPageShow = () => { if (!destroyed) { shell.refresh(); dock?.refresh(); } };
    const observer = new win.MutationObserver(() => { if (!topbar.isConnected) destroy(); });
    function destroy() {
        if (destroyed) return;
        destroyed = true; observer.disconnect();
        win.removeEventListener('pagehide', onPageHide); win.removeEventListener('pageshow', onPageShow);
        dock?.destroy(); shell.destroy();
        if (topbar[OWNER] === handle) delete topbar[OWNER];
    }
    const handle = { destroy };
    topbar[OWNER] = handle;
    observer.observe(doc.documentElement, { childList: true, subtree: true });
    win.addEventListener('pagehide', onPageHide); win.addEventListener('pageshow', onPageShow);
    return handle;
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initNavbarLq(), { once: true });
    else initNavbarLq();
}
