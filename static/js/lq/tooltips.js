import { attributesMarkup, escapeHtml } from './html.js';
import { getLayerSystem } from './layer.js';

const BINDINGS = Symbol.for('lanshare.lq.tooltip-bindings.v1');
const ROOTS = Symbol.for('lanshare.lq.tooltip-roots.v1');
const IDS = Symbol.for('lanshare.lq.tooltip-identities');
export function tooltipProps(props = {}, doc) {
    if (!props || typeof props !== 'object' || Array.isArray(props) || Object.keys(props).some(key => !['id', 'text'].includes(key))) throw new TypeError('Invalid LQ tooltip props');
    let id = props.id;
    if (id === undefined && doc) do { id = `lq-tooltip-${doc[IDS] = (doc[IDS] || 0) + 1}`; } while (doc.getElementById(id));
    if (typeof id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new TypeError('Invalid LQ tooltip id');
    if (typeof props.text !== 'string' || !props.text.trim()) throw new TypeError('LQ tooltip needs plain text');
    return { text: props.text.trim(), attrs: { id, class: 'lq-tooltip lq-glass lq-glass--thin', role: 'tooltip', hidden: '' } };
}
export function tooltipMarkup(props) { const p = tooltipProps(props); return `<div${attributesMarkup(p.attrs)}>${escapeHtml(p.text)}</div>`; }
export function createTooltip(props, doc = document) { const p = tooltipProps(props, doc), root = doc.createElement('div'); for (const [key, value] of Object.entries(p.attrs)) root.setAttribute(key, value); root.textContent = p.text; return root; }

/** Opt-in icon names only; data-explain remains owned by its existing controller. */
export function bindTooltip(trigger, rootOrProps) {
    const doc = trigger?.ownerDocument;
    if (!doc || !trigger.matches('button,[role="button"]') || !trigger.querySelector('svg,img') || trigger.textContent.trim() || trigger.hasAttribute('data-explain')) throw new TypeError('Tooltip needs an icon button without data-explain');
    const bindings = doc[BINDINGS] ||= new WeakMap();
    if (bindings.has(trigger)) { const prior = bindings.get(trigger); if (rootOrProps?.nodeType && prior.root !== rootOrProps) throw new TypeError('Trigger already owns a different tooltip'); return prior; }
    const root = rootOrProps?.nodeType ? rootOrProps : createTooltip(rootOrProps, doc);
    if (root.ownerDocument !== doc || root.getAttribute('role') !== 'tooltip' || !root.id || root.querySelector('*') || !root.textContent.trim()) throw new TypeError('Tooltip must be a plain text Element');
    const roots = doc[ROOTS] ||= new WeakMap();
    if (roots.has(root)) throw new TypeError('Tooltip already belongs to a different trigger');
    const originalTitle = trigger.getAttribute('title'), originalLabel = trigger.getAttribute('aria-label');
    const originalDescription = trigger.getAttribute('aria-describedby');
    const hadDescription = (originalDescription || '').split(/\s+/).includes(root.id);
    let ownedDescription = null, descriptionBefore = originalDescription;
    if (!originalLabel && !trigger.getAttribute('aria-labelledby')) trigger.setAttribute('aria-label', root.textContent);
    trigger.removeAttribute('title');
    const parent = root.parentNode, next = root.nextSibling, system = getLayerSystem(doc), listeners = [];
    let handle = null, disposed = false, showTimer = null, hideTimer = null, hover = false, overTip = false, focused = false, suppressed = false;
    const clear = () => { clearTimeout(showTimer); clearTimeout(hideTimer); showTimer = hideTimer = null; };
    const describe = show => {
        if (!show && ownedDescription === null) return;
        if (!show && trigger.getAttribute('aria-describedby') === ownedDescription) {
            if (descriptionBefore === null) trigger.removeAttribute('aria-describedby'); else trigger.setAttribute('aria-describedby', descriptionBefore);
            ownedDescription = null; return;
        }
        const tokens = new Set((trigger.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
        if (show && trigger.getAttribute('aria-describedby') !== ownedDescription) {
            if (ownedDescription === null) descriptionBefore = trigger.getAttribute('aria-describedby');
            else descriptionBefore = [...tokens].filter(token => token !== root.id || hadDescription).join(' ') || null;
        }
        if (show || hadDescription) tokens.add(root.id); else tokens.delete(root.id);
        if (tokens.size) trigger.setAttribute('aria-describedby', [...tokens].join(' ')); else trigger.removeAttribute('aria-describedby');
        ownedDescription = show ? trigger.getAttribute('aria-describedby') : null;
    };
    const restoreRoot = () => { describe(false); root.hidden = true; if (parent) parent.insertBefore(root, next?.parentNode === parent ? next : null); else root.remove(); };
    const listen = (node, name, listener) => { node.addEventListener(name, listener); listeners.push(() => node.removeEventListener(name, listener)); };
    function show() {
        clear(); if (disposed || suppressed || !trigger.isConnected) return null;
        const host = system.getPortalHost({ trigger }); if (root.parentNode !== host) host.append(root);
        describe(true);
        handle = system.open(root, { type: 'popover', modality: 'non-modal', trigger, owner: trigger, anchor: trigger, placement: 'top', initialFocus: false, returnFocus: false,
            onClose: reason => { if (reason === 'escape') suppressed = true; clear(); restoreRoot(); }, onDestroy: () => destroy() });
        return handle;
    }
    function hide(reason = 'programmatic') { clear(); return handle ? system.close(handle, reason) : Promise.resolve(true); }
    function leave() { clear(); if (!hover && !overTip && !focused) { suppressed = false; hideTimer = setTimeout(() => { void hide('leave'); }, 100); } }
    function destroy() {
        if (disposed) return; disposed = true; clear(); observer.disconnect(); for (const remove of listeners) remove(); handle?.destroy(); restoreRoot();
        if (!trigger.hasAttribute('title') && originalTitle !== null) trigger.setAttribute('title', originalTitle);
        if (originalLabel === null && trigger.getAttribute('aria-label') === root.textContent) trigger.removeAttribute('aria-label');
        bindings.delete(trigger); roots.delete(root);
    }
    listen(trigger, 'pointerenter', event => { if (event.pointerType === 'touch') return; hover = true; clear(); if (!suppressed) showTimer = setTimeout(show, 400); });
    listen(trigger, 'pointerleave', () => { hover = false; leave(); });
    listen(root, 'pointerenter', () => { overTip = true; clear(); }); listen(root, 'pointerleave', () => { overTip = false; leave(); });
    listen(trigger, 'focusin', () => { focused = true; if (!suppressed) show(); }); listen(trigger, 'focusout', () => { focused = false; leave(); });
    const observer = new doc.defaultView.MutationObserver(() => { if (!trigger.isConnected) destroy(); }); observer.observe(doc.documentElement, { childList: true, subtree: true });
    const binding = { root, show, hide, destroy, get handle() { return handle; } }; bindings.set(trigger, binding); roots.set(root, binding); return binding;
}
