import { navigationProps, segment } from './navigation.js';
import { attributesMarkup, escapeHtml, normalizeAttributes } from './html.js';

export const workspaceKinds = Object.freeze(['split', 'viewer']);
const ownerKey = Symbol.for('lanshare.lq.split.v1');
const node = (tag, attrs, children = []) => ({ tag, attrs, children });
const text = value => {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError('Workspace needs a text name');
    return value;
};
const identity = value => {
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(text(value)) || value.includes('--lq-')) throw new TypeError('Workspace needs a non-reserved id');
    return value;
};
const integer = (value, fallback) => {
    const result = value === undefined ? fallback : value;
    if (!Number.isSafeInteger(result) || result < 160 || result > 3200) throw new TypeError('Workspace size must be a bounded integer');
    return result;
};

export function workspaceProps(kind, props = {}) {
    if (!workspaceKinds.includes(kind) || !props || typeof props !== 'object' || Array.isArray(props)) throw new TypeError('Invalid workspace props');
    const allowed = kind === 'split' ? ['id', 'label', 'sideLabel', 'mainLabel', 'width', 'min', 'max', 'mainMin', 'selected', 'attrs'] : ['id', 'title', 'kind', 'attrs'];
    if (Object.keys(props).some(key => !allowed.includes(key))) throw new TypeError('Unknown workspace prop');
    const id = identity(props.id), attrs = normalizeAttributes(props.attrs);
    for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || ['id', 'role', 'aria-label', 'aria-labelledby', 'aria-hidden', 'aria-live'].includes(key)) delete attrs[key];
    if (kind === 'viewer') {
        const type = props.kind === undefined ? 'document' : props.kind;
        if (!['document', 'iframe'].includes(type)) throw new TypeError('Unknown viewer kind');
        return node('section', { ...attrs, id, class: `lq-viewer lq-viewer--${type}`, 'aria-labelledby': `${id}--lq-title` }, [
            node('header', { class: 'lq-viewer__toolbar lq-glass' }, [node('h2', { id: `${id}--lq-title`, class: 'lq-viewer__title' }, [text(props.title)]), node('div', { class: 'lq-viewer__actions' }, [{ slot: 'actions', text: '' }])]),
            node('div', { class: 'lq-viewer__content' }, [{ slot: 'content', text: '' }]),
        ]);
    }
    const min = integer(props.min, 160), max = integer(props.max, 640), width = integer(props.width, 280), mainMin = integer(props.mainMin, 320);
    if (min > max || width < min || width > max) throw new TypeError('Invalid workspace width bounds');
    const selected = props.selected === undefined ? 'main' : props.selected;
    const tree = navigationProps('segment', { id, label: text(props.label), selected, items: [{ key: 'side', label: text(props.sideLabel) }, { key: 'main', label: text(props.mainLabel) }] });
    Object.assign(tree.attrs, attrs, { class: 'lq-split lq-tabs lq-segment', 'data-lq-split': '', 'data-lq-split-width': String(width), 'data-lq-split-min': String(min), 'data-lq-split-max': String(max), 'data-lq-split-main-min': String(mainMin), 'data-lq-split-selected': selected });
    delete tree.attrs['data-lq-tabs'];
    tree.children[0].attrs.hidden = '';
    const panels = tree.children[1]; panels.attrs.class = 'lq-split__panes';
    for (let index = 0; index < panels.children.length; index++) {
        const panel = panels.children[index], key = index ? 'main' : 'side', label = index ? props.mainLabel : props.sideLabel;
        Object.assign(panel.attrs, { class: `lq-split__pane lq-split__pane--${key}`, role: 'region', 'aria-labelledby': `${id}--lq-heading-${key}`, 'data-lq-split-pane': key });
        delete panel.attrs.hidden;
        panel.children.unshift(node('h2', { id: `${id}--lq-heading-${key}`, class: 'lq-split__heading' }, [label]));
    }
    panels.children.splice(1, 0, node('div', { class: 'lq-split__separator', hidden: '', role: 'separator', tabindex: '0', 'aria-orientation': 'vertical', 'aria-label': `调整${props.sideLabel}宽度`, 'aria-controls': `${id}--lq-panel-side`, 'aria-valuemin': String(min), 'aria-valuemax': String(max), 'aria-valuenow': String(width) }));
    return tree;
}

