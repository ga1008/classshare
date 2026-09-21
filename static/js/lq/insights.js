/** Opt-in summary presentation. Statistics and their meaning belong to callers. */
import { componentProps } from './component-props.js';
import { componentMarkup, createComponent } from './components.js';
import { normalizeAttributes, attributesMarkup, escapeHtml } from './html.js';

export const INSIGHT_KINDS = Object.freeze(['avatar_stack', 'insight_ring', 'insight_bars', 'insight_meter']);
const tones = ['primary', 'success', 'warning', 'danger', 'info', 'neutral', 'indigo', 'teal', 'sky', 'amber', 'rose', 'violet', 'emerald'];
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const block = (name, children = [], attrs = {}, tag = 'div') => node(tag, { class: name, ...attrs }, children);
const option = (p, key, fallback) => p[key] === undefined ? fallback : p[key];
function mapping(value, keys) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value))
        || Object.keys(value).some(key => !keys.includes(key))) throw new TypeError('Invalid LQ insight props');
}
function text(value, required = false) {
    if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError('LQ insights require plain text'); return value;
}
function number(value) {
    if (value === null) return null;
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > Number.MAX_SAFE_INTEGER) throw new TypeError('LQ insight values must be nonnegative finite numbers or null');
    return value;
}
function choice(value, values) { if (!values.includes(value)) throw new TypeError('Invalid LQ insight choice'); return value; }
function array(value) {
    if (!Array.isArray(value) || Array.from({ length: value.length }, (_, i) => i).some(i => !Object.hasOwn(value, i))) throw new TypeError('LQ insights require dense arrays'); return value;
}
function tone(value) { return choice(value, tones); }
function attrs(p, owned) {
    const extra = normalizeAttributes(p.attrs);
    for (const key of Object.keys(extra)) if (key.startsWith('data-lq-') || ['data-tone', 'aria-label', 'aria-labelledby'].includes(key)) delete extra[key];
    if (p.id !== undefined) extra.id = text(p.id, true);
    return { ...extra, ...owned };
}
const percent = (value, total) => Math.min(100, Math.round((value / total) * 10000) / 100);
const graphic = (classes, value) => block(classes, [node('i', { style: `--lq-insight-percent: ${value};` })], { 'aria-hidden': 'true' });
const caption = value => block('lq-insight__caption', [value, { slot: 'caption' }]);
const empty = state => block('lq-insight__empty', [state === 'missing' ? '尚未提供统计数据。' : '暂无可统计的数据。'], {}, 'p');

