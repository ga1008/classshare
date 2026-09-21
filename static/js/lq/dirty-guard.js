import { confirm as confirmLeaveDialog } from './dialogs.js';

const registryKey = Symbol.for('lanshare.lq.dirty-guards.v1');
const ownTarget = (node, attribute, doc) => {
    const target = (node.getAttribute(attribute) || doc.querySelector('base[target]')?.getAttribute('target') || '_self').toLowerCase();
    return target === '_self' || (target === '_top' && doc.defaultView === doc.defaultView.top)
        || (target === '_parent' && doc.defaultView === doc.defaultView.parent);
};

function registry(doc) {
    if (doc[registryKey]) return doc[registryKey];
    const win = doc.defaultView;
    const guards = new Map();
    let listening = false, unloading = false, pendingNavigation = null;
    let nativeNavigation = null, approvedNavigation = null;
    const active = () => [...guards.values()].filter(guard => guard.root.isConnected);
    const dirty = guard => {
        try { return guard.readDirty(); }
        catch (error) { win.reportError?.(error); return true; }
    };
    const onNavigate = event => {
        const source = event.sourceElement;
        const form = source?.tagName === 'FORM' ? source : source?.form;
        nativeNavigation = form?.ownerDocument === doc ? event : null;
    };
    const onUnload = event => {
        // Use the browser's actual navigation source, not a submit-event timer.
        // This covers GET/POST, requestSubmit and submit(), while canceled/AJAX,
        // dialog and new-window forms do not waive this document's protection.
        const submission = nativeNavigation;
        nativeNavigation = null;
        const approved = approvedNavigation;
        approvedNavigation = null;
        if (approved && active().every(guard => !dirty(guard) || approved.some(([owner, revision]) => owner === guard && revision === guard.revision()))) return;
        if (submission && !submission.defaultPrevented && !submission.signal.aborted) return;
        if (!active().some(dirty)) return;
        event.preventDefault();
        event.returnValue = '';
    };
    const refresh = () => {
        const needed = active().some(dirty);
        if (needed !== unloading) {
            unloading = needed;
            win[needed ? 'addEventListener' : 'removeEventListener']('beforeunload', onUnload);
        }
    };
    const onEdit = event => {
        for (const guard of active()) if (guard.root.contains(event.target)) guard.invalidate();
        refresh();
    };
    const onPageShow = () => { approvedNavigation = null; nativeNavigation = null; refresh(); };
    const onClick = event => {
        if (event.defaultPrevented || event.button !== 0 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
        const anchor = event.target.closest?.('a[href]');
        if (!anchor || anchor.hasAttribute('download') || !ownTarget(anchor, 'target', doc)) return;
        let url;
        try { url = new URL(anchor.href, doc.baseURI); } catch { return; }
        if (!['http:', 'https:'].includes(url.protocol) || url.origin !== win.location.origin || url.username || url.password) return;
        const current = new URL(win.location.href);
        if (url.pathname === current.pathname && url.search === current.search && (url.hash || anchor.getAttribute('href')?.startsWith('#'))) return;
        const candidates = active().filter(guard => guard.navigation && dirty(guard));
        if (!candidates.length) return;
        event.preventDefault();
        if (pendingNavigation) return;
        const intent = { canceled: false };
        pendingNavigation = intent;
        const revisions = candidates.map(guard => [guard, guard.revision()]);
        (async () => {
            for (const guard of candidates) if (!await guard.requestLeave({ reason: 'navigate', trigger: anchor, href: url.href })) return;
            if (intent.canceled || candidates.some(guard => !guard.root.isConnected || !guards.has(guard.root))) return;
            if (revisions.some(([guard, revision]) => dirty(guard) && guard.revision() !== revision)) return;
            // New guards/edits cannot silently inherit an earlier decision.
            if (active().some(guard => guard.navigation && dirty(guard) && !candidates.includes(guard))) return;
            approvedNavigation = revisions;
            try { win.location.assign(url.href); } catch (error) { approvedNavigation = null; throw error; }
        })().catch(error => win.reportError?.(error)).finally(() => {
            if (pendingNavigation === intent) pendingNavigation = null;
        });
    };
    const api = {
        guards, refresh,
        connect() {
            if (listening) return;
            listening = true;
            doc.addEventListener('click', onClick);
            doc.addEventListener('input', onEdit, true);
            doc.addEventListener('change', onEdit, true);
            win.navigation?.addEventListener('navigate', onNavigate);
            win.addEventListener('pageshow', onPageShow);
            refresh();
        },
        disconnect() {
            refresh();
            if (guards.size || !listening) return;
            listening = false;
            if (pendingNavigation) pendingNavigation.canceled = true;
            pendingNavigation = null;
            approvedNavigation = null;
            nativeNavigation = null;
            doc.removeEventListener('click', onClick);
            doc.removeEventListener('input', onEdit, true);
            doc.removeEventListener('change', onEdit, true);
            win.navigation?.removeEventListener('navigate', onNavigate);
            win.removeEventListener('pageshow', onPageShow);
        },
    };
    doc[registryKey] = api;
    return api;
}

/** Opt-in controller. isDirty reads the page's authoritative state. Call refresh
 * after non-DOM state changes; it also invalidates pending leave decisions.
 * navigation enables same-origin full-page links; forms are never intercepted.
 * beforeClose plugs into LQ.layer without clearing drafts or changing versions. */
export function bindDirtyGuard(root, options = {}) {
    if (!root || root.nodeType !== 1 || !root.ownerDocument?.defaultView) throw new TypeError('LQ dirty guard needs an Element');
    if (!options || typeof options !== 'object' || Array.isArray(options)
        || Object.keys(options).some(key => !['isDirty', 'confirmLeave', 'navigation', 'title', 'message'].includes(key))) throw new TypeError('Invalid LQ dirty guard options');
    if (typeof options.isDirty !== 'function' || (options.confirmLeave !== undefined && typeof options.confirmLeave !== 'function')
        || (options.navigation !== undefined && typeof options.navigation !== 'boolean')) throw new TypeError('Invalid LQ dirty guard callbacks');
    for (const key of ['title', 'message']) if (options[key] !== undefined && (typeof options[key] !== 'string' || !options[key].trim())) throw new TypeError('Invalid LQ dirty guard text');
    const doc = root.ownerDocument, state = registry(doc);
    if (state.guards.has(root)) return state.guards.get(root).handle;
    const readDirty = () => {
        const value = options.isDirty();
        if (typeof value !== 'boolean') throw new TypeError('LQ isDirty must return a boolean');
        return value;
    };
    readDirty();
    let destroyed = false, revision = 0, pending = null;
    const requestLeave = (context = {}) => {
        if (destroyed || !root.isConnected) return Promise.resolve(false);
        if (!context || typeof context !== 'object' || Array.isArray(context)) throw new TypeError('Invalid leave context');
        if (!readDirty()) return Promise.resolve(true);
        if (pending) return pending.promise;
        const attempt = { revision, prompt: null, resolve: null, promise: null };
        attempt.promise = new Promise(resolve => { attempt.resolve = resolve; });
        pending = attempt;
        Promise.resolve().then(() => {
            if (destroyed || pending !== attempt) return false;
            attempt.prompt = options.confirmLeave
                ? options.confirmLeave(Object.freeze({ ...context, root }))
                : confirmLeaveDialog({ title: options.title || '离开当前编辑？', message: options.message || '当前修改尚未保存，离开后可能丢失。', confirmLabel: '放弃并离开', cancelLabel: '继续编辑', danger: true }, { trigger: context.trigger, parentLayer: context.layer }, doc);
            return attempt.prompt;
        }).then(allowed => {
            if (typeof allowed !== 'boolean') throw new TypeError('LQ confirmLeave must resolve to a boolean');
            attempt.resolve(!destroyed && root.isConnected && allowed && (!readDirty() || revision === attempt.revision));
        }).catch(error => {
            doc.defaultView.reportError?.(error);
            attempt.resolve(false);
        }).finally(() => { if (pending === attempt) pending = null; state.refresh(); });
        return attempt.promise;
    };
    const handle = Object.freeze({
        requestLeave,
        beforeClose: (reason, layer) => requestLeave({ reason, trigger: layer?.trigger, layer }),
        refresh() { if (destroyed) return; revision++; state.refresh(); },
        destroy() {
            if (destroyed) return;
            destroyed = true;
            revision++;
            state.guards.delete(root);
            const attempt = pending;
            pending = null;
            attempt?.resolve(false);
            attempt?.prompt?.destroy?.();
            state.disconnect();
        },
    });
    state.guards.set(root, { root, handle, readDirty, requestLeave, navigation: options.navigation === true,
        invalidate: () => { revision++; }, revision: () => revision });
    state.connect();
    state.refresh();
    return handle;
}
