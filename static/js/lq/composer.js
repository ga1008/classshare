import { componentTree, componentMarkup, createComponent } from './components.js';
import { attributesMarkup, escapeHtml, normalizeAttributes } from './html.js';

export const COMPOSER_KINDS = Object.freeze(['composer']);
const own = (p, key, fallback) => Object.hasOwn(p, key) ? p[key] : fallback;
const mapping = (p, keys) => {
    if (!p || typeof p !== 'object' || Array.isArray(p) || ![Object.prototype, null].includes(Object.getPrototypeOf(p)) || Object.keys(p).some(key => !keys.includes(key))) throw new TypeError('Invalid LQ composer props');
};
const text = (value, required = false) => {
    if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError('LQ composer requires plain text');
    return value;
};
const flag = value => { if (typeof value !== 'boolean') throw new TypeError('LQ composer requires booleans'); return value; };
const enterPolicy = value => { if (!['newline', 'send'].includes(value)) throw new TypeError('Invalid LQ composer Enter policy'); return value; };
function state(p) {
    return { disabled: flag(own(p, 'disabled', false)), busy: flag(own(p, 'busy', false)), hasContent: flag(own(p, 'hasContent', false)), enter: enterPolicy(own(p, 'enter', 'newline')) };
}
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
function button(label, icon, variant, type, disabled, form, hook) {
    const props = { label: '', icon, variant, type, disabled, attrs: { 'aria-label': label, [hook]: '', ...(form ? { form } : {}) } };
    componentTree('button', props); return { button: props };
}

/** A presentation group inside a caller-owned form, or explicitly associated by form=id. */
export function composerProps(kind, p = {}) {
    if (kind !== 'composer') throw new TypeError('Unknown LQ composer');
    mapping(p, ['id', 'attrs', 'label', 'name', 'form', 'value', 'placeholder', 'required', 'maxlength', 'disabled', 'busy', 'hasContent', 'enter', 'attachment', 'emoji', 'sendLabel', 'submit_name', 'submit_value']);
    const s = state(p), value = text(own(p, 'value', '')), label = text(own(p, 'label', '消息内容'), true), name = text(own(p, 'name', 'content'), true);
    const form = p.form === undefined ? '' : text(p.form, true), required = flag(own(p, 'required', false));
    const attachment = own(p, 'attachment', '添加附件'), emoji = own(p, 'emoji', '表情');
    for (const item of [attachment, emoji]) if (item !== null) text(item, true);
    const attrs = Object.fromEntries(Object.entries(normalizeAttributes(p.attrs)).filter(([key]) => !key.startsWith('data-lq-') && !['aria-busy', 'role'].includes(key)));
    if (p.id !== undefined) attrs.id = text(p.id, true);
    Object.assign(attrs, { class: 'lq-composer lq-glass', 'data-lq-composer': '', 'data-lq-enter': s.enter, 'data-lq-disabled': String(s.disabled), 'data-lq-busy': String(s.busy), 'data-lq-has-content': String(s.hasContent), 'aria-busy': String(s.busy) });
    const input = { class: 'lq-composer__input', 'data-lq-composer-input': '', 'aria-label': label, name, rows: '1', placeholder: text(own(p, 'placeholder', '输入消息…')), ...(form ? { form } : {}), ...(required ? { required: '' } : {}), ...(s.disabled ? { disabled: '' } : {}), ...(s.busy ? { readonly: '' } : {}) };
    if (p.maxlength !== undefined) { if (!Number.isSafeInteger(p.maxlength) || p.maxlength < 1) throw new TypeError('Invalid LQ composer maxlength'); input.maxlength = String(p.maxlength); }
    const send = button(text(own(p, 'sendLabel', '发送'), true), 'send', 'prominent', 'submit', s.disabled || s.busy || (!value.trim() && !s.hasContent), form, 'data-lq-composer-send');
    // P1 does not expose arbitrary value attributes; these two native submitter fields are owned here.
    if (p.submit_name !== undefined) { send.submit_name = text(p.submit_name, true); send.submit_value = text(own(p, 'submit_value', '')); }
    else if (p.submit_value !== undefined) throw new TypeError('Composer submit_value requires submit_name');
    const tools = [];
    if (attachment !== null) tools.push(button(attachment, 'paperclip', 'ghost', 'button', s.disabled || s.busy, form, 'data-lq-composer-attachment'));
    if (emoji !== null) tools.push(button(emoji, 'smile', 'ghost', 'button', s.disabled || s.busy, form, 'data-lq-composer-emoji'));
    return node('div', attrs, [node('div', { class: 'lq-composer__content', 'data-lq-composer-content': '' }, [{ slot: 'content' }]), node('textarea', input, [value]), node('div', { class: 'lq-composer__actions' }, [node('div', { class: 'lq-composer__tools' }, tools), send])]);
}
function markup(n) {
    if (typeof n === 'string') return escapeHtml(n);
    if (n.slot) return '';
    if (n.button) {
        const html = componentMarkup('button', n.button);
        return n.submit_name === undefined ? html : html.replace('<button', `<button${attributesMarkup({ name: n.submit_name, value: n.submit_value })}`);
    }
    return `<${n.tag}${attributesMarkup(n.attrs)}>${n.tag === 'textarea' ? '\n' : ''}${n.children.map(markup).join('')}</${n.tag}>`;
}
export const composerMarkup = (kind, props = {}) => markup(composerProps(kind, props));
export const html = Object.freeze({ composer: props => composerMarkup('composer', props) });
export function createComposer(kind, props = {}, slots = {}, doc = document) {
    const tree = composerProps(kind, props); mapping(slots, ['content']);
    const nodes = slots.content ?? [];
    const valid = n => n instanceof doc.defaultView.Node && n.ownerDocument === doc && [1, 3, 11].includes(n.nodeType) && !(n instanceof doc.defaultView.ShadowRoot) && !n.matches?.('html,head,body') && [...n.childNodes].every(valid);
    if (!Array.isArray(nodes) || nodes.some(n => !valid(n)) || new Set(nodes).size !== nodes.length || nodes.some(a => nodes.some(b => a !== b && a.contains(b)))) throw new TypeError('Composer content requires distinct, same-document content Nodes');
    const build = n => {
        if (typeof n === 'string') return doc.createTextNode(n);
        if (n.slot) { const fragment = doc.createDocumentFragment(); fragment.append(...nodes); return fragment; }
        if (n.button) { const result = createComponent('button', n.button, doc); if (n.submit_name !== undefined) { result.name = n.submit_name; result.value = n.submit_value; } return result; }
        const result = doc.createElement(n.tag); for (const [key, value] of Object.entries(n.attrs)) result.setAttribute(key, value);
        result.append(...n.children.map(build)); return result;
    };
    return build(tree);
}
export const composer = (props, slots, doc) => createComposer('composer', props, slots, doc);