export function insightProps(kind, p = {}) {
    if (!INSIGHT_KINDS.includes(kind)) throw new TypeError('Unknown LQ insight');
    const common = ['id', 'attrs'];
    if (kind === 'avatar_stack') {
        mapping(p, [...common, 'items', 'size', 'label']);
        const size = option(p, 'size', 32), label = text(option(p, 'label', '成员'), true);
        // Validate every member, including those represented by the +N summary.
        const members = array(p.items).map(item => {
            mapping(item, ['name', 'src', 'detail']);
            const props = { name: text(item.name, true), src: option(item, 'src', null), size };
            componentProps('avatar', props);
            return { avatar: props, full: props.name + (item.detail === undefined ? '' : `（${text(item.detail)}）`) };
        });
        choice(size, [24, 32, 40, 56]);
        const summary = members.length ? `${label}：${members.map(item => item.full).join('；')}` : `${label}：暂无成员`;
        return node('span', attrs(p, { class: `lq-avatar-stack lq-avatar-stack--${size}${members.length ? '' : ' is-empty'}`, 'data-lq-insight': kind, role: 'img', 'aria-label': summary }), [
            block('lq-avatar-stack__visual', members.length ? [...members.slice(0, 4).map(item => ({ avatar: item.avatar })),
                ...(members.length > 4 ? [block('lq-avatar-stack__more', [`+${members.length - 4}`], {}, 'span')] : [])] : ['暂无成员'], { 'aria-hidden': 'true' }, 'span'),
        ]);
    }
    const shared = [...common, 'title', 'caption', 'tone', 'zero'];
    mapping(p, [...shared, ...(kind === 'insight_ring' ? ['value', 'total', 'value_label', 'data_key'] : kind === 'insight_meter' ? ['value', 'percent', 'unit', 'delta', 'data_key'] : ['items', 'limit'])]);
    const title = text(p.title, true), description = text(option(p, 'caption', '')), tint = tone(option(p, 'tone', 'indigo'));
    const zero = choice(option(p, 'zero', 'empty'), ['empty', 'value']);
    const root = attrs(p, { class: 'lq-insight', 'data-lq-insight': kind, 'data-tone': tint });
    const dataKey = text(option(p, 'data_key', '')); if (dataKey) root['data-insight-key'] = dataKey;
    const children = [block('lq-insight__title', [title], {}, 'p')];
    let state;
    if (kind === 'insight_ring') {
        const value = number(p.value), total = number(p.total), valueLabel = text(option(p, 'value_label', ''));
        if (value !== null && total !== null && value > total) throw new TypeError('LQ ring value exceeds total');
        state = value === null || total === null || total === 0 ? 'missing' : value === 0 && zero === 'empty' ? 'empty' : 'value';
        if (state === 'value') {
            const visual = value > 0 ? [block('lq-ring__graphic', [node('svg', { viewBox: '0 0 36 36', focusable: 'false', 'aria-hidden': 'true' }, [
                node('circle', { class: 'lq-ring__track', cx: '18', cy: '18', r: '15.9155' }),
                node('circle', { class: 'lq-ring__fill', cx: '18', cy: '18', r: '15.9155', pathLength: '100', style: `--lq-insight-percent: ${percent(value, total)};` }),
            ]), block('lq-ring__percent', [`${Math.round(value / total * 100)}%`])], { 'aria-hidden': 'true' })] : [];
            children.push(block('lq-ring', [...visual, block('lq-ring__value', [valueLabel || `${value} / ${total}`]) ]));
        } else children.push(empty(state));
    } else if (kind === 'insight_meter') {
        const value = number(p.value), ratio = number(p.percent), unit = text(option(p, 'unit', '')), delta = text(option(p, 'delta', ''));
        if (ratio !== null && ratio > 100) throw new TypeError('LQ meter percent exceeds 100');
        state = value === null ? 'missing' : value === 0 && zero === 'empty' ? 'empty' : 'value';
        if (state === 'value') children.push(block('lq-meter', [block('lq-meter__row', [block('lq-meter__value', [`${value}${unit}`], {}, 'span'),
            ...(delta ? [block('lq-meter__delta', [delta], {}, 'span')] : [])]), ...(value > 0 && ratio !== null && ratio > 0 ? [graphic('lq-meter__track', Math.min(100, ratio))] : [])]));
        else children.push(empty(state));
    } else {
        const limit = option(p, 'limit', 6); if (!Number.isSafeInteger(limit) || limit < 0) throw new TypeError('LQ bar limit must be a nonnegative integer');
        const rows = array(p.items).map(item => {
            mapping(item, ['label', 'value', 'tone', 'zero']);
            return { label: text(item.label, true), value: number(item.value), tone: tone(option(item, 'tone', tint)), zero: choice(option(item, 'zero', zero), ['empty', 'value']) };
        }).slice(0, limit);
        const shown = rows.filter(item => item.value !== 0 || item.zero === 'value');
        const maximum = Math.max(0, ...rows.map(item => item.value ?? 0));
        state = !rows.length || !shown.length ? 'empty' : shown.every(item => item.value === null) ? 'missing' : 'value';
        if (state === 'value') children.push(node('ul', { class: 'lq-bars', role: 'list' }, shown.map(item => block('lq-bars__row', [
            node('span', { class: 'lq-bars__label' }, [item.label]), node('span', { class: 'lq-bars__value' }, [item.value === null ? '未提供' : String(item.value)]),
            ...(item.value > 0 ? [graphic('lq-bars__track', percent(item.value, maximum))] : []),
        ], { 'data-tone': item.tone }, 'li'))));
        else children.push(empty(state));
    }
    root['data-state'] = state;
    if (state !== 'value') root.class += ' is-empty';
    children.push(caption(description)); return node('article', root, children);
}

function markup(tree) {
    if (typeof tree === 'string') return escapeHtml(tree);
    if (tree.slot) return '';
    if (tree.avatar) return componentMarkup('avatar', tree.avatar);
    return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.children.map(markup).join('')}</${tree.tag}>`;
}
export const insightMarkup = (kind, props = {}) => markup(insightProps(kind, props));
export const html = Object.freeze(Object.fromEntries(INSIGHT_KINDS.map(kind => [kind, props => insightMarkup(kind, props)])));

/** Caller supplies already trusted Nodes; no HTML-string or sanitizer bypass. */
export function createInsight(kind, props = {}, slots = {}, doc = document) {
    const tree = insightProps(kind, props); mapping(slots, kind === 'avatar_stack' ? [] : ['caption']);
    const nodes = slots.caption === undefined ? [] : array(slots.caption), seen = new Set();
    for (const item of nodes) {
        if (!(item instanceof doc.defaultView.Node) || seen.has(item) || ![1, 3, 11].includes(item.nodeType)) throw new TypeError('LQ insight slots require distinct existing Nodes');
        seen.add(item);
    }
    for (const a of seen) for (const b of seen) if (a !== b && a.contains(b)) throw new TypeError('LQ insight slot Nodes cannot overlap');
    const element = (n, svg = false) => {
        if (typeof n === 'string') return doc.createTextNode(n);
        if (n.slot) { const fragment = doc.createDocumentFragment(); fragment.append(...nodes); return fragment; }
        if (n.avatar) return createComponent('avatar', n.avatar, doc);
        svg ||= n.tag === 'svg';
        const result = svg ? doc.createElementNS('http://www.w3.org/2000/svg', n.tag) : doc.createElement(n.tag);
        for (const [key, value] of Object.entries(n.attrs)) result.setAttribute(key, value);
        result.append(...n.children.map(child => element(child, svg))); return result;
    };
    return element(tree);
}
export const avatar_stack = (props, slots, doc) => createInsight('avatar_stack', props, slots, doc);
export const insight_ring = (props, slots, doc) => createInsight('insight_ring', props, slots, doc);
export const insight_bars = (props, slots, doc) => createInsight('insight_bars', props, slots, doc);
export const insight_meter = (props, slots, doc) => createInsight('insight_meter', props, slots, doc);
