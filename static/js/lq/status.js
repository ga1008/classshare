import { tone } from './tones.js';
import { normalizeAttributes, attributesMarkup, escapeHtml } from './html.js';
import { componentProps } from './component-props.js';
import { createComponent, componentMarkup } from './components.js';

export const STATUS_KINDS = Object.freeze(['status', 'save_status', 'alert', 'conflict']);
const SAVE_LABELS = Object.freeze({ dirty: '尚未保存', local_saved: '已保存到本机', syncing: '正在同步', synced: '已同步到服务器',
    offline: '离线，尚未同步', error: '保存失败', conflict: '内容有冲突，请重新核对', submitting: '正在提交', submitted: '已提交' });
const OWNER = Symbol.for('lanshare.lq.save-status.v1');
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const option = (props, key, fallback) => props[key] === undefined ? fallback : props[key];
const span = (classes, children = [], attrs = {}) => node('span', { class: classes, ...attrs }, children);
const string = (value, name, required = false) => {
    if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError(`LQ ${name} must be ${required ? 'nonempty ' : ''}text`);
    return value;
};
function mapping(value, allowed) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
        || ![Object.prototype, null].includes(Object.getPrototypeOf(value))
        || Object.keys(value).some(key => !allowed.includes(key))) throw new TypeError('Invalid LQ status props');
}
function actionProps(value, required) {
    if (value == null) {
        if (required) throw new TypeError('LQ error/conflict requires an explicit action');
        return null;
    }
    mapping(value, ['label', 'href', 'icon', 'variant', 'attrs', 'id']);
    const attrs = normalizeAttributes(value.attrs);
    for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-')) delete attrs[key];
    const result = { ...value, label: string(value.label, 'action label', true), variant: option(value, 'variant', 'link'), size: 'sm', type: 'button',
        attrs: { ...attrs, 'data-lq-status-action': '' } };
    componentProps('button', result);
    return result;
}
function rootAttributes(props, owned) {
    const attrs = normalizeAttributes(props.attrs);
    for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-')) delete attrs[key];
    if (props.id !== undefined) attrs.id = string(props.id, 'id', true);
    return { ...attrs, ...owned };
}
const actionTree = action => action ? [{ component: 'button', props: action }] : [];
const slot = (name, text) => ({ slot: name, text });

