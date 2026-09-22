/** Navigation menu button: a trigger plus the existing lq_menu panel.
 *
 * Item validation, keyboard handling, focus management and `LQ.layer`
 * participation all belong to ./menus.js; this module only adds the trigger
 * shape and pointer-intent open/close on fine pointers.
 */
import { componentProps } from './component-props.js';
import { componentMarkup, createComponent } from './components.js';
import { attributesMarkup } from './html.js';
import { createIcon, iconMarkup } from './icons.js';
import { bindMenu, createMenu, menuMarkup, menuProps } from './menus.js';

const VARIANTS = ['glass', 'soft', 'ghost'];
const TONES = ['primary', 'success', 'warning', 'danger', 'info', 'neutral'];
const SIZES = ['sm', 'md', 'lg'];
const SHAPES = ['capsule', 'rounded'];
const ALIGNMENTS = ['start', 'end'];
const CARET = 'chevron-down';
const KEYS = ['id', 'label', 'items', 'icon', 'variant', 'tone', 'size', 'shape', 'align'];
const HOSTS = Symbol.for('lanshare.lq.nav-menu-hosts.v1');

const option = (props, key, fallback) => (props[key] === undefined ? fallback : props[key]);
const choice = (value, values, name) => {
    if (typeof value !== 'string' || !values.includes(value)) throw new TypeError(`Invalid LQ nav menu ${name}`);
    return value;
};

