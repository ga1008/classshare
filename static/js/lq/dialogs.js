import { createComponent, componentMarkup } from './components.js';
import { attributesMarkup, escapeHtml, normalizeAttributes } from './html.js';
import { getLayerSystem } from './layer.js';

const TYPES = ['modal', 'sheet', 'drawer', 'popover'];
const SIZES = { modal: ['sm', 'md', 'lg', 'xl', 'full'], sheet: ['md'], drawer: ['md', 'wide'], popover: ['md'] };
const identities = Symbol.for('lanshare.lq.dialog-identities');
const creations = new WeakMap();
const bindings = new WeakMap();
const string = (value, fallback = '') => {
    if (value === undefined) return fallback;
    if (typeof value !== 'string') throw new TypeError('LQ dialog text must be a string');
    return value;
};
const flag = (value, fallback) => {
    if (value === undefined) return fallback;
    if (typeof value !== 'boolean') throw new TypeError('LQ dialog flags must be boolean');
    return value;
};
const nodeSlot = (value, doc) => doc && value && [1, 3, 11].includes(value.nodeType) && value.ownerDocument === doc;

/** The HTML and SSR entries accept text only; only the DOM factory takes Nodes. */
export function dialogProps(props = {}, doc) {
    if (!props || typeof props !== 'object' || Array.isArray(props)) throw new TypeError('LQ dialog props must be a mapping');
    const keys = new Set(['id', 'type', 'size', 'side', 'title', 'body', 'footer', 'closeLabel', 'closeButton', 'attrs']);
    if (Object.keys(props).some(key => !keys.has(key))) throw new TypeError('Unknown LQ dialog prop');
    const type = props.type === undefined ? 'modal' : props.type;
    if (!TYPES.includes(type)) throw new TypeError('Invalid LQ dialog type');
    const size = props.size === undefined ? 'md' : props.size;
    if (!SIZES[type].includes(size)) throw new TypeError('Invalid LQ dialog size');
    const side = props.side === undefined ? (type === 'sheet' ? 'bottom' : 'right') : props.side;
    if (!['bottom', 'right'].includes(side) || (type !== 'sheet' && props.side !== undefined)) throw new TypeError('Invalid LQ dialog side');
    const title = string(props.title).trim();
    if (!title) throw new TypeError('LQ dialog needs a title');
    let id = props.id;
    if (id === undefined && doc) {
        do { id = `lq-dialog-${doc[identities] = (doc[identities] || 0) + 1}`; } while (doc.getElementById(id));
    }
    if (typeof id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]*$/.test(id) || id.includes('--lq-')) throw new TypeError('LQ dialog needs a safe unique id');
    const body = nodeSlot(props.body, doc) ? props.body : string(props.body);
    const footer = nodeSlot(props.footer, doc) ? props.footer : string(props.footer);
    if (doc && [body, footer].some(value => value === doc.body || value === doc.documentElement)) throw new TypeError('LQ slots cannot move document roots');
    if (typeof body !== 'string' && typeof footer !== 'string' && (body === footer || body.contains(footer) || footer.contains(body))) throw new TypeError('LQ dialog slots cannot overlap');
    const attrs = normalizeAttributes(props.attrs);
    for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || ['id', 'aria-label', 'aria-labelledby', 'aria-describedby'].includes(key)) delete attrs[key];
    const closeLabel = string(props.closeLabel, '关闭').trim();
    if (!closeLabel) throw new TypeError('LQ close button needs a name');
    const closeButton = flag(props.closeButton, true);
    return { id, type, size, side, title, body, footer, closeLabel, closeButton,
        rootAttrs: { ...attrs, id, class: 'lq-dialog-root', 'data-lq-dialog': type, hidden: '' },
        surfaceAttrs: { class: `lq-dialog__surface lq-${type} lq-${type}--${size}${type === 'sheet' ? ` lq-sheet--${side}` : ''} lq-glass${type === 'popover' ? '' : ' lq-glass--thick'}`,
            role: 'dialog', tabindex: '-1', 'aria-labelledby': `${id}--lq-title`, ...(body ? { 'aria-describedby': `${id}--lq-body` } : {}), 'data-ui-overlay-surface': '' } };
}