/** One typed presentation tree for string and Element factories. */
export function statusProps(kind, props = {}) {
    if (!STATUS_KINDS.includes(kind)) throw new TypeError('Unknown LQ status kind');
    const common = ['id', 'attrs'];
    mapping(props, [...common, ...(kind === 'status' ? ['family', 'state', 'label'] : kind === 'save_status'
        ? ['state', 'label', 'time', 'datetime', 'action'] : ['title', 'body', 'action', 'announce', ...(kind === 'alert' ? ['tone'] : ['local', 'server'])])]);
    if (kind === 'status') {
        const family = string(props.family, 'family'), state = string(props.state, 'state');
        const resolved = tone(family, state);
        return node('span', rootAttributes(props, { class: 'lq-status', 'data-lq-status': kind, 'data-tone': resolved.name, 'data-lq-tone-level': resolved.level }), [
            span('lq-status__dot', [], { 'aria-hidden': 'true' }), span('lq-status__label', [string(props.label, 'label', true)]),
        ]);
    }
    if (kind === 'save_status') {
        const requested = string(option(props, 'state', 'dirty'), 'save state');
        const known = Object.hasOwn(SAVE_LABELS, requested);
        const state = known ? requested : 'unknown';
        if (props.label !== undefined) string(props.label, 'label', true);
        const label = known ? (props.label ?? SAVE_LABELS[state]) : '保存状态未知';
        const time = string(option(props, 'time', ''), 'time');
        const datetime = props.datetime === undefined ? '' : string(props.datetime, 'datetime', true);
        if (datetime && !time.trim()) throw new TypeError('LQ datetime requires visible time');
        const action = actionProps(props.action, state === 'error' || state === 'conflict');
        return node('span', rootAttributes(props, { class: 'lq-save-status', tabindex: '-1', 'data-lq-status': kind, 'data-lq-save-state': state,
            'data-tone': tone('save', state).name, 'data-lq-tone-level': tone('save', state).level }), [
            span('lq-status', [
                span('lq-status__indicator', state === 'syncing' || state === 'submitting' ? [{ component: 'spinner', props: { size: 'sm' } }]
                    : [span('lq-status__dot')], { 'aria-hidden': 'true', 'data-lq-save-indicator': '' }),
                span('lq-status__label', [label], { 'data-lq-save-label': '' }),
                node('time', { class: 'lq-status__time', 'data-lq-save-time': '', ...(datetime ? { datetime } : {}), ...(!time ? { hidden: '' } : {}) }, [time]),
            ]),
            span('lq-status__actions', actionTree(action), { 'data-lq-save-action': '' }),
            span('lq-status__live', [], { 'data-lq-save-live': '', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }),
        ]);
    }
    const conflict = kind === 'conflict';
    const severity = conflict ? 'danger' : option(props, 'tone', 'info');
    if (!['info', 'warning', 'danger'].includes(severity)) throw new TypeError('Invalid LQ alert tone');
    const announce = option(props, 'announce', 'off');
    if (!['off', 'polite', 'assertive'].includes(announce)) throw new TypeError('Invalid LQ announcement policy');
    const title = string(option(props, 'title', conflict ? '内容已变化，请重新核对' : ''), 'title');
    const body = string(option(props, 'body', conflict ? '本地修改已保留，尚未覆盖服务器内容。' : ''), 'body');
    if (!title.trim() && !body.trim()) throw new TypeError('LQ alert requires visible text');
    const action = actionProps(props.action, conflict);
    const content = [node('strong', { class: 'lq-alert__title', ...(!title ? { hidden: '' } : {}) }, [title]),
        node('div', { class: 'lq-alert__body', 'data-lq-status-slot': 'body' }, [slot('body', body)])];
    if (conflict) for (const [key, label] of [['local', '本地内容'], ['server', '服务器内容']]) {
        content.push(node('section', { class: 'lq-conflict__version', 'data-lq-status-version': key }, [
            node('strong', {}, [label]), node('div', { 'data-lq-status-slot': key }, [slot(key, string(option(props, key, ''), key))]),
        ]));
    }
    return node('div', rootAttributes(props, { class: `lq-alert${conflict ? ' lq-conflict' : ''}`, 'data-lq-status': kind,
        'data-tone': conflict ? tone('save', 'conflict').name : severity, 'data-lq-tone-level': severity }), [
        node('div', { class: 'lq-alert__content', ...(announce === 'off' ? {} : {
            role: announce === 'assertive' ? 'alert' : 'status', 'aria-live': announce, 'aria-atomic': 'true',
        }) }, content),
        node('div', { class: 'lq-alert__actions' }, actionTree(action)),
    ]);
}