const ownerKey = Symbol.for('lanshare.lq.composer-owner'), fixedKey = Symbol.for('lanshare.lq.composer-fixed-owner');
/** No sending callback, request, queue, successful-clear assumption or document scanning. */
export function enhanceComposer(root, options = {}) {
    if (!root?.matches?.('[data-lq-composer]') || !root.isConnected) throw new TypeError('Connected composer root required');
    mapping(options, ['fixed']);
    if (root[ownerKey]) return root[ownerKey];
    const doc = root.ownerDocument, win = doc.defaultView, input = root.querySelector('[data-lq-composer-input]'), send = root.querySelector('[data-lq-composer-send]');
    if (!input || !send || input.closest('[data-lq-composer]') !== root || send.closest('[data-lq-composer]') !== root) throw new TypeError('Composer input and submitter required');
    const fixed = options.fixed;
    if (fixed !== undefined) {
        mapping(fixed, ['container', 'contentRoot']);
        if (root.parentElement !== fixed.container || !fixed.container?.contains(fixed.contentRoot) || fixed.contentRoot.contains(root) || root.contains(fixed.contentRoot) || fixed.contentRoot.ownerDocument !== doc || win.getComputedStyle(fixed.container).position === 'static' || fixed.container[fixedKey] || fixed.contentRoot[fixedKey]) throw new TypeError('Fixed composer requires an unowned positioned parent and separate content root');
    }
    let current = state({ disabled: root.dataset.lqDisabled === 'true', busy: root.dataset.lqBusy === 'true', hasContent: root.dataset.lqHasContent === 'true', enter: root.dataset.lqEnter });
    let disposed = false, composing = false, committed = false, frame = 0;
    const tools = [...root.querySelectorAll('[data-lq-composer-attachment],[data-lq-composer-emoji]')], releases = [], saved = new Map();
    function save(el, name) { if (!saved.has(el)) saved.set(el, new Map()); if (!saved.get(el).has(name)) saved.get(el).set(name, el.getAttribute(name)); }
    function attr(el, name, value) { save(el, name); if (value === null) el.removeAttribute(name); else el.setAttribute(name, value); }
    const styles = new Map(), hadStyle = new Map();
    function style(el, key, value) { if (!styles.has(el)) { styles.set(el, new Map()); hadStyle.set(el, el.hasAttribute('style')); } if (!styles.get(el).has(key)) styles.get(el).set(key, [el.style.getPropertyValue(key), el.style.getPropertyPriority(key)]); el.style.setProperty(key, value); }
    const listen = (target, event, fn, capture = false) => { target.addEventListener(event, fn, capture); releases.push(() => target.removeEventListener(event, fn, capture)); };
    const blocked = () => current.disabled || current.busy || (!input.value.trim() && !current.hasContent) || input.matches(':disabled');
    function sync() {
        if (disposed) return;
        attr(input, 'disabled', current.disabled ? '' : null); attr(input, 'readonly', current.busy ? '' : null);
        for (const el of [send, ...tools]) {
            const disabled = current.disabled || current.busy || (el === send && blocked());
            attr(el, 'disabled', disabled ? '' : null); attr(el, 'aria-disabled', disabled ? 'true' : null); attr(el, 'data-lq-disabled', disabled ? 'true' : null);
            // P1 classes were derived from initial SSR state; reconcile the same class without replacing nodes.
            save(el, 'class'); el.classList.toggle('is-disabled', disabled);
        }
        for (const [key, value] of Object.entries({ 'data-lq-disabled': current.disabled, 'data-lq-busy': current.busy, 'data-lq-has-content': current.hasContent, 'aria-busy': current.busy, 'data-lq-enter': current.enter })) attr(root, key, String(value));
        resize();
    }
    function resize() {
        if (disposed || !root.isConnected) return;
        const css = win.getComputedStyle(input), line = parseFloat(css.lineHeight) || parseFloat(css.fontSize) * 1.5;
        const extra = (parseFloat(css.paddingTop) || 0) + (parseFloat(css.paddingBottom) || 0) + (parseFloat(css.borderTopWidth) || 0) + (parseFloat(css.borderBottomWidth) || 0);
        const maximum = line * 6 + extra, scroll = input.scrollTop;
        style(input, 'height', '0px'); style(input, 'height', `${Math.min(maximum, Math.max(line + extra, input.scrollHeight + (parseFloat(css.borderTopWidth) || 0) + (parseFloat(css.borderBottomWidth) || 0)))}px`);
        style(input, 'overflow-y', input.scrollHeight > maximum + 1 ? 'auto' : 'hidden');
        input.scrollTop = scroll;
        if (fixed) viewport();
    }
    function viewport() {
        if (disposed || !fixed) return;
        const vv = win.visualViewport, focused = root.contains(doc.activeElement);
        const keyboard = focused && vv && Math.abs(vv.scale - 1) < .01 ? Math.max(0, fixed.container.getBoundingClientRect().bottom - (vv.offsetTop + vv.height)) : 0;
        style(root, '--lq-composer-keyboard', `${keyboard}px`);
        style(fixed.contentRoot, '--lq-composer-keyboard', `${keyboard}px`);
        style(fixed.contentRoot, '--lq-composer-h', `${root.getBoundingClientRect().height}px`);
    }
    const schedule = () => { if (!disposed && !frame) frame = win.requestAnimationFrame(() => { frame = 0; resize(); }); };
    listen(input, 'input', sync);
    listen(input, 'compositionstart', () => { composing = true; });
    listen(input, 'compositionend', () => { composing = false; committed = true; win.queueMicrotask(() => { committed = false; }); });
    listen(input, 'keydown', event => {
        if (event.defaultPrevented || event.key !== 'Enter' || current.enter !== 'send' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.repeat || event.isComposing || event.keyCode === 229 || composing || committed) return;
        if (input.form && input.form === send.form) { event.preventDefault(); if (!blocked()) input.form.requestSubmit(send); }
    });
    listen(send, 'click', event => { if (blocked()) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
    // A form may contain other submitters: only this composer's actual submitter belongs to this guard.
    if (send.form) {
        listen(send.form, 'submit', event => { if (event.submitter === send && blocked()) { event.preventDefault(); event.stopImmediatePropagation(); } }, true);
        listen(send.form, 'reset', () => win.queueMicrotask(() => { if (!disposed) sync(); }));
    }
    const observer = win.ResizeObserver ? new win.ResizeObserver(schedule) : null;
    observer?.observe(root);
    listen(win, 'resize', schedule);
    if (fixed) {
        attr(root, 'data-lq-composer-fixed', ''); attr(fixed.contentRoot, 'data-lq-composer-scroll', '');
        observer?.observe(fixed.container);
        if (win.visualViewport) { listen(win.visualViewport, 'resize', schedule); listen(win.visualViewport, 'scroll', schedule); }
        listen(root, 'focusin', schedule); listen(root, 'focusout', schedule);
    }
    const handle = { refresh: sync, set(patch) {
        if (disposed) return;
        mapping(patch, ['disabled', 'busy', 'hasContent', 'enter']); const next = state({ ...current, ...patch }); current = next; sync();
    }, destroy() {
        if (disposed) return; disposed = true; releases.reverse().forEach(fn => fn()); observer?.disconnect(); if (frame) win.cancelAnimationFrame(frame);
        for (const [el, values] of saved) for (const [key, value] of values) if (value === null) el.removeAttribute(key); else el.setAttribute(key, value);
        for (const [el, values] of styles) for (const [key, [value, priority]] of values) if (value) el.style.setProperty(key, value, priority); else el.style.removeProperty(key);
        for (const [el, existed] of hadStyle) if (!existed && !el.style.length) el.removeAttribute('style');
        if (fixed) for (const el of [fixed.container, fixed.contentRoot]) if (el[fixedKey] === handle) delete el[fixedKey];
        if (root[ownerKey] === handle) delete root[ownerKey];
    } };
    root[ownerKey] = handle; if (fixed) { fixed.container[fixedKey] = handle; fixed.contentRoot[fixedKey] = handle; } sync(); return handle;
}
