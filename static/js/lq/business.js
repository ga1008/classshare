import { tone } from './tones.js';
import { status, statusMarkup } from './status.js';
import { createComponent, componentMarkup } from './components.js';
import { componentProps } from './component-props.js';
import { normalizeAttributes, attributesMarkup, escapeHtml } from './html.js';
import { subscribeAssignmentClock } from '../assignment_time.js';

export const BUSINESS_KINDS = Object.freeze(['deadline_clock', 'job_status', 'question_navigator']);
const OWNERS = Object.freeze(Object.fromEntries(BUSINESS_KINDS.map(kind => [kind, Symbol.for(`lanshare.lq.${kind}.v1`)])));
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const option = (p, key, value) => p[key] === undefined ? value : p[key];
const text = (value, required = false) => {
    if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError('LQ business text must be a string');
    return value;
};
const integer = (value, minimum = 0) => {
    if (!Number.isSafeInteger(value) || value < minimum) throw new TypeError('LQ business number must be a safe integer');
    return value;
};
const flag = value => { if (typeof value !== 'boolean') throw new TypeError('LQ business flags must be boolean'); return value; };
function mapping(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))
        || Object.keys(value).some(key => !keys.includes(key))) throw new TypeError('Invalid LQ business props');
}
function array(value) {
    if (!Array.isArray(value) || Array.from({ length: value.length }, (_, index) => index).some(index => !Object.hasOwn(value, index))) throw new TypeError('LQ business items require a dense array');
    return value;
}
function attrs(p, owned) {
    const result = normalizeAttributes(p.attrs);
    for (const key of Object.keys(result)) if (key.startsWith('data-lq-') || key.startsWith('data-assignment-')) delete result[key];
    if (p.id !== undefined) result.id = text(p.id, true);
    return { ...result, ...owned };
}
function safeAction(value) {
    mapping(value, ['key', 'label', 'href', 'icon', 'variant', 'disabled', 'attrs']);
    const key = text(value.key, true), label = text(value.label, true);
    const extra = normalizeAttributes(value.attrs);
    for (const name of Object.keys(extra)) if (name.startsWith('data-lq-')) delete extra[name];
    const props = { label, href: option(value, 'href', null), icon: option(value, 'icon', null), variant: option(value, 'variant', 'soft'),
        disabled: flag(option(value, 'disabled', false)), type: 'button', size: 'sm', attrs: { ...extra, 'data-lq-job-action': key } };
    componentProps('button', props);
    return { key, props };
}