function markup(tree) {
    if (typeof tree === 'string') return escapeHtml(tree);
    if (tree.component) return componentMarkup(tree.component, tree.props);
    if (tree.slot) return escapeHtml(tree.text);
    return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.children.map(markup).join('')}</${tree.tag}>`;
}
function element(tree, doc, slots) {
    if (typeof tree === 'string') return doc.createTextNode(tree);
    if (tree.component) return createComponent(tree.component, tree.props, doc);
    if (tree.slot) return slots.get(tree.slot) ?? doc.createTextNode(tree.text);
    const result = doc.createElement(tree.tag);
    for (const [key, value] of Object.entries(tree.attrs)) result.setAttribute(key, value);
    for (const child of tree.children) result.append(element(child, doc, slots));
    return result;
}
export const statusMarkup = (kind, props = {}) => markup(statusProps(kind, props));
export function createStatus(kind, props = {}, doc = document, slots = new Map()) {
    const tree = statusProps(kind, props);
    const allowed = kind === 'conflict' ? ['body', 'local', 'server'] : kind === 'alert' ? ['body'] : [];
    if (!(slots instanceof Map)) throw new TypeError('LQ content slots require a Map');
    const used = [];
    for (const [key, value] of slots) {
        if (!allowed.includes(key) || !(value instanceof doc.defaultView.Node) || ![1, 3, 11].includes(value.nodeType)
            || value.host || value.ownerDocument !== doc || used.some(prior => prior === value || prior.contains(value) || value.contains(prior))) {
            throw new TypeError('Invalid or overlapping LQ content Node');
        }
        used.push(value);
    }
    // All presentation and slot inputs validate before caller-owned Nodes move.
    return element(tree, doc, slots);
}
export const html = Object.freeze(Object.fromEntries(STATUS_KINDS.map(kind => [kind, props => statusMarkup(kind, props)])));
export const status = (props, doc) => createStatus('status', props, doc);
export const save_status = (props, doc) => createStatus('save_status', props, doc);
export const alert = (props, doc, slots) => createStatus('alert', props, doc, slots);
export const conflict = (props, doc, slots) => createStatus('conflict', props, doc, slots);

const group = state => ['error', 'conflict'].includes(state) ? 'danger' : ['dirty', 'offline'].includes(state) ? 'warning' : 'quiet';
function matchAttributes(target, source) {
    for (const attribute of [...target.attributes]) if (!source.hasAttribute(attribute.name)) target.removeAttribute(attribute.name);
    for (const attribute of source.attributes) target.setAttribute(attribute.name, attribute.value);
}
/** Presentation only. The caller owns state transitions, requests and actions. */
export function saveStatus(root) {
    if (!root?.matches?.('[data-lq-status="save_status"]')) throw new TypeError('Expected an LQ save status root');
    if (root[OWNER]) return root[OWNER];
    const own = selector => [...root.querySelectorAll(selector)].filter(node => node.closest('[data-lq-status]') === root);
    const names = ['indicator', 'label', 'time', 'action', 'live'];
    const parts = {};
    for (const name of names) {
        const matches = own(`[data-lq-save-${name}]`);
        if (matches.length !== 1) throw new TypeError('Invalid LQ save status structure');
        parts[name] = matches[0];
    }
    const doc = root.ownerDocument;
    let state = root.dataset.lqSaveState;
    if (!Object.hasOwn(SAVE_LABELS, state) && state !== 'unknown') throw new TypeError('Invalid initial save status');
    let destroyed = false;
    const controller = {
        get state() { return state; },
        set(next, options = {}) {
            if (destroyed) return false;
            mapping(options, ['label', 'time', 'datetime', 'action']);
            // Build and validate detached output before touching the active view.
            const rendered = createStatus('save_status', { ...options, state: next }, doc);
            const nextState = rendered.dataset.lqSaveState;
            const incoming = rendered.querySelector('[data-lq-status-action]');
            const current = parts.action.querySelector('[data-lq-status-action]');
            const active = doc.activeElement;
            if (current && incoming && current.tagName === incoming.tagName) {
                matchAttributes(current, incoming);
                current.replaceChildren(...incoming.childNodes);
            } else {
                parts.action.replaceChildren(...rendered.querySelector('[data-lq-save-action]').childNodes);
                if (current?.contains(active)) {
                    if (incoming) incoming.focus({ preventScroll: true });
                    else root.focus({ preventScroll: true });
                }
            }
            parts.indicator.replaceChildren(...rendered.querySelector('[data-lq-save-indicator]').childNodes);
            parts.label.textContent = rendered.querySelector('[data-lq-save-label]').textContent;
            const time = rendered.querySelector('[data-lq-save-time]'); matchAttributes(parts.time, time); parts.time.textContent = time.textContent;
            root.dataset.lqSaveState = nextState; root.dataset.tone = rendered.dataset.tone; root.dataset.lqToneLevel = rendered.dataset.lqToneLevel;
            if (group(state) !== group(nextState)) parts.live.textContent = parts.label.textContent;
            state = nextState;
            return true;
        },
        destroy() {
            if (destroyed) return;
            destroyed = true;
            if (root[OWNER] === controller) delete root[OWNER];
        },
    };
    Object.defineProperty(root, OWNER, { value: controller, configurable: true });
    return controller;
}
