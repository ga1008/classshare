import { createComponent, componentMarkup } from './components.js';
import { attributesMarkup, safeUrl } from './html.js';
import { getLayerSystem } from './layer.js';

const BINDINGS = Symbol.for('lanshare.lq.menu-bindings.v1');
const ROOTS = Symbol.for('lanshare.lq.menu-roots.v1');
const IDS = Symbol.for('lanshare.lq.menu-identities');
const required = (value, name) => { if (typeof value !== 'string' || !value.trim()) throw new TypeError(`LQ menu ${name} must be text`); return value.trim(); };
const flag = value => { if (value === undefined) return false; if (typeof value !== 'boolean') throw new TypeError('Invalid LQ menu flag'); return value; };
export function menuProps(props = {}, doc) {
    if (!props || typeof props !== 'object' || Array.isArray(props) || Object.keys(props).some(key => !['id', 'label', 'items'].includes(key))) throw new TypeError('Invalid LQ menu props');
    let id = props.id;
    if (id === undefined && doc) do { id = `lq-menu-${doc[IDS] = (doc[IDS] || 0) + 1}`; } while (doc.getElementById(id));
    if (typeof id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) throw new TypeError('Invalid LQ menu id');
    const label = required(props.label, 'label');
    if (!Array.isArray(props.items) || !props.items.length) throw new TypeError('LQ menu needs items');
    const seen = new Set();
    const items = props.items.map(value => {
        if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).some(key => !['id', 'label', 'icon', 'href', 'target', 'disabled', 'danger', 'group'].includes(key))) throw new TypeError('Invalid LQ menu item');
        const item = { id: required(value.id, 'item id'), label: required(value.label, 'item label'), disabled: flag(value.disabled), danger: flag(value.danger), group: value.group === undefined ? '' : required(value.group, 'group') };
        if (seen.has(item.id)) throw new TypeError('Duplicate LQ menu item'); seen.add(item.id);
        const href = value.href === undefined ? null : safeUrl(value.href);
        if (value.href !== undefined && href === null) throw new TypeError('Invalid menu href');
        if (value.target !== undefined && (!href || !['_blank', '_self'].includes(value.target))) throw new TypeError('Invalid menu target');
        item.button = { label: item.label, variant: 'ghost', ...(value.icon === undefined ? {} : { icon: required(value.icon, 'icon') }), ...(href ? { href } : {}), ariaDisabled: item.disabled,
            attrs: { 'data-lq-menu-item': item.id, ...(item.danger ? { 'data-danger': 'true' } : {}), ...(value.target ? { target: value.target } : {}) } };
        // The shared button renderer owns its validation, icon and safe URL rules.
        componentMarkup('button', item.button);
        return item;
    });
    return { id, label, items, attrs: { id, class: 'lq-menu lq-glass', role: 'menu', 'aria-label': label, tabindex: '-1', hidden: '' } };
}
const split = (item, previous) => previous && (item.group !== previous.group || item.danger !== previous.danger);
export function menuMarkup(props) {
    const p = menuProps(props);
    return `<div${attributesMarkup(p.attrs)}>${p.items.map((item, index) => `${split(item, p.items[index - 1]) ? '<div class="lq-menu__separator" role="separator" aria-orientation="horizontal"></div>' : ''}${componentMarkup('button', item.button).replace('class="', 'role="menuitem" tabindex="-1" class="lq-menu__item ')}`).join('')}</div>`;
}
export function createMenu(props, doc = document) {
    const p = menuProps(props, doc), root = doc.createElement('div');
    for (const [key, value] of Object.entries(p.attrs)) root.setAttribute(key, value);
    p.items.forEach((item, index) => {
        if (split(item, p.items[index - 1])) { const line = doc.createElement('div'); line.className = 'lq-menu__separator'; line.setAttribute('role', 'separator'); line.setAttribute('aria-orientation', 'horizontal'); root.append(line); }
        const node = createComponent('button', item.button, doc); node.className = `lq-menu__item ${node.className}`; node.setAttribute('role', 'menuitem'); node.tabIndex = -1; root.append(node);
    });
    return root;
}

/** Action menu only. onAction(id, itemElement) receives the actual HTMLElement,
 * not item props; use itemElement.textContent for display text. Native links
 * deliberately retain their native activation. */
