import { cancelOverlayMotion, setOverlayOpen } from '../ui_overlay_motion.js';
import { findPopoverParent, getPopoverPosition } from '../ui_popover_geometry.js';

const SYSTEM = Symbol.for('lanshare.lq.layer.v1');
const TYPES = new Set(['modal', 'sheet', 'drawer', 'popover', 'menu', 'viewer']);
const FOCUSABLE = 'a[href],button,input:not([type="hidden"]),select,textarea,[tabindex],[contenteditable="true"]';
const active = (handle) => !['closed', 'destroyed'].includes(handle.state);
const element = (value, doc) => value?.nodeType === 1 && value.ownerDocument === doc;
const restoreAttribute = (node, name, value) => value === null ? node.removeAttribute(name) : node.setAttribute(name, value);

/** One coordinator per Document, including imports through different asset URLs. */
export function getLayerSystem(doc = document) {
    if (doc[SYSTEM]) return doc[SYSTEM];
    const system = createLayerSystem(doc);
    Object.defineProperty(doc, SYSTEM, { value: system, configurable: true });
    return system;
}

function createLayerSystem(doc) {
    const view = doc.defaultView;
    // The scroll-lock compensation variable is authored on the root element.
    // An inherited custom property there invalidates every descendant's style;
    // registering it as non-inherited keeps the identical authored value and
    // restoration while confining the write to the root itself.
    try { view?.CSS?.registerProperty?.({ name: '--lq-scrollbar-w', syntax: '<length>', inherits: false, initialValue: '0px' }); }
    catch { /* Already registered or unsupported: compensation is unchanged. */ }
    const stack = [];
    const roots = new WeakMap();
    const records = new WeakMap();
    const blocked = new Map();
    const tabs = new Map();
    const externals = new Set();
    const externalLayers = new Map();
    const externalRecords = new Map();
    const companions = new Map();
    let host;
    let ownsHost = false;
    let observer;
    let observed = false;
    let listening = false;
    let disposed = false;
    let queued = false;
    let repositionFrame = null;
    let pointerStart = null;
    let scrollLock = null;
    let openingSequence = 0;
    let redirectingFocus = false;
    let movingCompanions = false;

    const top = () => [...stack].reverse().find(active) || null;
    const descendantOf = (item, parent) => {
        for (let current = item.parentLayer; current; current = current.parentLayer) if (current === parent) return true;
        return false;
    };
    const descendants = (parent) => stack.filter((item) => descendantOf(item, parent));
    const modal = () => [...stack].reverse().find((item) => active(item) && item.modality === 'modal') || null;
    const externalActive = (item) => item.isActive?.() ?? item.isOpen();
    const externalPresent = (item) => item.isPresent?.() ?? item.isOpen();
    const ordered = (item) => item.mode === 'ordered';
    const topExternal = () => {
        const priority = [...externals].reverse().find((item) => !ordered(item) && item.isOpen());
        if (priority) return priority;
        const candidates = [...externals].filter((item) => ordered(item) && externalPresent(item))
            .sort((a, b) => externalRecords.get(b).order - externalRecords.get(a).order);
        const first = candidates[0];
        return first && externalRecords.get(first).order > (records.get(top())?.order || 0) ? first : null;
    };
    const externalRoots = (owners) => {
        const order = Math.max(0, ...stack.filter((item) => owners.includes(item.root) || owners.includes(records.get(item).surface)).map((item) => records.get(item).order));
        const roots = [...externals].filter((item) => ordered(item) && externalPresent(item) && externalRecords.get(item).order > order).map((item) => item.root()).filter(Boolean);
        return [...roots, ...[...externals].filter((item) => !ordered(item) && item.isOpen() && [...owners, ...roots].some((owner) => owner.contains(item.trigger()))).map((item) => item.root()).filter(Boolean)];
    };
    const dismissExternalItem = (item, reason) => {
        if (reason === 'parent-destroyed') for (const child of [...stack].reverse()) {
            if (records.get(child).externalParent === item) destroyHandle(child, reason);
        }
        item.dismissTop(reason);
    };
    const dismissExternal = (reason) => {
        const item = topExternal();
        if (!item) return false;
        dismissExternalItem(item, reason); return true;
    };
    const dismissOwnedExternals = (handle) => {
        for (const item of externals) if ((externalActive(item) || externalPresent(item)) && (handle.root.contains(item.trigger()) || externalRecords.get(item)?.parentLayer === handle)) dismissExternalItem(item, 'parent-destroyed');
    };
    const scopes = () => {
        const current = modal();
        const owned = current ? stack.slice(stack.indexOf(current)).map((item) => records.get(item).surface) : [];
        return [...owned, ...externalRoots(owned), ...companions.keys()];
    };
    const visible = (node) => element(node, doc) && node.isConnected && !node.closest('[hidden],[inert],[aria-hidden="true"]')
        && !node.matches(':disabled') && view.getComputedStyle(node).visibility !== 'hidden' && node.getClientRects().length > 0;
    const focusable = (scope) => [...scope.querySelectorAll(FOCUSABLE)].filter((node) => visible(node) && node.tabIndex >= 0);
    const focus = (node) => { if (visible(node)) node.focus({ preventScroll: true }); };
    const call = (handle, name, ...args) => {
        try { return records.get(handle).options[name]?.(...args, handle); }
        catch (error) {
            try { records.get(handle).options.onError?.(error, handle); } catch { /* A callback cannot retain coordinator resources. */ }
            return undefined;
        }
    };

    function getPortalHost({ trigger, parentLayer } = {}) {
        if (disposed) throw new Error('Layer system is destroyed');
        const parent = parentLayer || findPopoverParent(stack, trigger, (item) => item.root);
        const context = parent?.root || trigger || doc.activeElement;
        const native = context?.closest?.('dialog[open]');
        if (native) {
            try { if (native.matches(':modal')) return native; }
            catch { if (parent && records.get(parent)?.native) return native; }
        }
        // A caller without a trigger still belongs to an active native modal.
        for (const item of [...stack].reverse()) if (active(item) && records.get(item).native && item.root.open) return item.root;
        if (host?.isConnected) return host;
        host = doc.getElementById('lq-layers');
        if (!host) {
            host = doc.createElement('div'); host.id = 'lq-layers';
            doc.body.append(host); ownsHost = true;
        }
        return host;
    }

    function captureScrollLock() {
        const body = doc.body;
        const keys = ['overflow', 'padding-right', 'position', 'top', 'left', 'width'];
        const saved = keys.map((key) => [key, body.style.getPropertyValue(key), body.style.getPropertyPriority(key)]);
        const oldGap = [doc.documentElement.style.getPropertyValue('--lq-scrollbar-w'), doc.documentElement.style.getPropertyPriority('--lq-scrollbar-w')];
        const gap = Math.max(0, view.innerWidth - doc.documentElement.clientWidth);
        // Read compensation before overflow/inert writes invalidate layout.
        const padding = gap && !view.getComputedStyle(doc.documentElement).scrollbarGutter?.includes('stable')
            ? `${(parseFloat(view.getComputedStyle(body).paddingRight) || 0) + gap}px` : null;
        // Overlay scrollbars need no compensation. Avoid invalidating the
        // entire document through an otherwise unused inherited variable.
        const ownsGap = gap > 0 || oldGap[0] !== '';
        const ios = /iP(ad|hone|od)/.test(view.navigator?.userAgent || '') || (/Mac/.test(view.navigator?.platform || '') && view.navigator?.maxTouchPoints > 1);
        const x = view.scrollX, y = view.scrollY;
        return { body, saved, oldGap, ownsGap, x, y, ios, gap, padding };
    }

    function lockScroll(needed, snapshot) {
        if (needed && !scrollLock) {
            scrollLock = snapshot || captureScrollLock();
            const { body, ownsGap, gap, padding, ios, x, y } = scrollLock;
            body.style.overflow = 'hidden';
            if (ownsGap) doc.documentElement.style.setProperty('--lq-scrollbar-w', `${gap}px`);
            if (padding !== null) body.style.paddingRight = padding;
            if (ios) Object.assign(body.style, { position: 'fixed', top: `${-y}px`, left: `${-x}px`, width: '100%' });
        } else if (!needed && scrollLock) {
            const saved = scrollLock; scrollLock = null;
            for (const [key, value, priority] of saved.saved) saved.body.style.setProperty(key, value, priority);
            if (saved.ownsGap) doc.documentElement.style.setProperty('--lq-scrollbar-w', ...saved.oldGap);
            if (saved.ios) view.scrollTo(saved.x, saved.y);
        }
    }

    function restoreBlocked(node, saved) {
        if (saved.native) node.inert = saved.inert;
        if (node.getAttribute('aria-hidden') === 'true') restoreAttribute(node, 'aria-hidden', saved.aria);
    }

    const hasResources = () => stack.length || companions.size || [...externals].some(externalActive);
    function syncCompanions() {
        if (movingCompanions || !companions.size) return;
        movingCompanions = true;
        try {
            const focusedDialog = doc.activeElement?.closest?.('dialog[open]');
            const native = [focusedDialog, ...[...doc.querySelectorAll('dialog[open]')].reverse()]
                .find((node) => { try { return node?.matches(':modal'); } catch { return false; } });
            const destination = native || doc.body;
            for (const [root, record] of [...companions]) {
                if (!root.isConnected && root.parentNode !== record.host) { record.release('removed'); continue; }
                if (root.parentNode !== destination) destination.append(root);
                record.host = destination;
            }
        } finally { movingCompanions = false; }
    }
    function companionHostClosed() { syncCompanions(); syncBackground(); }

    const setOrder = (root, value) => {
        if (root.style.getPropertyValue('--lq-layer-order') !== value || root.style.getPropertyPriority('--lq-layer-order')) root.style.setProperty('--lq-layer-order', value);
    };
    function syncBackground({ scrollSnapshot, relocateCompanions = true, retainBlockedFor } = {}) {
        if (relocateCompanions) syncCompanions();
        const current = modal();
        lockScroll(Boolean(current), scrollSnapshot);
        const owned = current ? stack.slice(stack.indexOf(current)).map((item) => item.root) : [];
        const allowed = [...owned, ...externalRoots(owned), ...companions.keys()];
        const next = new Set();
        function visit(parent) {
            for (const child of parent.children) {
                if (allowed.includes(child)) continue;
                if (allowed.some((root) => child.contains(root))) visit(child);
                else next.add(child);
            }
        }
        if (current && doc.body) visit(doc.body);
        for (const [node, saved] of blocked) if (!next.has(node) && !(retainBlockedFor && node.contains(retainBlockedFor))) { restoreBlocked(node, saved); blocked.delete(node); }
        for (const node of next) {
            if (!blocked.has(node)) {
                const saved = { native: 'inert' in node, inert: node.inert, aria: node.getAttribute('aria-hidden') };
                blocked.set(node, saved);
                if (saved.native) node.inert = true;
                node.setAttribute('aria-hidden', 'true');
            }
        }
        const disabled = new Set();
        for (const [node, saved] of blocked) if (!saved.native) {
            if (node.matches(FOCUSABLE)) disabled.add(node);
            node.querySelectorAll(FOCUSABLE).forEach((child) => disabled.add(child));
        }
        for (const [node, value] of tabs) if (!disabled.has(node)) {
            if (node.getAttribute('tabindex') === '-1') restoreAttribute(node, 'tabindex', value);
            tabs.delete(node);
        }
        for (const node of disabled) if (!tabs.has(node)) { tabs.set(node, node.getAttribute('tabindex')); node.tabIndex = -1; }
        const present = [...externals].filter(externalPresent);
        const orderedLayers = [...stack, ...present.filter(ordered)].sort((a, b) => (records.get(a)?.order || externalRecords.get(a).order) - (records.get(b)?.order || externalRecords.get(b).order));
        stack.forEach((item) => setOrder(item.root, String(orderedLayers.indexOf(item) + 1)));
        for (const [item, saved] of externalLayers) if (!present.includes(item) || item.root() !== saved.root) {
            restoreAttribute(saved.root, 'data-lq-layer-state', saved.state);
            saved.root.style.setProperty('--lq-layer-order', saved.order, saved.priority);
            externalLayers.delete(item);
        }
        present.forEach((item, index) => {
            const root = item.root();
            if (!element(root, doc)) return;
            if (!externalLayers.has(item)) externalLayers.set(item, { root, state: root.getAttribute('data-lq-layer-state'),
                order: root.style.getPropertyValue('--lq-layer-order'), priority: root.style.getPropertyPriority('--lq-layer-order') });
            const state = item.isOpen() ? 'open' : 'closing';
            if (root.dataset.lqLayerState !== state) root.dataset.lqLayerState = state;
            setOrder(root, String(ordered(item) ? orderedLayers.indexOf(item) + 1 : orderedLayers.length + index + 1));
        });
    }

    function setState(handle, state) { handle.state = state; if (handle.root.dataset.lqLayerState !== state) handle.root.dataset.lqLayerState = state; }
    function focusFirst(handle, force = false) {
        const record = records.get(handle);
        if (!force && record.options.initialFocus === false) return;
        if (!force) {
            const event = new view.Event('lq:initial-focus', { cancelable: true });
            call(handle, 'onInitialFocus', event);
            if (event.defaultPrevented || !active(handle) || top() !== handle || topExternal()) return;
        }
        let preferred = record.options.initialFocus;
        if (typeof preferred === 'function') preferred = call(handle, 'initialFocus');
        // Initial focus needs only the first eligible target. Checking every
        // link in a long navigation pane forces needless style/geometry reads.
        const target = [preferred, record.surface.querySelector('[data-autofocus]')].find(visible)
            || [...record.surface.querySelectorAll(FOCUSABLE)].find((node) => node.tabIndex >= 0 && visible(node))
            || record.surface;
        focus(target);
    }
    function position(handle) {
        const record = records.get(handle);
        const anchor = record.options.anchor;
        if (!anchor || !['popover', 'menu'].includes(handle.type)) return;
        const box = record.surface.getBoundingClientRect();
        const result = getPopoverPosition({ anchor: anchor.getBoundingClientRect(), panel: { width: record.surface.offsetWidth || box.width, height: record.surface.offsetHeight || box.height },
            width: view.innerWidth, height: view.innerHeight, placement: record.options.placement });
        record.surface.style.left = `${result.left}px`; record.surface.style.top = `${result.top}px`;
        record.surface.toggleAttribute('data-lq-flipped', result.flipped);
    }
    function reposition() {
        if (repositionFrame !== null) return;
        repositionFrame = view.requestAnimationFrame(() => { repositionFrame = null; stack.forEach(position); });
    }
    function inside(handle, event) {
        const path = event.composedPath?.() || [event.target];
        const owned = [handle, ...descendants(handle)].map((item) => records.get(item).surface);
        return [...owned, ...externalRoots(owned), ...companions.keys()].some((scope) => path.some((node) => node?.nodeType && scope.contains(node)))
            || path.some((node) => node?.nodeType && handle.trigger?.contains(node));
    }
    function blockPointer(event) {
        if ([...blocked.keys()].some((node) => node.contains(event.target))) {
            event.preventDefault(); event.stopImmediatePropagation(); return true;
        }
        return false;
    }
    function pointerdown(event) {
        const handle = topExternal() ? null : top();
        pointerStart = handle && event.button === 0 ? { handle, outside: !inside(handle, event) } : null;
        if (modal()) blockPointer(event);
    }
    function pointercancel() { pointerStart = null; }
    function click(event) {
        const start = pointerStart; pointerStart = null;
        const handle = topExternal() ? null : top();
        if (handle && start?.handle === handle && start.outside && !inside(handle, event) && records.get(handle).options.closeOnOutside !== false) close(handle, 'outside');
        if (modal()) blockPointer(event);
    }
    function keydown(event) {
        if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
        if (event.key === 'Escape' && dismissExternal('escape')) {
            event.preventDefault(); event.stopPropagation(); return;
        }
        if (ordered(topExternal() || {})) return;
        const handle = top();
        if (!handle) return;
        if (event.key === 'Escape') {
            // One physical event belongs to the current top even while checking/closing.
            event.preventDefault(); event.stopPropagation();
            if (records.get(handle).options.closeOnEscape !== false) close(handle, 'escape');
        } else if (event.key === 'Tab' && modal()) {
            const scope = scopes();
            const nodes = [...new Set(scope.flatMap(focusable))];
            const index = nodes.indexOf(doc.activeElement);
            if (!nodes.length) { event.preventDefault(); focus(records.get(handle).surface); }
            else if (index < 0 || (!event.shiftKey && index === nodes.length - 1) || (event.shiftKey && index === 0)) {
                event.preventDefault(); focus(event.shiftKey ? nodes.at(-1) : nodes[0]);
            }
        }
    }
    function focusin(event) {
        syncCompanions();
        if (redirectingFocus || ordered(topExternal() || {}) || !modal() || scopes().some((scope) => scope.contains(event.target))) return;
        redirectingFocus = true; focusFirst(top(), true); redirectingFocus = false;
    }
    function sweep() {
        queued = false;
        if (disposed) return;
        for (const item of externals) if ((externalActive(item) || externalPresent(item)) && (!item.trigger()?.isConnected || (item.owner && !item.owner()?.isConnected))) dismissExternalItem(item, 'parent-destroyed');
        for (const item of [...stack]) {
            if (!active(item)) continue;
            if (!item.root.isConnected || (element(item.owner, doc) && !item.owner.isConnected) || (item.trigger && !item.trigger.isConnected)) destroyHandle(item, 'parent-destroyed');
        }
        syncBackground();
    }
    function listen() {
        if (listening) return;
        listening = true;
        // Bubble honors a control's own preventDefault; legacy capture bridges are separate.
        doc.addEventListener('keydown', keydown);
        doc.addEventListener('focusin', focusin, true);
        doc.addEventListener('pointerdown', pointerdown, true);
        doc.addEventListener('pointercancel', pointercancel, true);
        doc.addEventListener('click', click, true);
        view.addEventListener('resize', reposition);
        doc.addEventListener('scroll', reposition, true);
        doc.addEventListener('close', companionHostClosed, true);
        observer ||= new view.MutationObserver(() => { if (!queued) { queued = true; queueMicrotask(sweep); } });
        observer.observe(doc.documentElement, { childList: true, subtree: true }); observed = true;
    }
    function unlisten() {
        if (!listening) return;
        listening = false;
        doc.removeEventListener('keydown', keydown);
        doc.removeEventListener('focusin', focusin, true);
        doc.removeEventListener('pointerdown', pointerdown, true);
        doc.removeEventListener('pointercancel', pointercancel, true);
        doc.removeEventListener('click', click, true);
        view.removeEventListener('resize', reposition);
        doc.removeEventListener('scroll', reposition, true);
        doc.removeEventListener('close', companionHostClosed, true);
        if (observed) observer.disconnect(); observed = false;
        if (repositionFrame !== null) view.cancelAnimationFrame(repositionFrame);
        repositionFrame = null; pointerStart = null;
    }
    function settle(record, value) {
        for (const cancel of [...record.waits]) cancel();
        for (const timer of record.timers) clearTimeout(timer);
        record.timers.clear();
        const pending = record.pending; record.pending = null;
        pending?.resolve(value);
    }
    function bounded(record, promise, milliseconds, fallback) {
        return new Promise((resolve) => {
            const finish = (value) => { clearTimeout(timer); record.timers.delete(timer); record.waits.delete(cancel); resolve(value); };
            const cancel = () => finish(fallback);
            const timer = setTimeout(cancel, Math.max(0, Math.min(Number(milliseconds) || 0, 30000)));
            record.timers.add(timer);
            record.waits.add(cancel);
            Promise.resolve(promise).then(finish, cancel);
        });
    }
    function release(handle) {
        const record = records.get(handle);
        const index = stack.indexOf(handle); if (index >= 0) stack.splice(index, 1);
        roots.delete(handle.root);
        handle.root.removeEventListener('cancel', record.onCancel);
        handle.root.removeEventListener('close', record.onNativeClose);
        record.hostDialog?.removeEventListener('close', record.onHostClose);
        const nativeOpen = record.native && handle.root.open;
        cancelOverlayMotion(handle.root);
        handle.root.hidden = true;
        for (const [node, name, value] of record.attributes) restoreAttribute(node, name, value);
        for (const [node, name, value, priority] of record.styles) node.style.setProperty(name, value, priority);
        if (nativeOpen) {
            // Restore the background in the native close batch, but keep our
            // existing block on its former focus until native automatic return
            // has finished. The cancellable coordinator hook still owns return.
            syncBackground({ relocateCompanions: false, retainBlockedFor: record.lastFocus });
            handle.root.close();
        }
        if (record.appended) handle.root.remove();
        syncBackground();
        if (!hasResources()) unlisten();
    }
    function returnFocus(handle, sequence) {
        const record = records.get(handle);
        if (record.options.returnFocus === false || handle.closeReason === 'outside' || sequence !== openingSequence) return;
        if (top() && top() !== handle.parentLayer && record.externalParent !== topExternal()) return;
        const event = new view.Event('lq:return-focus', { cancelable: true });
        call(handle, 'onReturnFocus', event);
        if (event.defaultPrevented || sequence !== openingSequence) return;
        let target = record.options.returnFocus;
        if (typeof target === 'function') target = call(handle, 'returnFocus');
        const destination = [target, handle.trigger, record.lastFocus].find(visible);
        if (destination) focus(destination);
        else if (top()) focusFirst(top());
    }
    function close(handle, reason = 'programmatic') {
        const record = records.get(handle);
        if (!record || handle.state === 'destroyed') return Promise.resolve(false);
        if (handle.state === 'closed') return Promise.resolve(true);
        if (record.pending) return record.pending.promise;
        if (reason === 'parent-destroyed') { destroyHandle(handle, reason); return Promise.resolve(true); }
        const generation = ++record.generation;
        const sequence = openingSequence;
        let resolve;
        const promise = new Promise((done) => { resolve = done; });
        record.pending = { promise, resolve };
        setState(handle, 'checking');
        const current = () => active(handle) && record.generation === generation;
        void (async () => {
            let allowed;
            try {
                const decision = record.options.beforeClose?.(reason, handle);
                if (!current()) return;
                allowed = await bounded(record, decision, record.options.checkTimeoutMs ?? 10000, false);
            } catch { allowed = false; }
            if (!current()) return;
            if (allowed === false) { setState(handle, 'open'); settle(record, false); return; }
            for (const child of descendants(handle).reverse()) {
                if (active(child) && !await close(child, reason)) {
                    if (current()) { setState(handle, 'open'); settle(record, false); }
                    return;
                }
                if (!current()) return;
            }
            handle.closeReason = reason;
            setState(handle, 'closing');
            dismissOwnedExternals(handle);
            call(handle, 'onCloseRequested', reason);
            if (!current()) return;
            await bounded(record, setOverlayOpen(handle.root, false), record.options.closeTimeoutMs ?? 2000, false);
            if (!current()) return;
            setState(handle, 'closed');
            release(handle);
            // Adapter cleanup in either completion hook cannot revoke a completed exit.
            settle(record, true);
            returnFocus(handle, sequence);
            call(handle, 'onClose', reason);
        })();
        return promise;
    }
    function destroyHandle(handle, reason = 'destroyed') {
        const record = records.get(handle);
        if (!record || handle.state === 'destroyed') return;
        record.generation++; settle(record, false);
        const wasActive = active(handle);
        setState(handle, 'destroyed'); handle.closeReason = reason;
        dismissOwnedExternals(handle);
        // Mark the owner first: a child's cleanup cannot register under a dying parent.
        for (const child of descendants(handle).reverse()) destroyHandle(child, 'parent-destroyed');
        if (wasActive) release(handle);
        call(handle, 'onDestroy', reason);
    }
    function update(handle, changes = {}) {
        const record = records.get(handle);
        if (handle.state === 'destroyed') return handle;
        if (changes.surface && changes.surface !== record.surface) throw new Error('A layer surface cannot change while registered');
        if ('parentLayer' in changes && changes.parentLayer !== handle.parentLayer) throw new Error('A layer parent cannot change while registered');
        if (changes.type && !TYPES.has(changes.type)) throw new TypeError('Unknown layer type');
        if (changes.modality && !['modal', 'non-modal'].includes(changes.modality)) throw new TypeError('Unknown layer modality');
        Object.assign(record.options, changes);
        handle.type = record.options.type;
        handle.modality = record.options.modality;
        handle.returnFocus = record.options.returnFocus;
        handle.beforeClose = record.options.beforeClose;
        if ('owner' in changes) handle.owner = changes.owner;
        if ('trigger' in changes || 'anchor' in changes) {
            handle.trigger = changes.trigger || changes.anchor || null;
            if (!('owner' in record.options)) handle.owner = handle.trigger || handle.root;
        }
        if (active(handle)) {
            if (record.roleOwned) record.surface.setAttribute('role', handle.type === 'menu' ? 'menu' : 'dialog');
            if (handle.modality === 'modal') record.surface.setAttribute('aria-modal', 'true');
            else record.surface.removeAttribute('aria-modal');
        }
        if (active(handle)) { syncBackground(); position(handle); }
        return handle;
    }
    function open(root, options = {}) {
        if (disposed) throw new Error('Layer system is destroyed');
        if (!element(root, doc)) throw new TypeError('Layer root must be an Element in this Document');
        const existing = roots.get(root);
        if (existing) {
            update(existing, options);
            if (['checking', 'closing'].includes(existing.state)) {
                const record = records.get(existing); record.generation++; settle(record, false); openingSequence++;
                reveal(existing);
            }
            return existing;
        }
        const type = options.type || 'modal';
        const modality = options.modality || (['popover', 'menu'].includes(type) ? 'non-modal' : 'modal');
        if (!TYPES.has(type) || !['modal', 'non-modal'].includes(modality)) throw new TypeError('Invalid layer type or modality');
        const surface = options.surface || root;
        if (!element(surface, doc) || !root.contains(surface)) throw new TypeError('Layer surface must belong to its root');
        const candidate = options.trigger || options.anchor || (element(doc.activeElement, doc) ? doc.activeElement : null);
        const trigger = [doc.body, doc.documentElement].includes(candidate) ? null : candidate;
        const externalParent = [...externals].reverse().find((item) => ordered(item) && item.isOpen() && item.root()?.contains(trigger));
        const parentLayer = 'parentLayer' in options ? options.parentLayer
            : (options.parent && options.parent !== 'anchor' ? options.parent
                : findPopoverParent(stack, trigger, (item) => item.root) || externalRecords.get(externalParent)?.parentLayer);
        if (parentLayer && (!stack.includes(parentLayer) || !active(parentLayer))) throw new TypeError('Parent layer must be active in this system');
        const nativeModal = root.tagName === 'DIALOG' && modality === 'modal' && typeof root.showModal === 'function';
        // Capture before this open operation changes attributes, hosts or focus.
        // Native display can then flush isolation and scroll-lock writes together.
        const batchNative = nativeModal && !root.open;
        const scrollSnapshot = batchNative && !scrollLock ? captureScrollLock() : null;
        for (const item of externals) if (!ordered(item) && item.isOpen()) dismissExternalItem(item, 'superseded');
        const appended = !root.isConnected;
        if (appended) getPortalHost({ trigger, parentLayer }).append(root);
        const record = { options: { ...options, type, modality }, surface, appended, native: false, pending: null, generation: 0, externalParent,
            lastFocus: doc.activeElement, attributes: [], styles: [], timers: new Set(), waits: new Set() };
        const handle = { root, owner: options.owner || trigger || root, trigger, parentLayer, type, modality,
            returnFocus: options.returnFocus, beforeClose: options.beforeClose, closeReason: null, state: 'opening',
            update: (changes) => update(handle, changes), destroy: () => destroyHandle(handle) };
        records.set(handle, record); roots.set(root, handle);
        const attr = (node, name, value) => { record.attributes.push([node, name, node.getAttribute(name)]); node.setAttribute(name, value); };
        const style = (node, name) => record.styles.push([node, name, node.style.getPropertyValue(name), node.style.getPropertyPriority(name)]);
        if (!surface.hasAttribute('tabindex')) attr(surface, 'tabindex', '-1');
        record.roleOwned = !surface.hasAttribute('role');
        if (record.roleOwned) attr(surface, 'role', type === 'menu' ? 'menu' : 'dialog');
        record.attributes.push([surface, 'aria-modal', surface.getAttribute('aria-modal')]);
        if (modality === 'modal') surface.setAttribute('aria-modal', 'true');
        else surface.removeAttribute('aria-modal');
        if (surface !== root && !surface.hasAttribute('data-ui-overlay-surface')) attr(surface, 'data-ui-overlay-surface', '');
        style(root, '--lq-layer-order');
        if (options.anchor && ['popover', 'menu'].includes(type)) {
            style(surface, 'left'); style(surface, 'top');
            record.attributes.push([surface, 'data-lq-flipped', surface.getAttribute('data-lq-flipped')]);
        }
        record.onCancel = (event) => { event.preventDefault(); if (top() === handle) close(handle, 'escape'); };
        record.onNativeClose = () => { if (active(handle)) destroyHandle(handle, 'native-closed'); };
        record.hostDialog = root.parentElement?.closest('dialog[open]') || null;
        record.onHostClose = () => destroyHandle(handle, 'parent-destroyed');
        record.hostDialog?.addEventListener('close', record.onHostClose);
        root.addEventListener('cancel', record.onCancel); root.addEventListener('close', record.onNativeClose);
        stack.push(handle); record.order = ++openingSequence; listen();
        try {
            if (nativeModal) {
                if (batchNative) syncBackground({ scrollSnapshot, relocateCompanions: false });
                root.hidden = false; if (!root.open) root.showModal(); record.native = true;
            } else if (root.tagName === 'DIALOG' && !root.open) attr(root, 'open', '');
            syncBackground(); reveal(handle);
        } catch (error) { destroyHandle(handle, 'open-failed'); throw error; }
        return handle;
    }
    function reveal(handle) {
        const record = records.get(handle);
        const generation = ++record.generation;
        setState(handle, 'opening');
        const opening = setOverlayOpen(handle.root, true);
        position(handle); focusFirst(handle);
        void opening.then(() => { if (record.generation === generation && handle.state === 'opening') setState(handle, 'open'); });
    }
    const system = {
        open, close, top, getPortalHost,
        closeTop: (reason = 'programmatic') => dismissExternal(reason) ? Promise.resolve(true) : top() ? close(top(), reason) : Promise.resolve(false),
        registerCompanion(root, { onRelease } = {}) {
            if (disposed) throw new Error('Layer system is destroyed');
            if (!element(root, doc) || [doc.body, doc.documentElement].includes(root) || roots.has(root)) throw new TypeError('Companion must be a separate Element in this Document');
            if (companions.has(root)) return companions.get(root).lease;
            const record = { host: root.parentNode, release(reason) {
                if (!companions.delete(root)) return;
                if (root.isConnected && root.parentNode !== doc.body) doc.body.append(root);
                syncBackground(); if (!hasResources()) unlisten();
                if (reason) { try { onRelease?.(reason); } catch { /* Releasing must not retain core resources. */ } }
            } };
            record.lease = { refresh: () => { if (!disposed && companions.has(root)) syncBackground(); }, destroy: () => record.release() };
            companions.set(root, record); listen(); syncBackground();
            return record.lease;
        },
        registerExternal(adapter) {
            if (disposed) throw new Error('Layer system is destroyed');
            externals.add(adapter);
            externalRecords.set(adapter, { open: false, order: 0, parentLayer: null });
            const refresh = () => {
                if (disposed) return;
                const record = externalRecords.get(adapter);
                if (!record) return;
                const open = adapter.isOpen();
                if (ordered(adapter) && open && !record.open) {
                    record.order = ++openingSequence;
                    record.parentLayer = findPopoverParent(stack, adapter.trigger(), (item) => item.root);
                }
                const closed = record.open && !open;
                record.open = open;
                if (ordered(adapter) && closed) for (const child of [...stack].reverse()) {
                    if (records.get(child).externalParent === adapter) destroyHandle(child, 'parent-destroyed');
                }
                if (hasResources()) listen(); else unlisten();
                syncBackground();
            };
            refresh();
            return { refresh, isTop: () => !disposed && topExternal() === adapter,
                destroy() {
                    if (!externals.delete(adapter)) return;
                    dismissExternalItem(adapter, 'parent-destroyed');
                    externalRecords.delete(adapter);
                    syncBackground();
                    if (!hasResources()) unlisten();
                } };
        },
        async closeAll(reason = 'programmatic') {
            for (const handle of [...stack].reverse()) if (active(handle) && !await close(handle, reason)) return false;
            return true;
        },
        destroy() {
            if (disposed) return;
            disposed = true;
            for (const record of [...companions.values()]) record.release('system-destroyed');
            for (const item of externals) dismissExternalItem(item, 'parent-destroyed');
            externals.clear();
            externalRecords.clear();
            for (const handle of [...stack].reverse()) destroyHandle(handle);
            unlisten(); syncBackground();
            if (ownsHost && host?.childNodes.length === 0) host.remove();
            if (doc[SYSTEM] === system) delete doc[SYSTEM];
        },
    };
    return system;
}