export function businessProps(kind, p = {}) {
    if (!BUSINESS_KINDS.includes(kind)) throw new TypeError('Unknown LQ business component');
    const common = ['id', 'attrs'];
    if (kind === 'deadline_clock') {
        const fields = ['assignment_id', 'server_now', 'countdown_at', 'starts_at', 'resubmission_due_at', 'late_until', 'deadline_phase', 'late_policy_label'];
        const flags = ['personal_resubmission', 'can_resubmit', 'accepting', 'late_open'];
        mapping(p, [...common, ...fields, ...flags, 'compact', 'label', 'value', 'detail', 'absolute']);
        const data = {};
        for (const key of fields) {
            let value = option(p, key, key === 'deadline_phase' ? 'none' : '');
            if (key === 'assignment_id' && typeof value === 'number') value = String(integer(value, 1));
            data[`data-${key.replaceAll('_', '-')}`] = text(value);
        }
        for (const key of flags) data[`data-${key.replaceAll('_', '-')}`] = flag(option(p, key, false)) ? '1' : '0';
        const compact = flag(option(p, 'compact', false));
        const deadline = data['data-personal-resubmission'] === '1' ? data['data-resubmission-due-at'] : data['data-countdown-at'];
        const resolved = tone('deadline', data['data-deadline-phase']);
        return node('div', attrs(p, { class: `lq-clock${compact ? ' lq-clock--compact' : ''}`, 'data-lq-business': kind,
            'data-assignment-clock': '', 'data-tone': resolved.name, 'data-lq-tone-level': resolved.level, ...data }), [
            node('span', { class: 'lq-clock__dot', 'aria-hidden': 'true' }),
            node('span', { class: 'lq-clock__label', 'data-assignment-clock-label': '' }, [text(option(p, 'label', '剩余时间'))]),
            node('strong', { class: 'lq-clock__value', 'data-assignment-clock-value': '', role: 'timer', 'aria-live': 'off', ...(compact ? { hidden: '' } : {}) }, [text(option(p, 'value', '--:--:--'))]),
            node('strong', { class: 'lq-clock__compact', 'data-lq-clock-compact': '', role: 'timer', 'aria-live': 'off', ...(!compact ? { hidden: '' } : {}) }, ['--:--']),
            node('small', { class: 'lq-clock__detail', 'data-assignment-clock-detail': '' }, [text(option(p, 'detail', ''))]),
            node('span', { class: 'lq-clock__absolute' }, ['绝对截止：', node('time', { 'data-lq-clock-absolute': '', ...(deadline ? { datetime: deadline } : {}) }, [text(option(p, 'absolute', deadline || '未设置截止时间'))])]),
        ]);
    }
    if (kind === 'job_status') {
        mapping(p, [...common, 'identity', 'generation', 'family', 'state', 'label', 'message', 'elapsed', 'progress', 'actions']);
        const identity = text(p.identity, true), generation = integer(p.generation);
        const family = text(option(p, 'family', 'job'));
        if (!['job', 'agent'].includes(family)) throw new TypeError('LQ jobs require a job or agent family');
        const state = text(p.state), resolved = tone(family, state);
        const label = text(p.label, true), message = text(option(p, 'message', '')), elapsed = text(option(p, 'elapsed', ''));
        const actions = array(option(p, 'actions', [])).map(safeAction);
        if (new Set(actions.map(action => action.key)).size !== actions.length) throw new TypeError('Duplicate LQ job action key');
        let progress = null;
        if (p.progress !== undefined && p.progress !== null) {
            mapping(p.progress, ['value', 'max', 'label']);
            progress = { label: text(p.progress.label, true), max: option(p.progress, 'max', 100), value: option(p.progress, 'value', null) };
            componentProps('progress', progress);
        }
        return node('section', attrs(p, { class: 'lq-job', tabindex: '-1', 'data-lq-business': kind, 'data-lq-job-identity': identity,
            'data-lq-job-generation': String(generation), 'data-lq-job-state': resolved.known ? state : 'unknown' }), [
            node('div', { class: 'lq-job__head' }, [{ status: { family, state, label: resolved.known ? label : '任务状态未知' } },
                node('span', { class: 'lq-job__elapsed', 'data-lq-job-elapsed': '' }, [elapsed])]),
            node('div', { class: 'lq-job__progress' }, progress ? [{ component: 'progress', props: progress }] : []),
            node('p', { class: 'lq-job__message' }, [message]),
            node('p', { class: 'lq-job__superseded', ...(state === 'superseded' ? {} : { hidden: '' }) }, ['此任务已被更新的任务替代，请核对最新结果。']),
            node('div', { class: 'lq-job__actions' }, actions.map(action => ({ component: 'button', props: action.props }))),
        ]);
    }
    mapping(p, [...common, 'label', 'groups']);
    const ids = new Set(), numbers = new Set(), groupIds = new Set(); let currents = 0, count = 0;
    const groups = array(p.groups).map(group => {
        mapping(group, ['id', 'label', 'items']); const groupId = text(group.id, true), label = text(group.label, true);
        if (groupIds.has(groupId)) throw new TypeError('Duplicate LQ question group'); groupIds.add(groupId);
        const items = array(group.items).map(item => {
            mapping(item, ['id', 'index', 'label', 'answered', 'current', 'flagged', 'error', 'pendingUpload', 'disabled']);
            const id = text(item.id, true), index = integer(item.index, 1), title = text(option(item, 'label', ''));
            if (ids.has(id) || numbers.has(index)) throw new TypeError('Duplicate LQ question identity/index'); ids.add(id); numbers.add(index); count++;
            const flags = Object.fromEntries(['answered', 'current', 'flagged', 'error', 'pendingUpload', 'disabled'].map(key => [key, flag(option(item, key, false))]));
            if (flags.current) currents++;
            const names = [flags.answered ? '已作答' : '未作答', ...(flags.current ? ['当前题'] : []), ...(flags.flagged ? ['已标记'] : []),
                ...(flags.error ? ['有错误'] : []), ...(flags.pendingUpload ? ['附件上传中'] : [])];
            const classes = ['lq-nav-grid__item', ...['answered', 'current', 'flagged', 'error'].filter(key => flags[key]).map(key => `is-${key}`), ...(flags.pendingUpload ? ['is-pending-upload'] : [])].join(' ');
            return node('button', { type: 'button', class: classes, 'data-lq-question': id, 'data-lq-question-index': String(index),
                'aria-label': [`第${index}题`, ...(title ? [title] : []), ...names].join('，'), ...(flags.current ? { 'aria-current': 'step' } : {}),
                ...(flags.disabled ? { disabled: '', 'aria-disabled': 'true' } : {}) }, [
                node('span', { class: 'lq-nav-grid__number' }, [String(index)]),
                node('span', { class: 'lq-nav-grid__marks', 'aria-hidden': 'true' }, [[flags.answered ? '✓' : '', flags.flagged ? '⚑' : '', flags.error ? '!' : '', flags.pendingUpload ? '•' : ''].join('')]),
            ]);
        });
        return node('section', { class: 'lq-nav-grid__group', 'data-lq-question-group': groupId, role: 'group', 'aria-label': label }, [
            node('p', { class: 'lq-nav-grid__title' }, [label]), node('div', { class: 'lq-nav-grid__items' }, items),
        ]);
    });
    if (currents > 1) throw new TypeError('Only one current question is allowed');
    return node('nav', attrs(p, { class: 'lq-nav-grid', tabindex: '-1', 'data-lq-business': kind, 'aria-label': text(option(p, 'label', '答题卡'), true) }),
        count ? groups : [node('p', { class: 'lq-nav-grid__empty' }, ['暂无题目'])]);
}