function markup(tree) {
    if (typeof tree === 'string') return escapeHtml(tree);
    if (tree.slot) return escapeHtml(tree.text);
    return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.children.map(markup).join('')}</${tree.tag}>`;
}
export const workspaceMarkup = (kind, props) => markup(workspaceProps(kind, props));
export const html = Object.freeze(Object.fromEntries(workspaceKinds.map(kind => [kind, props => workspaceMarkup(kind, props)])));
export function createWorkspace(kind, props, slots = {}, doc = document) {
    const tree = workspaceProps(kind, props), allowed = kind === 'split' ? ['side', 'main'] : ['actions', 'content'], nodes = [];
    if (!slots || typeof slots !== 'object' || Array.isArray(slots)) throw new TypeError('Workspace slots must be a mapping');
    for (const [name, value] of Object.entries(slots)) {
        if (!allowed.includes(name) || !(value instanceof doc.defaultView.Node) || value.ownerDocument !== doc || ![1, 3, 11].includes(value.nodeType) || value.host || value === doc.body || value === doc.documentElement) throw new TypeError('Workspace slots require existing content Nodes');
        if (nodes.some(node => node === value || node.contains(value) || value.contains(node))) throw new TypeError('Workspace slots cannot overlap');
        nodes.push(value);
    }
    const element = current => {
        if (typeof current === 'string') return doc.createTextNode(current);
        if (current.slot) return slots[current.slot] || doc.createTextNode(current.text);
        const result = doc.createElement(current.tag);
        for (const [key, value] of Object.entries(current.attrs)) result.setAttribute(key, value);
        result.append(...current.children.map(element)); return result;
    };
    return element(tree);
}

/** The two panels stay in their original parent. Desktop resize has no document
 * mouse listeners; narrow views reuse the existing Segment owner and keyboard. */
export function enhanceSplit(root, { onResize } = {}) {
    if (!root?.matches?.('[data-lq-split]') || (onResize !== undefined && typeof onResize !== 'function')) throw new TypeError('Invalid split binding');
    if (root[ownerKey]) return root[ownerKey];
    const doc = root.ownerDocument, win = doc.defaultView;
    const panes = [...root.querySelectorAll('[data-lq-split-pane]')].filter(pane => pane.closest('[data-lq-split]') === root);
    const list = root.querySelector(':scope > [role=tablist]'), separator = root.querySelector(':scope > .lq-split__panes > .lq-split__separator');
    if (panes.length !== 2 || !list || !separator || panes.map(pane => pane.dataset.lqSplitPane).join(',') !== 'side,main') throw new TypeError('Invalid split structure');
    const min = integer(Number(root.dataset.lqSplitMin)), max = integer(Number(root.dataset.lqSplitMax)), mainMin = integer(Number(root.dataset.lqSplitMainMin));
    let width = integer(Number(root.dataset.lqSplitWidth));
    if (min > width || width > max) throw new TypeError('Invalid split bounds');
    const media = win.matchMedia('(max-width: 1023px)'), coarse = win.matchMedia('(pointer: coarse)');
    const saved = new Map([root, list, separator, ...panes, ...list.querySelectorAll('button')].map(element => [element, [...element.attributes].map(attr => [attr.name, attr.value])]));
    const originalWidth = root.style.getPropertyValue('--lq-split'), originalPriority = root.style.getPropertyPriority('--lq-split');
    let destroyed = false, nav = null, narrow = null, pointer = null, selected = root.dataset.lqSplitSelected, frame = 0, invalidTarget = null, invalidTimer = null;
    const limits = () => ({ min, max: Math.max(min, Math.min(max, root.clientWidth - mainMin - (coarse.matches ? 44 : 12))) });
    const setWidth = (value, reason, notify = true) => {
        if (destroyed || !Number.isFinite(value)) return;
        const bounds = limits(); width = Math.round(Math.max(bounds.min, Math.min(bounds.max, value)));
        root.style.setProperty('--lq-split', `${width}px`);
        separator.setAttribute('aria-valuenow', String(width)); separator.setAttribute('aria-valuemin', String(bounds.min)); separator.setAttribute('aria-valuemax', String(bounds.max));
        if (notify) onResize?.({ width, reason });
    };
    const release = () => {
        const held = pointer; pointer = null;
        if (held !== null && separator.hasPointerCapture?.(held.id)) separator.releasePointerCapture(held.id);
        root.classList.remove('is-resizing');
    };
    const refresh = () => {
        if (destroyed) return;
        const next = media.matches || root.clientWidth < min + mainMin + (coarse.matches ? 44 : 12);
        if (next !== narrow) {
            release();
            if (nav) { selected = nav.value; nav.destroy(); nav = null; }
            narrow = next; root.dataset.lqSplitEnhanced = next ? 'narrow' : 'wide';
            list.hidden = !next; separator.hidden = next;
            if (next) {
                const focused = panes.find(pane => pane.contains(doc.activeElement));
                if (focused) selected = focused.dataset.lqSplitPane;
                root.dataset.lqTabs = '';
                for (const pane of panes) { pane.setAttribute('role', 'tabpanel'); pane.setAttribute('aria-labelledby', `${root.id}--lq-tab-${pane.dataset.lqSplitPane}`); }
                for (const tab of list.querySelectorAll('[data-lq-tab]')) tab.setAttribute('aria-selected', String(tab.dataset.lqTab === selected));
                nav = segment(root, { onChange: detail => { selected = detail.key; } });
                nav.select(selected, { sync: false, reason: 'responsive' });
            } else {
                delete root.dataset.lqTabs;
                for (const pane of panes) { pane.hidden = false; pane.setAttribute('role', 'region'); pane.setAttribute('aria-labelledby', `${root.id}--lq-heading-${pane.dataset.lqSplitPane}`); }
                if (list.contains(doc.activeElement)) panes.find(pane => pane.dataset.lqSplitPane === selected)?.focus({ preventScroll: true });
            }
        }
        if (!narrow) setWidth(width, 'layout', false);
    };
    const resized = () => { if (!frame && !destroyed) frame = win.requestAnimationFrame(() => { frame = 0; refresh(); }); };
    const keydown = event => {
        if (narrow || event.isComposing || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const sign = win.getComputedStyle(root).direction === 'rtl' ? -1 : 1;
        setWidth(event.key === 'Home' ? min : event.key === 'End' ? limits().max : width + (event.key === 'ArrowRight' ? 8 : -8) * sign, 'keyboard');
    };
    const down = event => {
        if (narrow || event.button !== 0 || event.isPrimary === false || pointer) return;
        event.preventDefault(); separator.focus({ preventScroll: true });
        pointer = { id: event.pointerId, x: event.clientX, width, sign: win.getComputedStyle(root).direction === 'rtl' ? -1 : 1 };
        separator.setPointerCapture(event.pointerId); root.classList.add('is-resizing');
    };
    const move = event => { if (pointer?.id === event.pointerId) setWidth(pointer.width + (event.clientX - pointer.x) * pointer.sign, 'pointer'); };
    const up = event => { if (pointer?.id === event.pointerId) release(); };
    const invalid = event => {
        if (!nav) return;
        // Native validation emits every invalid control before focusing the
        // first. Later panels must not hide that first invalid field again.
        if (invalidTarget?.isConnected && !invalidTarget.validity.valid) {
            event.preventDefault(); // Suppress only later fields' native focus/UI; validity and blocked submit remain intact.
            return;
        }
        const pane = panes.find(item => item.contains(event.target));
        if (pane) {
            invalidTarget = event.target;
            if (invalidTimer !== null) win.clearTimeout(invalidTimer);
            invalidTimer = win.setTimeout(() => { invalidTarget = null; invalidTimer = null; }, 0);
            nav.select(pane.dataset.lqSplitPane, { sync: false, reason: 'invalid' });
        }
    };
    separator.addEventListener('keydown', keydown); separator.addEventListener('pointerdown', down); separator.addEventListener('pointermove', move);
    separator.addEventListener('pointerup', up); separator.addEventListener('pointercancel', up); separator.addEventListener('lostpointercapture', up);
    root.addEventListener('invalid', invalid, true); media.addEventListener('change', resized); coarse.addEventListener('change', resized);
    const observer = win.ResizeObserver ? new win.ResizeObserver(resized) : null;
    observer?.observe(root); if (!observer) win.addEventListener('resize', resized);
    const handle = {
        refresh,
        get width() { return width; },
        select(key) { if (!['side', 'main'].includes(key)) throw new TypeError('Unknown split pane'); if (destroyed) return false; selected = key; return nav ? nav.select(key) : true; },
        destroy() {
            if (destroyed) return;
            destroyed = true; release(); nav?.destroy(); observer?.disconnect();
            if (frame) win.cancelAnimationFrame(frame);
            if (invalidTimer !== null) win.clearTimeout(invalidTimer);
            invalidTarget = null; invalidTimer = null;
            media.removeEventListener('change', resized); coarse.removeEventListener('change', resized); win.removeEventListener('resize', resized);
            separator.removeEventListener('keydown', keydown); separator.removeEventListener('pointerdown', down); separator.removeEventListener('pointermove', move);
            separator.removeEventListener('pointerup', up); separator.removeEventListener('pointercancel', up); separator.removeEventListener('lostpointercapture', up); root.removeEventListener('invalid', invalid, true);
            // Restore only owned structural attributes; retain all content,
            // control values and other attributes added by the page controller.
            for (const [element, attrs] of saved) for (const key of ['role', 'hidden', 'aria-labelledby', 'aria-selected', 'tabindex', 'aria-valuenow', 'aria-valuemin', 'aria-valuemax', 'data-lq-tabs', 'data-lq-split-enhanced']) {
                const previous = attrs.find(([name]) => name === key);
                if (previous) element.setAttribute(key, previous[1]); else element.removeAttribute(key);
            }
            if (originalWidth) root.style.setProperty('--lq-split', originalWidth, originalPriority); else root.style.removeProperty('--lq-split');
            for (const [element, attrs] of saved) if (!attrs.some(([name]) => name === 'style') && !element.style.length) element.removeAttribute('style');
            delete root[ownerKey];
        },
    };
    root[ownerKey] = handle; refresh(); return handle;
}
