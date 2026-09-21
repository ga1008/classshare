import { createComponent } from './components.js';
import { createIcon } from './icons.js';
import { safeUrl } from './html.js';
import { cancelOverlayMotion, setOverlayOpen } from '../ui_overlay_motion.js';
import { getLayerSystem } from './layer.js';

const SYSTEM = Symbol.for('lanshare.lq.toast.v1');
const TONES = ['primary', 'success', 'warning', 'danger', 'info', 'neutral'];
const ICONS = { success: 'circle-check', warning: 'triangle-alert', danger: 'circle-alert', primary: 'info', info: 'info', neutral: 'bell' };
const text = (value, name) => {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError(`LQ toast ${name} must be nonempty text`);
    return value.trim();
};

export function toastProps(message, options = {}) {
    message = text(message, 'message');
    if (!options || typeof options !== 'object' || Array.isArray(options)) throw new TypeError('LQ toast options must be a mapping');
    const tone = options.tone === undefined ? 'info' : options.tone;
    if (!TONES.includes(tone)) throw new TypeError('Invalid LQ toast tone');
    const duration = options.duration === undefined ? 3000 : options.duration;
    if (typeof duration !== 'number' || !Number.isFinite(duration) || duration < 0 || duration > 2147483647) throw new TypeError('Invalid LQ toast duration');
    const key = options.key == null ? null : text(options.key, 'key');
    const icon = options.icon === undefined ? ICONS[tone] : text(options.icon, 'icon');
    const closeLabel = options.closeLabel === undefined ? '关闭通知' : text(options.closeLabel, 'closeLabel');
    for (const name of ['onClose', 'onError']) if (options[name] !== undefined && typeof options[name] !== 'function') throw new TypeError(`LQ ${name} must be a function`);
    let action = null;
    if (options.action !== undefined && options.action !== null) {
        const value = options.action;
        if (typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Invalid LQ toast action');
        const label = text(value.label, 'action label');
        const href = value.href === undefined ? null : safeUrl(value.href);
        const onClick = value.onClick;
        if ((href !== null) === (onClick !== undefined) || (onClick !== undefined && typeof onClick !== 'function')) throw new TypeError('LQ action needs exactly one safe href or callback');
        action = { label, href, onClick };
    }
    return { message, tone, duration, key, icon, closeLabel, action, onClose: options.onClose, onError: options.onError };
}

/** Monotonic remaining time; independent pause owners cannot resume each other. */
export function createToastClock(expire, { now = () => performance.now(), set = setTimeout, clear = clearTimeout } = {}) {
    let remaining = Infinity, deadline = 0, timer = null, destroyed = false;
    const pauses = new Set();
    const stop = () => { if (timer !== null) { remaining = Math.max(0, deadline - now()); clear(timer); timer = null; } };
    const arm = () => {
        if (destroyed || pauses.size || !Number.isFinite(remaining)) return;
        deadline = now() + remaining;
        timer = set(() => { timer = null; remaining = 0; expire(); }, remaining);
    };
    return {
        reset(duration) { if (destroyed) return; stop(); remaining = duration > 0 ? duration : Infinity; arm(); },
        pause(reason) { if (!destroyed && !pauses.has(reason)) { pauses.add(reason); stop(); } },
        resume(reason) { if (pauses.delete(reason) && !pauses.size) arm(); },
        stop,
        destroy() { destroyed = true; stop(); pauses.clear(); },
        get remaining() { return timer === null ? remaining : Math.max(0, deadline - now()); },
    };
}

export function getToastSystem(doc = document) {
    if (doc[SYSTEM]) return doc[SYSTEM];
    const view = doc.defaultView;
    const records = [];
    let container = null, companion = null, observer = null, listening = false, disposed = false;
    const now = () => view.performance.now();
    const check = () => { companion?.refresh(); for (const record of [...records]) if (!record.root.isConnected) finish(record, 'removed', true); };
    const visibility = () => { for (const record of records) record.clock[doc.hidden ? 'pause' : 'resume']('hidden'); };
    const pagehide = () => system.destroy();
    function host() {
        if (!container?.isConnected) {
            container = doc.createElement('div'); container.id = 'lq-toasts'; container.className = 'lq-toasts'; doc.body.append(container);
            companion = getLayerSystem(doc).registerCompanion(container, { onRelease: () => system.destroy() });
        }
        if (!listening) {
            listening = true; doc.addEventListener('visibilitychange', visibility); view.addEventListener('pagehide', pagehide);
            observer ||= new view.MutationObserver(check); observer.observe(doc.body, { childList: true, subtree: true });
        }
        return container;
    }
    function idle() {
        if (records.length) return;
        doc.removeEventListener('visibilitychange', visibility); view.removeEventListener('pagehide', pagehide);
        listening = false; observer?.disconnect(); companion?.destroy(); companion = null; container?.remove(); container = null;
    }
    function error(record, failure, callback = record.props.onError) {
        try { callback?.(failure, record.handle); } catch { /* Callback failures cannot retain notifications. */ }
    }
    function cancelExit(record) {
        clearTimeout(record.exitTimer); record.exitTimer = null;
        cancelOverlayMotion(record.root);
        if (record.pending) { record.pending.resolve(false); record.pending = null; }
    }
    function finish(record, reason, destroyed = false) {
        if (!records.includes(record)) return;
        const restoreFocus = record.root.contains(doc.activeElement);
        record.generation++; cancelExit(record); record.clock.destroy();
        record.handle.state = destroyed ? 'destroyed' : 'closed';
        for (const [name, listener] of record.listeners) record.root.removeEventListener(name, listener);
        records.splice(records.indexOf(record), 1); record.root.remove(); idle();
        record.resolveClosed(reason);
        if (restoreFocus) {
            const target = records.at(-1)?.closeButton || record.origin;
            if (target?.isConnected && !target.closest('[hidden],[inert]')) target.focus?.({ preventScroll: true });
        }
        try { record.props.onClose?.(reason, record.handle); } catch (failure) { error(record, failure); }
    }
    function close(record, reason = 'programmatic') {
        if (!records.includes(record)) return Promise.resolve(true);
        if (record.pending) return record.pending.promise;
        record.clock.stop(); record.handle.state = 'closing'; const generation = ++record.generation;
        let resolve; const promise = new Promise(done => { resolve = done; }); record.pending = { promise, resolve };
        const complete = () => {
            if (record.generation !== generation || !records.includes(record)) return;
            record.pending = null; finish(record, reason); resolve(true);
        };
        record.exitTimer = setTimeout(complete, 800);
        void setOverlayOpen(record.root, false).then(complete);
        return promise;
    }
    function update(record, message, options = {}) {
        if (!records.includes(record)) return false;
        const props = toastProps(message, { ...record.props, ...options });
        const changed = props.message !== record.live.textContent || props.tone !== record.props.tone;
        const generation = ++record.generation; cancelExit(record); record.props = props;
        record.handle.state = 'opening'; record.root.dataset.tone = props.tone;
        record.root.removeAttribute('data-action-error');
        record.icon.replaceChildren(createIcon(props.icon, doc));
        record.live.setAttribute('aria-live', props.tone === 'danger' ? 'assertive' : 'polite');
        record.closeButton.setAttribute('aria-label', props.closeLabel);
        if (changed || !record.live.textContent) queueMicrotask(() => {
            if (record.generation === generation && records.includes(record)) record.live.textContent = props.message;
        });
        record.action?.remove(); record.action = null;
        if (props.action) {
            record.action = createComponent('button', { label: props.action.label, variant: 'link', ...(props.action.href ? { href: props.action.href } : {}), disabled: record.actionUsed, ariaDisabled: record.actionUsed, attrs: { 'data-lq-toast-action': '' } }, doc);
            record.content.append(record.action);
        }
        record.clock.reset(props.duration);
        record.clock.resume('action');
        if (doc.hidden) record.clock.pause('hidden'); else record.clock.resume('hidden');
        void setOverlayOpen(record.root, true).then(() => { if (record.generation === generation && records.includes(record)) record.handle.state = 'open'; });
        return record.handle;
    }
    function activate(record, event) {
        const action = record.props.action;
        if (!action || !record.action?.contains(event.target)) return;
        if (record.actionUsed || record.handle.state === 'closing') { event.preventDefault(); return; }
        record.actionUsed = true;
        const generation = record.generation;
        record.action.setAttribute('aria-disabled', 'true'); record.action.setAttribute('data-lq-disabled', 'true');
        if (action.href) { void close(record, 'action'); return; }
        event.preventDefault(); record.clock.pause('action');
        const failed = failure => {
            if (record.generation !== generation || !records.includes(record)) return;
            record.clock.reset(0); record.clock.resume('action');
            record.live.textContent = `${record.props.message} 操作未完成，请重新打开对应页面处理。`;
            record.root.dataset.actionError = 'true'; error(record, failure);
        };
        try {
            Promise.resolve(action.onClick(record.handle)).then(() => {
                if (record.generation === generation && records.includes(record)) void close(record, 'action');
            }, failed);
        } catch (failure) { failed(failure); }
    }
    function show(message, options = {}) {
        if (disposed) throw new Error('Toast system is destroyed');
        const props = toastProps(message, options);
        const sameAction = value => value?.label === props.action?.label && value?.href === props.action?.href && value?.onClick === props.action?.onClick;
        const duplicate = records.find(record => props.key !== null ? record.props.key === props.key
            : record.props.key === null && record.props.message === props.message && record.props.tone === props.tone && sameAction(record.props.action));
        if (duplicate) return update(duplicate, message, props);
        // Remove the oldest before adding: even animated exits count toward three.
        while (records.length >= 3) finish(records[0], 'overflow', true);
        const root = doc.createElement('article'); root.className = 'lq-toast lq-surface'; root.hidden = true;
        const icon = doc.createElement('span'); icon.className = 'lq-toast__icon'; icon.setAttribute('aria-hidden', 'true');
        const content = doc.createElement('div'); content.className = 'lq-toast__content';
        const live = doc.createElement('div'); live.className = 'lq-toast__message'; live.setAttribute('role', 'status'); live.setAttribute('aria-atomic', 'true');
        const closeButton = createComponent('button', { icon: 'x', variant: 'ghost', attrs: { 'aria-label': props.closeLabel } }, doc); closeButton.classList.add('lq-toast__close');
        content.append(live); root.append(icon, content, closeButton);
        let resolveClosed; const closed = new Promise(done => { resolveClosed = done; });
        const record = { root, icon, content, live, closeButton, origin: doc.activeElement, props, generation: 0, pending: null, exitTimer: null,
            action: null, actionUsed: false, listeners: [], resolveClosed, clock: null, handle: null };
        const handle = { root, state: 'opening', closed, update: (next, opts) => update(record, next, opts), close: reason => close(record, reason), destroy: () => finish(record, 'destroyed', true) };
        record.handle = handle;
        record.clock = createToastClock(() => { void close(record, 'timeout'); }, { now, set: view.setTimeout.bind(view), clear: view.clearTimeout.bind(view) });
        const listen = (name, listener) => { record.listeners.push([name, listener]); root.addEventListener(name, listener); };
        listen('pointerenter', () => record.clock.pause('hover')); listen('pointerleave', () => record.clock.resume('hover'));
        listen('focusin', () => record.clock.pause('focus'));
        listen('focusout', event => { if (!root.contains(event.relatedTarget)) record.clock.resume('focus'); });
        listen('click', event => { if (closeButton.contains(event.target)) { event.preventDefault(); void close(record, 'button'); } else activate(record, event); });
        records.push(record); host().append(root); update(record, message, props);
        return handle;
    }
    const system = {
        show,
        get size() { return records.length; },
        clear: () => { for (const record of [...records]) finish(record, 'cleared', true); },
        destroy() {
            if (disposed) return;
            disposed = true; for (const record of [...records]) finish(record, 'destroyed', true); idle();
            if (doc[SYSTEM] === system) delete doc[SYSTEM];
        },
    };
    Object.defineProperty(doc, SYSTEM, { value: system, configurable: true });
    return system;
}

export const toast = (message, options, doc = document) => getToastSystem(doc).show(message, options);