function markup(tree) {
    if (typeof tree === 'string') return escapeHtml(tree);
    if (tree.status) return statusMarkup('status', tree.status);
    if (tree.component) return componentMarkup(tree.component, tree.props);
    return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.children.map(markup).join('')}</${tree.tag}>`;
}
function element(tree, doc) {
    if (typeof tree === 'string') return doc.createTextNode(tree);
    if (tree.status) return status(tree.status, doc);
    if (tree.component) return createComponent(tree.component, tree.props, doc);
    const result = doc.createElement(tree.tag);
    for (const [key, value] of Object.entries(tree.attrs)) result.setAttribute(key, value);
    result.append(...tree.children.map(child => element(child, doc))); return result;
}
export const businessMarkup = (kind, props = {}) => markup(businessProps(kind, props));
export const createBusiness = (kind, props = {}, doc = document) => element(businessProps(kind, props), doc);
export const html = Object.freeze(Object.fromEntries(BUSINESS_KINDS.map(kind => [kind, props => businessMarkup(kind, props)])));
export const deadline_clock = (props, doc) => createBusiness('deadline_clock', props, doc);
export const job_status = (props, doc) => createBusiness('job_status', props, doc);
export const question_navigator = (props, doc) => createBusiness('question_navigator', props, doc);

function rootCheck(root, kind) {
    if (!root?.matches?.(`[data-lq-business="${kind}"]`)) throw new TypeError(`Expected LQ ${kind}`);
}
function syncAttributes(target, source) {
    for (const attr of [...target.attributes]) if (!source.hasAttribute(attr.name)) target.removeAttribute(attr.name);
    for (const attr of source.attributes) target.setAttribute(attr.name, attr.value);
}
function updateTree(root, next, selector) {
    const doc = root.ownerDocument, active = doc.activeElement;
    const hadFocus = root.contains(active);
    const originals = new Map([...root.querySelectorAll(`[${selector}]`)].filter(node => node.closest('[data-lq-business]') === root).map(node => [node.getAttribute(selector), node]));
    let focused = null;
    for (const replacement of [...next.querySelectorAll(`[${selector}]`)]) {
        const existing = originals.get(replacement.getAttribute(selector));
        if (existing?.tagName === replacement.tagName) {
            if (existing.contains(active)) focused = existing;
            syncAttributes(existing, replacement); existing.replaceChildren(...replacement.childNodes); replacement.replaceWith(existing);
        }
    }
    // Root IDs, layout attributes and caller hooks are not snapshot state.
    for (const attr of next.attributes) if (attr.name !== 'class') root.setAttribute(attr.name, attr.value);
    root.replaceChildren(...next.childNodes);
    if (hadFocus) (focused && !focused.disabled && focused.getAttribute('aria-disabled') !== 'true' ? focused : root).focus({ preventScroll: true });
}
function controller(root, kind, options, selector, onSet) {
    rootCheck(root, kind); if (root[OWNERS[kind]]) return root[OWNERS[kind]];
    const callback = options[kind === 'job_status' ? 'onAction' : 'onSelect'];
    mapping(options, kind === 'job_status' ? ['onAction'] : ['onSelect']);
    if (callback !== undefined && typeof callback !== 'function') throw new TypeError('LQ business callback must be a function');
    let destroyed = false;
    const click = event => {
        const target = event.target.closest?.(`[${selector}]`);
        if (event.defaultPrevented || !target || target.closest('[data-lq-business]') !== root || target.disabled || target.getAttribute('aria-disabled') === 'true') return;
        callback?.(kind === 'job_status' ? { key: target.dataset.lqJobAction, identity: root.dataset.lqJobIdentity, generation: Number(root.dataset.lqJobGeneration) }
            : { id: target.dataset.lqQuestion, index: Number(target.dataset.lqQuestionIndex) }, event);
    };
    const handle = {
        set(snapshot) {
            if (destroyed) return false;
            const next = createBusiness(kind, snapshot, root.ownerDocument);
            if (onSet && !onSet(next)) return false;
            updateTree(root, next, selector); return true;
        },
        destroy() { if (destroyed) return; destroyed = true; root.removeEventListener('click', click); if (root[OWNERS[kind]] === handle) delete root[OWNERS[kind]]; },
    };
    root.addEventListener('click', click); Object.defineProperty(root, OWNERS[kind], { value: handle, configurable: true }); return handle;
}
export function jobStatus(root, options = {}) {
    return controller(root, 'job_status', options, 'data-lq-job-action', next => {
        const current = Number(root.dataset.lqJobGeneration), generation = Number(next.dataset.lqJobGeneration);
        return generation > current || (generation === current && next.dataset.lqJobIdentity === root.dataset.lqJobIdentity);
    });
}
export function questionNavigator(root, options = {}) { return controller(root, 'question_navigator', options, 'data-lq-question'); }

/** Only an explicit binder subscribes to the existing assignment clock owner. */
export function deadlineClock(root) {
    rootCheck(root, 'deadline_clock'); if (root[OWNERS.deadline_clock]) return root[OWNERS.deadline_clock];
    const absolute = root.querySelector('[data-lq-clock-absolute]'), compact = root.querySelector('[data-lq-clock-compact]');
    if (!absolute || !compact) throw new TypeError('Invalid LQ clock structure');
    let destroyed = false, columns = 9;
    const lease = subscribeAssignmentClock(root, snapshot => {
        if (destroyed) return;
        const state = snapshot.phase === 'closed' || snapshot.phase === 'late' ? snapshot.phase : snapshot.urgent ? 'urgent' : snapshot.phase;
        const resolved = tone('deadline', state);
        root.dataset.tone = resolved.name; root.dataset.lqToneLevel = resolved.level;
        root.dataset.lqClockUrgent = String(snapshot.urgent && snapshot.phase !== 'closed');
        if (snapshot.deadlineAt) {
            absolute.dateTime = snapshot.deadlineAt;
            absolute.textContent = new Date(snapshot.deadlineAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        } else { absolute.removeAttribute('datetime'); absolute.textContent = '未设置截止时间'; }
        const seconds = snapshot.remainingSeconds;
        compact.textContent = seconds === null || seconds <= 0 ? snapshot.value
            : `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
        columns = Math.max(columns, compact.textContent.length); compact.style.minWidth = `${columns}ch`;
    });
    const handle = { refresh: () => !destroyed && lease.refresh(), destroy() {
        if (destroyed) return; destroyed = true; lease.dispose();
        if (root[OWNERS.deadline_clock] === handle) delete root[OWNERS.deadline_clock];
    } };
    Object.defineProperty(root, OWNERS.deadline_clock, { value: handle, configurable: true }); return handle;
}