function normalize(props) {
    if (!props || typeof props !== 'object' || Array.isArray(props) || Object.keys(props).some(key => !KEYS.includes(key))) throw new TypeError('Invalid LQ nav menu props');
    const menuInput = { id: props.id, label: props.label, items: props.items };
    const menu = menuProps(menuInput);
    const variant = choice(option(props, 'variant', 'glass'), VARIANTS, 'variant');
    const tone = choice(option(props, 'tone', 'neutral'), TONES, 'tone');
    const size = choice(option(props, 'size', 'md'), SIZES, 'size');
    const shape = choice(option(props, 'shape', 'capsule'), SHAPES, 'shape');
    const align = choice(option(props, 'align', 'start'), ALIGNMENTS, 'align');
    // The shared button contract owns icon names, escaping and accessible names.
    const buttonInput = {
        label: menu.label, variant, size, ...(props.icon === undefined ? {} : { icon: props.icon }), id: `${menu.id}--lq-trigger`,
        attrs: { 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': menu.id, 'data-lq-nav-trigger': '', 'data-tone': tone, 'data-lq-nav-shape': shape },
    };
    const trigger = componentProps('button', buttonInput);
    trigger.classes = `lq-nav-menu__trigger ${trigger.classes}`;
    const p = {
        attrs: { class: 'lq-nav-menu', 'data-lq-nav-menu': '', 'data-lq-nav-align': align },
        trigger, menu, caret: CARET, id: menu.id, label: menu.label, variant, tone, size, shape, align,
    };
    return { p, menuInput, buttonInput };
}

export function navMenuProps(props = {}) { return normalize(props).p; }

export function navMenuMarkup(props = {}) {
    const { p, menuInput, buttonInput } = normalize(props);
    // The button renderer emits its class attribute first, so this reaches the
    // trigger and never an icon's class; the caret is the trigger's last child.
    const trigger = componentMarkup('button', buttonInput)
        .replace('class="', 'class="lq-nav-menu__trigger ')
        .replace(/<\/button>$/, `<span class="lq-nav-menu__caret" aria-hidden="true">${iconMarkup(p.caret)}</span></button>`);
    return `<div${attributesMarkup(p.attrs)}>${trigger}${menuMarkup(menuInput)}</div>`;
}

export function createNavMenu(props = {}, doc = document) {
    const { p, menuInput, buttonInput } = normalize(props);
    const host = doc.createElement('div');
    for (const [key, value] of Object.entries(p.attrs)) host.setAttribute(key, value);
    const trigger = createComponent('button', buttonInput, doc);
    trigger.setAttribute('class', p.trigger.classes);
    const caret = doc.createElement('span');
    caret.setAttribute('class', 'lq-nav-menu__caret');
    caret.setAttribute('aria-hidden', 'true');
    caret.append(createIcon(p.caret, doc));
    trigger.append(caret);
    host.append(trigger, createMenu(menuInput, doc));
    return host;
}

function control(host, doc, view, hoverOpenDelay, hoverCloseDelay, options) {
    const trigger = host.querySelector('[data-lq-nav-trigger]');
    const panel = trigger && doc.getElementById(trigger.getAttribute('aria-controls') || '');
    if (!trigger || !panel) throw new TypeError('LQ nav menu needs a trigger and its menu panel');
    const pointer = view.matchMedia('(hover: hover) and (pointer: fine)');
    const motion = view.matchMedia('(prefers-reduced-motion: reduce)');
    let openTimer = null, closeTimer = null, inTrigger = false, inPanel = false, hovering = false;
    const binding = bindMenu(trigger, panel, {
        ...options,
        // Pointer intent must not move focus: hovering a nav menu would otherwise
        // pull focus out of whatever the reader was using and hand it back to the
        // trigger on leave. Keyboard and click opens keep bindMenu's own focus.
        onInitialFocus: (event, layer) => { if (hovering) event.preventDefault(); options.onInitialFocus?.(event, layer); },
        onReturnFocus: (event, layer) => { if (layer?.closeReason === 'pointer') event.preventDefault(); options.onReturnFocus?.(event, layer); },
    });
    const opened = () => Boolean(binding.handle) && ['opening', 'open'].includes(binding.handle.state);
    const wait = value => (motion.matches ? 0 : value);
    function enter() {
        if (!pointer.matches) return;
        view.clearTimeout(closeTimer); closeTimer = null;
        if (opened() || openTimer !== null) return;
        openTimer = view.setTimeout(() => {
            openTimer = null;
            if (!(inTrigger || inPanel) || opened()) return;
            hovering = true;
            try { binding.open(); } finally { hovering = false; }
        }, wait(hoverOpenDelay));
    }
    function leave() {
        if (!pointer.matches) return;
        view.clearTimeout(openTimer); openTimer = null;
        view.clearTimeout(closeTimer);
        // Travelling from the trigger onto the panel fires leave then enter; this
        // delay is exactly what keeps the menu open across that gap.
        closeTimer = view.setTimeout(() => {
            closeTimer = null;
            if (!inTrigger && !inPanel && opened()) void binding.close('pointer');
        }, wait(hoverCloseDelay));
    }
    const listeners = [];
    const listen = (node, name, listener) => { node.addEventListener(name, listener); listeners.push(() => node.removeEventListener(name, listener)); };
    const track = (node, inside) => {
        listen(node, 'pointerenter', event => { if (event.pointerType === 'touch') return; inside(true); enter(); });
        listen(node, 'pointerleave', event => { if (event.pointerType === 'touch') return; inside(false); leave(); });
    };
    track(trigger, value => { inTrigger = value; });
    track(panel, value => { inPanel = value; });
    return {
        host, count: 0, trigger, binding,
        destroy() {
            view.clearTimeout(openTimer); view.clearTimeout(closeTimer); openTimer = closeTimer = null;
            for (const remove of listeners) remove();
            binding.destroy();
        },
    };
}

/** Enhance every `[data-lq-nav-menu]` under `root`; ownership is reference counted. */
export function enhanceNavMenus(root = document, { hoverOpenDelay = 120, hoverCloseDelay = 220, ...options } = {}) {
    const doc = root.ownerDocument || root;
    const view = doc.defaultView;
    if (!view || typeof root.querySelectorAll !== 'function') throw new TypeError('LQ nav menus need a live document root');
    for (const value of [hoverOpenDelay, hoverCloseDelay]) {
        if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) throw new TypeError('LQ nav menu hover delays must be finite milliseconds');
    }
    const owners = doc[HOSTS] ||= new WeakMap();
    const hosts = [...(root.matches?.('[data-lq-nav-menu]') ? [root] : []), ...root.querySelectorAll('[data-lq-nav-menu]')];
    const taken = [];
    for (const host of hosts) {
        let owner = owners.get(host);
        if (!owner) { owner = control(host, doc, view, hoverOpenDelay, hoverCloseDelay, options); owners.set(host, owner); }
        owner.count++;
        taken.push(owner);
    }
    let disposed = false;
    return {
        menus: taken.map(owner => owner.binding),
        dispose() {
            if (disposed) return;
            disposed = true;
            for (const owner of taken) if (--owner.count === 0) { owners.delete(owner.host); owner.destroy(); }
        },
    };
}
