import { enhanceShell } from './shells.js';

const OWNER = Symbol.for('lanshare.lq.navbar-shell');

/** Shared navigation presentation. The caller owns its page lifecycle. */
export function enhanceNavbarShell(topbar, { shellFactory = enhanceShell, document: doc = topbar?.ownerDocument } = {}) {
    if (!topbar) return null;
    if (topbar[OWNER]) return topbar[OWNER];
    const win = doc.defaultView;
    const options = doc.documentElement.dataset.lqShellFallback === 'inline'
        ? { paneGuards: { actions: { keepOpen: true } } } : undefined;
    const shell = shellFactory(topbar, options);
    let destroyed = false, feedbackObserver = null;
    // The existing feedback controller owns its body modal and scroll lock.
    // Release our native pane before its original click arrives, exactly once.
    const handoffFeedback = event => {
        const action = event.target.closest?.('[data-open-feedback]');
        const pane = topbar.querySelector('[data-lq-pane="actions"]');
        const modal = doc.getElementById('feedback-modal');
        if (destroyed || event.defaultPrevented || !action || !topbar.contains(action) || !modal || !pane?.matches(':modal')) return;
        const returnTrigger = topbar.querySelector('[data-lq-pane-open="actions"]');
        shell.openPane('actions')?.destroy();
        feedbackObserver?.disconnect();
        let opened = false;
        const watcher = new win.MutationObserver(() => {
            if (destroyed || feedbackObserver !== watcher) return;
            const visible = !modal.hidden && modal.classList.contains('show') && modal.getAttribute('aria-hidden') !== 'true';
            if (visible) {
                if (!opened) { opened = true; modal.querySelector('[data-feedback-dismiss]')?.focus({ preventScroll: true }); }
            } else if (opened) {
                watcher.disconnect(); feedbackObserver = null;
                if (modal.contains(doc.activeElement) || doc.activeElement === doc.body) {
                    [action, returnTrigger].find(node => node?.isConnected && node.getClientRects().length && !node.closest('[inert]'))?.focus({ preventScroll: true });
                }
            }
        });
        feedbackObserver = watcher;
        watcher.observe(modal, { attributes: true, attributeFilter: ['class', 'hidden', 'aria-hidden'] });
    };
    topbar.addEventListener('click', handoffFeedback, true);
    const handle = { refresh: () => { if (!destroyed) shell.refresh(); }, destroy() {
        if (destroyed) return;
        destroyed = true;
        feedbackObserver?.disconnect(); feedbackObserver = null;
        topbar.removeEventListener('click', handoffFeedback, true);
        shell.destroy();
        if (topbar[OWNER] === handle) delete topbar[OWNER];
    } };
    topbar[OWNER] = handle;
    return handle;
}