const closeProps = p => ({ icon: 'x', variant: 'ghost', attrs: { 'aria-label': p.closeLabel, 'data-lq-dialog-close': '' } });
export function dialogMarkup(props = {}) {
    const p = dialogProps(props);
    return `<div${attributesMarkup(p.rootAttrs)}>${p.type === 'popover' ? '' : '<div class="lq-scrim" aria-hidden="true"></div>'}<section${attributesMarkup(p.surfaceAttrs)}><div class="lq-dialog__grip" aria-hidden="true"></div><header class="lq-dialog__head"><h2 id="${escapeHtml(p.id)}--lq-title">${escapeHtml(p.title)}</h2>${p.closeButton ? componentMarkup('button', closeProps(p)) : ''}</header><div class="lq-dialog__body" id="${escapeHtml(p.id)}--lq-body">${escapeHtml(p.body)}</div><footer class="lq-dialog__foot">${escapeHtml(p.footer)}</footer></section></div>`;
}

export function createDialog(props = {}, doc = document) {
    const p = dialogProps(props, doc);
    const create = (tag, attrs = {}, text) => {
        const node = doc.createElement(tag);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const root = create('div', p.rootAttrs), surface = create('section', p.surfaceAttrs);
    if (p.type !== 'popover') root.append(create('div', { class: 'lq-scrim', 'aria-hidden': 'true' }));
    root.append(surface);
    surface.append(create('div', { class: 'lq-dialog__grip', 'aria-hidden': 'true' }));
    const head = create('header', { class: 'lq-dialog__head' });
    head.append(create('h2', { id: `${p.id}--lq-title` }, p.title));
    if (p.closeButton) head.append(createComponent('button', closeProps(p), doc));
    surface.append(head);
    const slots = [];
    for (const [name, value] of [['body', p.body], ['foot', p.footer]]) {
        const container = create(name === 'foot' ? 'footer' : 'div', { class: `lq-dialog__${name}`, ...(name === 'body' ? { id: `${p.id}--lq-body` } : {}) });
        surface.append(container);
        if (typeof value === 'string') container.textContent = value;
        else {
            const nodes = value.nodeType === 11 ? [...value.childNodes] : [value];
            for (const node of nodes) slots.push({ node, parent: node.parentNode, next: node.nextSibling });
            container.append(value);
        }
    }
    creations.set(root, { slots });
    return root;
}

function releaseSlots(root) {
    const owned = creations.get(root);
    if (!owned) return;
    for (const { node, parent, next } of [...owned.slots].reverse()) if (parent && root.contains(node)) {
        // Returning to a detached original parent does not resurrect its page.
        parent.insertBefore(node, next?.parentNode === parent ? next : null);
    }
    creations.delete(root); root.remove();
}

/** Reclaims a never-opened factory root as well as an active dialog. */
export function disposeDialog(root) {
    const binding = bindings.get(root);
    if (binding) binding.handle.destroy();
    else releaseSlots(root);
}

export function openDialog(elementOrProps, options = {}, doc = document) {
    const createdHere = elementOrProps?.nodeType !== 1;
    const root = createdHere ? createDialog(elementOrProps, doc) : elementOrProps;
    doc = root.ownerDocument;
    const type = root.dataset.lqDialog;
    const surface = root.querySelector(':scope > .lq-dialog__surface');
    if (!TYPES.includes(type) || !surface) throw new TypeError('LQ openDialog requires an LQ dialog structure');
    if (type === 'popover' && (options.anchor?.nodeType !== 1 || options.anchor.ownerDocument !== doc)) {
        if (createdHere) releaseSlots(root);
        throw new TypeError('LQ popover requires an anchor in its document');
    }
    const layer = getLayerSystem(doc);
    const previous = bindings.get(root);
    if (previous) root.removeEventListener('click', previous.click);
    let handle;
    const cleanup = () => {
        root.removeEventListener('click', click);
        if (bindings.get(root)?.click === click) bindings.delete(root);
        releaseSlots(root);
    };
    const click = event => {
        const target = event.target.closest?.('[data-lq-dialog-close]');
        if (target && root.contains(target)) { event.preventDefault(); void layer.close(handle, 'button'); }
    };
    try {
        handle = layer.open(root, { ...options, type, surface,
            onClose: (reason, current) => { cleanup(); options.onClose?.(reason, current); },
            onDestroy: (reason, current) => { cleanup(); options.onDestroy?.(reason, current); },
        });
        if (!['closed', 'destroyed'].includes(handle.state)) {
            root.addEventListener('click', click); bindings.set(root, { handle, click });
        }
        return handle;
    } catch (error) { cleanup(); throw error; }
}

/** Validate actions before constructing or moving any DOM. */
export function choiceProps(props) {
    const title = string(props.title).trim(), message = string(props.message), cancelLabel = string(props.cancelLabel, '返回').trim();
    if (!title || !cancelLabel || !Array.isArray(props.choices) || props.choices.length < 1 || props.choices.length > 3) throw new TypeError('LQ choose requires a title and one to three choices');
    const seen = new Set();
    const choices = props.choices.map(choice => {
        const value = string(choice.value), label = string(choice.label).trim();
        if (!value || !label || seen.has(value)) throw new TypeError('LQ choices need distinct values and labels');
        seen.add(value);
        return { value, label, danger: flag(choice.danger, false), disabled: flag(choice.disabled, false) };
    });
    return { title, message, cancelLabel, choices };
}

export function choose(props, options = {}, doc = document) {
    const p = choiceProps(props);
    const footer = doc.createDocumentFragment();
    const cancel = createComponent('button', { label: p.cancelLabel, variant: 'ghost', attrs: { 'data-lq-dialog-cancel': '' } }, doc);
    footer.append(cancel);
    const actions = p.choices.map(choice => {
        const node = createComponent('button', { label: choice.label, variant: choice.danger ? 'destructive' : 'prominent', disabled: choice.disabled }, doc);
        footer.append(node); return { node, choice };
    });
    const root = createDialog({ title: p.title, body: p.message, footer, size: 'sm' }, doc);
    root.querySelector('.lq-dialog__surface').classList.add('lq-confirm');
    let resolve, settled = false, pending = null, chosen = null, handle;
    const result = new Promise(done => { resolve = done; });
    const finish = value => { if (!settled) { settled = true; root.removeEventListener('click', click); resolve(value); } };
    const dismissed = () => ({ status: 'dismissed' });
    const click = event => {
        if (settled || pending || !handle || ['checking', 'closing'].includes(handle.state)) return;
        const action = actions.find(({ node, choice }) => !choice.disabled && node.contains(event.target));
        if (!action && !cancel.contains(event.target)) return;
        event.preventDefault(); chosen = action ? action.choice.value : null;
        pending = getLayerSystem(doc).close(handle, 'button');
        void pending.then(closed => { pending = null; if (!closed && !settled) chosen = null; });
    };
    root.addEventListener('click', click);
    try {
        handle = openDialog(root, { ...options, initialFocus: options.initialFocus ?? cancel,
            onClose: (reason, current) => { finish(reason === 'button' && chosen !== null ? { status: 'chosen', value: chosen } : dismissed()); options.onClose?.(reason, current); },
            onDestroy: (reason, current) => { finish(dismissed()); options.onDestroy?.(reason, current); },
        }, doc);
    } catch (error) { root.removeEventListener('click', click); disposeDialog(root); throw error; }
    Object.defineProperties(result, { handle: { value: handle }, destroy: { value: () => handle.destroy() } });
    return result;
}

export function confirm(props, options = {}, doc = document) {
    const result = choose({ title: props.title, message: props.message, cancelLabel: props.cancelLabel ?? '取消',
        choices: [{ value: 'confirmed', label: string(props.confirmLabel, '确认'), danger: flag(props.danger, false) }] }, options, doc);
    const confirmation = result.then(value => value.status === 'chosen');
    Object.defineProperties(confirmation, { handle: { value: result.handle }, destroy: { value: result.destroy } });
    return confirmation;
}
