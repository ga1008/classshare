/** Explicit adapters for domain-owned controls. Never scans or observes a page.
 * Business code keeps nodes, events, values and transactions; LQ owns the leaf
 * presentation. A choice may contain domain slots instead of a plain label.
 */
import { componentProps } from './component-props.js';
import { createComponent } from './components.js';
import { attributesMarkup } from './html.js';
import { createPopoverSystem } from '../ui_popover.js';

const kinds = new Set(['button', 'choice', 'input', 'textarea', 'select', 'range', 'checkbox', 'radio']);
const nativeClasses = ['lq-input', 'lq-textarea', 'lq-select', 'lq-range', 'lq-checkbox', 'lq-radio', 'lq-native-file', 'lq-native-color'];
const inferredKind = node => node.tagName === 'BUTTON' ? 'button'
    : node.tagName === 'TEXTAREA' ? 'textarea' : node.tagName === 'SELECT' ? 'select'
        : node.tagName === 'INPUT' ? (['range', 'checkbox', 'radio'].includes(node.type) ? node.type : 'input') : null;
const buttonPresentation = ({ variant = 'glass', size = 'sm' } = {}) =>
    componentProps('button', { label: 'presentation', variant, size });

/** Adopt in place, including caller-assigned type/name/form/hidden/disabled.
 * This does not dispatch events, update values, bind a second controller, or
 * invent an accessible name for a choice whose content is still being built.
 */
export function adoptDomainControl(node, { kind = inferredKind(node), variant = 'glass', size = 'sm' } = {}) {
    if (!kinds.has(kind)) throw new TypeError('Unsupported domain control');
    if (kind === 'button' || kind === 'choice') {
        if (node.tagName !== 'BUTTON' && node.tagName !== 'A') throw new TypeError('Domain button requires a button or link');
        const p = buttonPresentation({ variant, size });
        for (const name of [...node.classList]) if (/^lq-btn--(?:glass|soft|ghost|prominent|destructive|link|sm|md|lg)$/.test(name)) node.classList.remove(name);
        node.classList.add(...p.classes.split(' '));
        node.classList.toggle('lq-domain-choice', kind === 'choice');
    } else {
        if (inferredKind(node) !== kind) throw new TypeError('Domain control kind must match its native type');
        node.classList.remove(...nativeClasses);
        node.classList.add(`lq-${kind}`);
        if (node.tagName === 'INPUT' && ['file', 'color'].includes(node.type)) node.classList.add(`lq-native-${node.type}`);
    }
    node.dataset.lqComponent = kind;
    node.dataset.lqDomainControl = '';
    return node;
}

export function createDomainButton({ label = '', className = '', kind = 'button', variant = 'glass', size = 'sm', attrs = {} } = {}, doc = document) {
    let node;
    if (label) {
        node = createComponent('button', { label, variant, size, attrs }, doc);
        // Business code may replace the visible label (loading, save status).
        // Let that content remain its name unless a caller explicitly owns one.
        if (!attrs['aria-label']) node.removeAttribute('aria-label');
    }
    else {
        // Composite choices are named by their preserved contents or caller's
        // aria-label. A fake placeholder name must never reach accessibility.
        node = doc.createElement('button'); node.type = 'button';
        const p = componentProps('button', { label: 'presentation', variant, size, attrs });
        for (const [key, value] of Object.entries(p.attrs)) {
            if (key === 'aria-label') { if (attrs['aria-label']) node.setAttribute(key, attrs['aria-label']); }
            else node.setAttribute(key, value);
        }
        kind = 'choice';
    }
    if (className) node.classList.add(...className.split(/\s+/).filter(Boolean));
    return adoptDomainControl(node, { kind, variant, size });
}

/** Canvas content is an authored document slot, not a form-field surface.
 * Its font, position, transparency and event semantics belong to the editor.
 */
export function adoptDomainContentSlot(node) {
    node.dataset.lqComponent = 'content-slot';
    node.dataset.lqDomainControl = '';
    node.classList.add('lq-domain-content-slot');
    return node;
}

/** Geometry handles preserve exact hit boxes and glyphs, without pill paint. */
export function adoptDomainHandle(node, { kind } = {}) {
    if (!['resize', 'rotate'].includes(kind)) throw new TypeError('Unsupported domain handle');
    node.dataset.lqComponent = 'handle';
    node.dataset.lqDomainControl = '';
    node.dataset.lqHandle = kind;
    node.dataset.lqShape = 'surface';
    node.classList.add('lq-domain-handle');
    return node;
}

/** Safe attributes for static domain templates; callers keep their authored
 * SVG and data hooks. No raw HTML or style escape hatch enters LQ props.
 */
export function domainButtonAttributes({ label, className = '', kind = 'button', variant = 'glass', size = 'sm', attrs = {}, iconOnly = false } = {}) {
    const p = componentProps('button', { label, variant, size, attrs });
    if (!['button', 'choice'].includes(kind)) throw new TypeError('Invalid domain button kind');
    return attributesMarkup({ ...p.attrs, class: `${p.classes}${iconOnly ? ' lq-btn--icon' : ''}${kind === 'choice' ? ' lq-domain-choice' : ''}${className ? ` ${className}` : ''}`,
        'data-lq-component': kind, 'data-lq-domain-control': '' });
}

/** Keep the existing domain popover lifecycle and its nested action contract.
 * One explicit surface declaration gives it the same material as LQ dialogs;
 * the caller still owns preview/commit, focus hooks and close cleanup.
 */
export function createDomainPopoverSystem(options) {
    const system = createPopoverSystem(options);
    return { ...system, createPopover(props) {
        props.panel.classList.add('lq-domain-popover', 'lq-glass');
        props.panel.dataset.lqComponent = props.modal ? 'dialog' : props.role === 'menu' ? 'menu' : 'popover';
        props.panel.dataset.lqMaterial = 'raised';
        return system.createPopover(props);
    } };
}
