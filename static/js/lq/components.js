import { componentProps } from './component-props.js';
import { attributesMarkup, escapeHtml } from './html.js';
import { createIcon, iconMarkup } from './icons.js';

function tree(kind, props) {
  const p = componentProps(kind, props);
  const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
  const span = (classes, children = [], attrs = {}) => node('span', { class: classes, ...attrs }, children);
  const icon = name => ({ icon: name });
  const hidden = { 'aria-hidden': 'true' };
  let children = [];
  if (kind === 'button') {
    if (p.icon) children.push(span('lq-btn__icon', [icon(p.icon)], hidden));
    if (!p.icon_only) children.push(span('lq-btn__label', [p.label]));
    if (p.badge !== null) children.push(span('lq-btn__badge', [p.badge], hidden));
    if (p.loading) children.push(span('lq-btn__spinner', [tree('spinner', { size: 'sm' })], hidden));
  } else if (kind === 'chip') {
    if (p.kind === 'status') children.push(span('lq-chip__dot', [], hidden));
    children.push(span('lq-chip__label', [p.label]));
    if (p.removable) children.push(node('button', {
      type: 'button', class: 'lq-chip__remove', 'aria-label': p.remove_label, 'data-lq-chip-remove': '',
      ...(p.disabled ? { disabled: '', 'aria-disabled': 'true', 'data-lq-disabled': 'true' } : {}),
    }, [icon('x')]));
  } else if (kind === 'badge') {
    if (!p.visible) return null;
    if (!p.dot) children = [p.value];
  } else if (kind === 'avatar') {
    children = [span('lq-avatar__fallback', [p.initial], hidden)];
    if (p.src) children.push(node('img', { class: 'lq-avatar__image', src: p.src, alt: '', loading: 'lazy', decoding: 'async' }));
  } else if (kind === 'skeleton') {
    children = Array.from({ length: p.lines }, () => span('lq-skeleton__part'));
  } else if (kind === 'progress' && p.variant === 'ring') {
    // Same 100-unit circle geometry as InsightRing; this root owns progress semantics.
    const circle = { cx: '18', cy: '18', r: '15.9155', pathLength: '100' };
    children = [node('svg', { class: 'lq-progress__svg', viewBox: '0 0 36 36', focusable: 'false', ...hidden }, [
      node('circle', { class: 'lq-progress__track', ...circle }),
      node('circle', { class: 'lq-progress__fill', ...circle, 'stroke-dasharray': `${p.percent ?? 25} 100`,
        ...(p.percent === 0 ? { visibility: 'hidden' } : {}) }),
    ]), span('lq-progress__value', [p.percent === null ? '…' : `${p.percent}%`], hidden)];
  }
  return node(p.tag || (kind === 'progress' ? 'progress' : 'span'), { class: p.classes, ...p.attrs }, children);
}

function markup(node) {
  if (node === null) return '';
  if (typeof node === 'string') return escapeHtml(node);
  if (node.icon) return iconMarkup(node.icon);
  const start = `<${node.tag}${attributesMarkup(node.attrs)}>`;
  return node.tag === 'img' ? start : `${start}${node.children.map(markup).join('')}</${node.tag}>`;
}
function element(node, doc, svg = false) {
  if (node === null) return null;
  if (typeof node === 'string') return doc.createTextNode(node);
  if (node.icon) return createIcon(node.icon, doc);
  svg ||= node.tag === 'svg';
  const result = svg ? doc.createElementNS('http://www.w3.org/2000/svg', node.tag) : doc.createElement(node.tag);
  for (const [key, value] of Object.entries(node.attrs)) result.setAttribute(key, value);
  for (const child of node.children) result.append(element(child, doc, svg));
  return result;
}

/** Only our typed tree becomes HTML. User values are always text/attributes. */
export function componentMarkup(kind, props = {}) { return markup(tree(kind, props)); }
export function createComponent(kind, props = {}, doc = document) {
  const result = element(tree(kind, props), doc);
  // Element factories remain safe before the document-wide SSR enhancer starts.
  if (result) {
    result.addEventListener('click', blockDisabled, true);
    result.addEventListener('keydown', blockDisabled, true);
    result.addEventListener('error', recoverAvatar, true);
  }
  return result;
}

function blockDisabled(event) {
  if (event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
  const target = event.target?.closest?.('.lq-btn, .lq-chip, .lq-chip__remove');
  if (target?.getAttribute('data-lq-disabled') !== 'true') return;
  event.preventDefault(); event.stopImmediatePropagation();
}
function recoverAvatar(event) {
  const img = event.target;
  if (img?.matches?.('.lq-avatar > .lq-avatar__image')) img.hidden = true;
}

const enhancementKey = Symbol.for('lanshare.lq.presentation-enhancements');
/** Reference-counted ownership also handles imports from distinct asset URLs. */
export function enhanceComponents(doc = document) {
  let owner = doc[enhancementKey];
  if (!owner) {
    owner = { count: 0 };
    doc[enhancementKey] = owner;
    doc.addEventListener('click', blockDisabled, true);
    doc.addEventListener('keydown', blockDisabled, true);
    doc.addEventListener('error', recoverAvatar, true);
    owner.dispose = () => {
      doc.removeEventListener('click', blockDisabled, true);
      doc.removeEventListener('keydown', blockDisabled, true);
      doc.removeEventListener('error', recoverAvatar, true);
      delete doc[enhancementKey];
    };
    // An SSR image can fail before this module downloads; cover that case too.
    for (const img of doc.querySelectorAll('.lq-avatar > .lq-avatar__image')) {
      if (img.complete && img.naturalWidth === 0) img.hidden = true;
    }
  }
  owner.count++;
  let disposed = false;
  return { dispose() { if (!disposed) { disposed = true; if (--owner.count === 0) owner.dispose(); } } };
}

export const html = Object.freeze(Object.fromEntries(['button', 'chip', 'badge', 'avatar', 'spinner', 'progress', 'skeleton']
  .map(kind => [kind, props => componentMarkup(kind, props)])));
export const button = (props, doc) => createComponent('button', props, doc);
export const chip = (props, doc) => createComponent('chip', props, doc);
export const badge = (props, doc) => createComponent('badge', props, doc);
export const avatar = (props, doc) => createComponent('avatar', props, doc);
export const spinner = (props, doc) => createComponent('spinner', props, doc);
export const progress = (props, doc) => createComponent('progress', props, doc);
export const skeleton = (props, doc) => createComponent('skeleton', props, doc);
// React consumes the same validated presentation tree without a second runtime.
export { tree as componentTree };