export function bindMenu(trigger, root, options = {}) {
    const doc = trigger?.ownerDocument;
    if (!doc || !trigger.matches('button,[role="button"]') || root?.ownerDocument !== doc || root.getAttribute('role') !== 'menu' || !root.id) throw new TypeError('LQ menu requires a button and menu Element');
    const bindings = doc[BINDINGS] ||= new WeakMap();
    if (bindings.has(trigger)) { const prior = bindings.get(trigger); if (prior.root !== root) throw new TypeError('Trigger already owns a different menu'); return prior; }
    const roots = doc[ROOTS] ||= new WeakMap();
    if (roots.has(root)) throw new TypeError('Menu already belongs to a different trigger');
    const system = getLayerSystem(doc), parent = root.parentNode, next = root.nextSibling;
    const original = new Map(['aria-haspopup', 'aria-controls', 'aria-expanded'].map(name => [name, trigger.getAttribute(name)]));
    let handle = null, generation = 0, disposed = false, busy = false, buffer = '', searchTimer = null, tabbing = false;
    const listeners = [];
    const items = () => [...root.querySelectorAll('[role="menuitem"]')];
    const listen = (node, name, listener) => { node.addEventListener(name, listener); listeners.push(() => node.removeEventListener(name, listener)); };
    const restoreRoot = () => { root.hidden = true; if (parent) parent.insertBefore(root, next?.parentNode === parent ? next : null); else root.remove(); };
    const clearSearch = () => { clearTimeout(searchTimer); searchTimer = null; buffer = ''; };
    const report = error => { try { options.onError?.(error); } catch { /* Consumer errors cannot retain the menu. */ } };
    const move = item => { if (item) item.focus({ preventScroll: true }); };
    function open({ focus = 'first' } = {}) {
        if (disposed) throw new Error('Menu binding is destroyed');
        if (!['first', 'last'].includes(focus)) throw new TypeError('Invalid menu initial focus');
        generation++; busy = false; tabbing = false; clearSearch();
        const host = system.getPortalHost({ trigger, parentLayer: options.parentLayer });
        if (root.parentNode !== host) host.append(root);
        trigger.setAttribute('aria-expanded', 'true');
        handle = system.open(root, { ...options, type: 'menu', modality: 'non-modal', trigger, anchor: trigger, owner: trigger,
            initialFocus: () => focus === 'last' ? items().at(-1) : items()[0],
            beforeClose: (reason, layer) => reason === 'navigation' ? true : options.beforeClose?.(reason, layer),
            onReturnFocus: (event, layer) => { if (tabbing) event.preventDefault(); options.onReturnFocus?.(event, layer); },
            onClose: (reason, layer) => { trigger.setAttribute('aria-expanded', 'false'); clearSearch(); restoreRoot(); options.onClose?.(reason, layer); },
            onDestroy: (reason, layer) => { destroy(); options.onDestroy?.(reason, layer); },
        });
        return handle;
    }
    function close(reason = 'programmatic') { generation++; busy = false; tabbing = false; return handle ? system.close(handle, reason) : Promise.resolve(true); }
    function destroy() {
        if (disposed) return; disposed = true; generation++; clearSearch(); observer.disconnect();
        for (const remove of listeners) remove(); handle?.destroy(); restoreRoot();
        for (const [name, value] of original) value === null ? trigger.removeAttribute(name) : trigger.setAttribute(name, value);
        bindings.delete(trigger); roots.delete(root);
    }
    async function command(item) {
        if (busy || !handle || !['opening', 'open'].includes(handle.state)) return;
        busy = true; const current = generation;
        if (await system.close(handle, 'action') && !disposed && generation === current) {
            try { await options.onAction?.(item.dataset.lqMenuItem, item); } catch (error) { report(error); }
        }
        if (generation === current) busy = false;
    }
    function activate(event) {
        const item = event.target.closest?.('[role="menuitem"]');
        if (!item || !root.contains(item)) return;
        if (item.getAttribute('aria-disabled') === 'true') { event.preventDefault(); return; }
        if (item.matches('a[href]')) { void system.close(handle, 'navigation'); return; }
        if (event.type === 'auxclick') return;
        event.preventDefault(); void command(item);
    }
    listen(root, 'click', activate); listen(root, 'auxclick', activate);
    listen(root, 'pointermove', event => { if (event.pointerType === 'touch' || !handle || !['open', 'opening'].includes(handle.state)) return; const item = event.target.closest?.('[role="menuitem"]'); if (item && root.contains(item)) move(item); });
    listen(root, 'keydown', event => {
        if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return;
        const list = items(), index = list.indexOf(doc.activeElement);
        if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault(); clearSearch();
            move(event.key === 'Home' ? list[0] : event.key === 'End' ? list.at(-1) : list[(index + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length]);
        } else if (event.key === ' ' || event.key === 'Enter') {
            event.preventDefault(); if (index >= 0) list[index].click();
        } else if (event.key === 'Tab') {
            event.preventDefault(); tabbing = true;
            const current = generation;
            const context = trigger.closest('dialog[open],[aria-modal="true"]') || doc;
            const candidates = [...context.querySelectorAll('a[href],button,input,select,textarea,[tabindex]')].filter(node => !root.contains(node) && node.tabIndex >= 0 && !node.matches(':disabled') && !node.closest('[hidden],[inert]') && node.getClientRects().length);
            const at = candidates.indexOf(trigger), destination = candidates[(at + (event.shiftKey ? -1 : 1) + candidates.length) % candidates.length] || trigger;
            void system.close(handle, 'tab').then(closed => { if (!disposed && current === generation) { tabbing = false; if (closed && destination.isConnected) move(destination); } });
        } else if (event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) {
            event.preventDefault(); clearTimeout(searchTimer); buffer += event.key.toLocaleLowerCase();
            const search = [...buffer].every(char => char === buffer[0]) ? buffer[0] : buffer;
            move([...list.slice(index + 1), ...list.slice(0, index + 1)].find(item => (item.getAttribute('aria-label') || item.textContent).trim().toLocaleLowerCase().startsWith(search)));
            searchTimer = setTimeout(clearSearch, 500);
        }
    });
    listen(trigger, 'click', () => { if (handle && ['opening', 'open'].includes(handle.state)) void close('button'); else open(); });
    listen(trigger, 'keydown', event => { if (event.defaultPrevented || event.isComposing || event.keyCode === 229) return; if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); open({ focus: event.key === 'ArrowUp' ? 'last' : 'first' }); } });
    const observer = new doc.defaultView.MutationObserver(() => { if (!trigger.isConnected) destroy(); }); observer.observe(doc.documentElement, { childList: true, subtree: true });
    trigger.setAttribute('aria-haspopup', 'menu'); trigger.setAttribute('aria-controls', root.id); trigger.setAttribute('aria-expanded', 'false');
    const binding = { root, open, close, destroy, get handle() { return handle; } }; bindings.set(trigger, binding); roots.set(root, binding); return binding;
}
