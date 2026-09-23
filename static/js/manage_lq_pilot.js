import { enhanceShell } from './lq/shells.js';

const OWNER = Symbol.for('lanshare.manage-lq-pilot');
const LEGACY_COLLAPSE_KEY = 'lanshare:manage-sidebar-collapsed';

/** Presentational adapter only: no fetch, submit, cloning, controller dispatch or URL interception. */
export function initManageLqPilot(doc = document) {
    const body = doc.body;
    if (!body?.matches('.lq-manage-pilot,.lq-manage-shell') || body.classList.contains('manage-embedded-page')) return null;
    if (body[OWNER]) return body[OWNER];
    const sidebar = doc.querySelector('[data-lq-manage-sidebar]');
    const topbar = doc.getElementById('manage-pilot-topbar');
    if (!sidebar || !topbar) return null;
    const win = doc.defaultView, releases = [], saved = new Map();
    const inlineFallback = doc.documentElement.dataset.lqShellFallback === 'inline';
    const remember = (node, key) => { if (!saved.has(node)) saved.set(node, new Map()); const attrs = saved.get(node); if (!attrs.has(key)) attrs.set(key, node.getAttribute(key)); };
    const set = (node, key, value) => { remember(node, key); if (value == null) node.removeAttribute(key); else node.setAttribute(key, value); };
    const listen = (node, key, callback, options) => { node.addEventListener(key, callback, options); releases.push(() => node.removeEventListener(key, callback, options)); };
    const search = doc.getElementById('manageNavSearch'), nav = doc.getElementById('manageNav'), collapse = doc.getElementById('sidebarCollapseBtn');
    let disposed = false, navHandle = null, topbarHandle = null, frame = null;

    // Register before opening a core layer: first Escape clears the active menu search.
    listen(doc, 'keydown', event => {
        if (event.defaultPrevented || event.key !== 'Escape' || event.target !== search || !search.value) return;
        event.preventDefault(); event.stopImmediatePropagation();
        search.value = ''; search.dispatchEvent(new win.Event('input', { bubbles: true }));
    }, true);

    const failure = () => {
        const status = doc.querySelector('[data-lq-manage-shell-status]');
        if (status) { set(status, 'hidden', null); status.textContent = '导航增强暂不可用，入口仍可直接使用。'; }
    };
    try { navHandle = enhanceShell(sidebar, { paneGuards: inlineFallback ? { nav: { keepOpen: true } } : {}, onError: failure }); } catch { failure(); }
    try { topbarHandle = enhanceShell(topbar, { paneGuards: inlineFallback ? { actions: { keepOpen: true } } : {}, onError: failure }); } catch { failure(); }

    // A legacy collapse preference is read only as the initial adapter value. New writes
    // are isolated by role:id; another account's pilot preference is never overwritten.
    const identity = sidebar.dataset.lqManageIdentity;
    const storageKey = identity && !inlineFallback ? `lq.manage-sidebar:${JSON.stringify([identity, 'manage', 'collapsed'])}` : null;
    let compact = false;
    if (storageKey) try {
        const current = win.localStorage.getItem(storageKey);
        compact = current === '1' || current === null && win.localStorage.getItem(LEGACY_COLLAPSE_KEY) === '1';
    } catch { /* Storage is optional; the visible navigation remains usable. */ }
    const syncCompact = () => {
        set(sidebar, 'data-lq-compact', String(compact));
        if (collapse) {
            set(collapse, 'aria-expanded', String(!compact));
            set(collapse, 'aria-label', compact ? '展开菜单' : '收起菜单');
            set(collapse, 'title', compact ? '展开菜单' : '收起菜单');
        }
    };
    syncCompact();
    if (collapse) listen(collapse, 'click', () => {
        compact = !compact; syncCompact();
        if (storageKey) try { win.localStorage.setItem(storageKey, compact ? '1' : '0'); } catch { /* Optional. */ }
    });
    if (search) listen(search, 'input', () => {
        if (search.value && compact) { compact = false; syncCompact(); }
    });

    // Give the original click to the original controller once. Synchronously release
    // only our active pane before it opens a legacy modal or native file picker;
    // destroy() does not schedule a return-focus that could steal the new modal's focus.
    // Materials owns document-capture handlers that stop propagation. Window
    // capture releases our pane first without intercepting the business click.
    listen(win, 'click', event => {
        const action = event.target.closest?.('.manage-context-actions button,.manage-context-actions a[href],.lq-manage-utility a[href],.lq-manage-utility [data-open-feedback]');
        if (!action || !topbar.contains(action) || action.matches(':disabled,[aria-disabled="true"],#materials-create-menu-btn,#materials-upload-menu-btn')) return;
        const panel = topbar.querySelector('[data-lq-pane="actions"]');
        if (!topbarHandle || !panel?.matches(':modal')) return;
        topbarHandle.openPane('actions')?.destroy();
    }, true);

    // Reflect native details state for existing inspection/automation hooks; details
    // and enhanceShell remain the only owners of opening and single-domain selection.
    for (const group of sidebar.querySelectorAll('[data-lq-nav-group]')) {
        const summary = group.querySelector('summary');
        const reflect = () => { if (summary) set(summary, 'aria-expanded', String(group.open)); };
        reflect(); listen(group, 'toggle', reflect);
    }
    const centerActive = () => {
        if (disposed || !nav || !nav.getClientRects().length) return;
        const active = nav.querySelector('.manage-nav-item.active');
        if (!active?.getClientRects().length) return;
        const bounds = nav.getBoundingClientRect(), item = active.getBoundingClientRect();
        nav.scrollTop = Math.max(0, Math.min(nav.scrollHeight - nav.clientHeight, nav.scrollTop + item.top - bounds.top - (nav.clientHeight - item.height) / 2));
    };
    frame = win.requestAnimationFrame(centerActive);
    listen(win, 'pageshow', event => { if (event.persisted) { navHandle?.refresh(); topbarHandle?.refresh(); centerActive(); } });

    const handle = { destroy() {
        if (disposed) return;
        disposed = true; if (frame !== null) win.cancelAnimationFrame(frame);
        releases.reverse().forEach(release => release());
        topbarHandle?.destroy(); navHandle?.destroy();
        for (const [node, attrs] of saved) for (const [key, value] of attrs) { if (value === null) node.removeAttribute(key); else node.setAttribute(key, value); }
        delete body[OWNER];
    } };
    body[OWNER] = handle;
    listen(win, 'pagehide', event => { if (!event.persisted) handle.destroy(); });
    return handle;
}

if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => initManageLqPilot(), { once: true });
    else initManageLqPilot();
}
