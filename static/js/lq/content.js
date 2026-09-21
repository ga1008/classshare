/** Opt-in content presentation; controllers own data, actions and sanitized DOM. */
import { normalizeAttributes, escapeHtml, attributesMarkup, safeUrl } from './html.js';
import { componentProps } from './component-props.js';
import { componentMarkup, createComponent } from './components.js';
import { formProps } from './forms.js';

export const contentKinds = Object.freeze(['card', 'list', 'row', 'empty', 'page_head', 'filter_bar', 'prose', 'bubble']);
const reasons = { empty: '暂无内容', 'no-results': '没有匹配的结果', error: '内容加载失败', forbidden: '没有访问权限', offline: '当前处于离线状态' };
const text = (value, required = false) => { value = value ?? ''; if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError('Content requires plain text'); return value; };
const identity = value => { value = text(value, true); if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(value) || value.includes('--lq-')) throw new TypeError('Content requires a unique non-reserved id'); return value; };
const flag = (p, key, fallback = false) => { const value = p[key] === undefined ? fallback : p[key]; if (typeof value !== 'boolean') throw new TypeError('Content flags must be boolean'); return value; };
const choice = (value, choices) => { if (!choices.includes(value)) throw new TypeError('Invalid content variant'); return value; };
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const block = (classes, children = [], tag = 'div', attrs = {}) => node(tag, { class: classes, ...attrs }, children);
const slot = (name, classes) => block(classes, [{ slot: name }], 'div', { 'data-lq-slot': name });
function attributes(value) {
  if (value != null && (typeof value !== 'object' || Array.isArray(value))) throw new TypeError('Content attrs must be a mapping');
  for (const [key, item] of Object.entries(value || {})) {
    if ((!/^(?:aria|data)-[a-z][a-z0-9_.:-]*$/.test(key) && !['id', 'title'].includes(key)) || (item != null && !['string', 'boolean'].includes(typeof item))) throw new TypeError('Unsupported content attribute');
  }
  const attrs = normalizeAttributes(value);
  for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || ['aria-label', 'aria-labelledby'].includes(key)) delete attrs[key];
  if (attrs.id !== undefined) attrs.id = identity(attrs.id);
  return attrs;
}
function action(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('Actions must be structured Button props');
  const allowed = ['label', 'href', 'id', 'attrs', 'variant', 'size', 'icon', 'disabled', 'ariaDisabled', 'loading', 'type'];
  if (Object.keys(value).some(key => !allowed.includes(key))) throw new TypeError('Unknown action property');
  const p = { label: text(value.label), variant: value.variant === undefined ? 'soft' : value.variant, size: value.size === undefined ? 'sm' : value.size,
    href: value.href ?? null, id: value.id ?? null, icon: value.icon ?? null, disabled: value.disabled === undefined ? false : value.disabled,
    ariaDisabled: value.ariaDisabled === undefined ? false : value.ariaDisabled, loading: value.loading === undefined ? false : value.loading,
    type: value.type === undefined ? 'button' : value.type, attrs: value.attrs ?? null };
  const normalized = componentProps('button', p);
  Object.assign(p, { href: normalized.attrs.href ?? null, label: normalized.label, icon: normalized.icon });
  if (p.id !== null) p.id = identity(p.id);
  p.attrs = Object.fromEntries(Object.entries(p.attrs || {}).filter(([, value]) => value != null).map(([key, value]) => [key, String(value)]));
  return { button: p };
}
function actions(value) { if (value == null) return []; if (!Array.isArray(value)) throw new TypeError('Actions must be a list'); return value.map(action); }

export function contentProps(kind, p = {}) {
  if (!contentKinds.includes(kind)) throw new TypeError('Unknown content component');
  if (['html', 'rawHTML', 'raw_html'].some(key => key in p)) throw new TypeError('Content does not accept raw HTML');
  if (('swipe' in p && kind !== 'row') || ('groups' in p && kind !== 'list')) throw new TypeError('Grouped lists and swipe Rows have distinct props');
  const attrs = attributes(p.attrs);
  if (p.id != null) attrs.id = identity(p.id);
  attrs.class = `lq-${kind.replaceAll('_', '-')}`;
  if (kind === 'card' || kind === 'row') {
    const title = text(p.title, true), meta = text(p.meta);
    let heading = [title];
    if (p.primary != null) {
      if (typeof p.primary !== 'object' || Array.isArray(p.primary) || Object.keys(p.primary).some(key => !['href', 'id', 'attrs', 'disabled'].includes(key))) throw new TypeError('Primary action accepts href/id/attrs/disabled only');
      heading = [action({ ...p.primary, label: title, variant: 'link', size: 'md' })]; attrs.class += ` lq-${kind}--interactive`;
    }
    if (kind === 'card') {
      const variant = choice(p.variant === undefined ? 'default' : p.variant, ['default', 'flat', 'stat', 'hero']);
      attrs.class += ` lq-surface lq-card--${variant}`;
      const children = [block('lq-card__head', [block('lq-card__copy', [block('lq-card__title', heading, 'h3'), ...(meta ? [block('lq-card__meta', [meta], 'p')] : [])]), block('lq-card__actions', [...actions(p.actions), { slot: 'actions' }], 'div', { 'data-lq-slot': 'actions' })])];
      if (variant === 'stat') {
        if (!['string', 'number'].includes(typeof p.value) || (typeof p.value === 'string' && !p.value.trim())) throw new TypeError('Stat cards require an explicit value');
        if (typeof p.value === 'number' && !Number.isSafeInteger(p.value)) throw new TypeError('Stat numbers must be safe integers; format other values as text');
        children.push(block('lq-card__value', [String(p.value)], 'p'));
      }
      children.push(slot('body', 'lq-card__body'), slot('foot', 'lq-card__foot')); return node('article', attrs, children);
    }
    if (flag(p, 'unread')) attrs.class += ' is-unread';
    let children = [slot('lead', 'lq-row__lead'), block('lq-row__main', [block('lq-row__title', heading, 'p'), ...(meta ? [block('lq-row__meta', [meta], 'p')] : [])]), block('lq-row__trail', [...actions(p.actions), { slot: 'trail' }], 'div', { 'data-lq-slot': 'trail' })];
    if ('swipe' in p) {
      const swipe = p.swipe;
      if (!swipe || typeof swipe !== 'object' || Array.isArray(swipe) || Object.keys(swipe).some(key => !['key', 'label', 'disabled', 'busy'].includes(key))) throw new TypeError('Swipe requires structured intent props');
      const rowId = identity(attrs.id), key = identity(swipe.key), label = text(swipe.label, true), disabled = flag(swipe, 'disabled'), busy = flag(swipe, 'busy');
      Object.assign(attrs, { class: `${attrs.class} lq-row--swipe`, 'data-lq-row-key': key, 'data-lq-row-disabled': String(disabled), 'data-lq-row-busy': String(busy), 'aria-busy': String(busy) });
      children.at(-1).children.push(action({ label: '显示操作', variant: 'ghost', disabled: disabled || busy,
        attrs: { 'data-lq-row-reveal': '', 'aria-label': `显示${label}操作`, 'aria-expanded': 'false', 'aria-controls': `${rowId}--lq-swipe` } }));
      children = [block('lq-row__front', children), block('lq-row__swipe-actions', [action({ label, variant: 'destructive', disabled: disabled || busy,
        attrs: { 'data-lq-row-action': key } })], 'div', { id: `${rowId}--lq-swipe` })];
    }
    return node('li', attrs, children);
  }
  if (kind === 'list') {
    const items = p.items === undefined ? [] : p.items;
    if (!Array.isArray(items) || items.some(item => !item || typeof item !== 'object' || Array.isArray(item))) throw new TypeError('List items must be Row props');
    Object.assign(attrs, { 'aria-label': text(p.label, true), role: 'list' });
    const tag = flag(p, 'ordered') ? 'ol' : 'ul';
    if (!('groups' in p)) return node(tag, attrs, [...items.map(item => contentProps('row', item)), { slot: 'items' }]);
    if (items.length || !Array.isArray(p.groups)) throw new TypeError('Grouped lists require groups and no flat items');
    const listId = identity(attrs.id), seen = new Set(); attrs.class += ' lq-list--grouped';
    return node(tag, attrs, p.groups.map(group => {
      if (!group || typeof group !== 'object' || Array.isArray(group) || Object.keys(group).some(key => !['key', 'title', 'items'].includes(key))) throw new TypeError('Invalid list group');
      const key = identity(group.key), title = text(group.title, true), items = group.items === undefined ? [] : group.items;
      if (seen.has(key)) throw new TypeError('List group keys must be unique'); seen.add(key);
      if (!Array.isArray(items) || items.some(item => !item || typeof item !== 'object' || Array.isArray(item))) throw new TypeError('Group items must be Row props');
      const headingId = `${listId}--lq-group-${key}`;
      return block('lq-list__group', [block('lq-list__heading', [title], 'h3', { id: headingId }), node(tag, { class: 'lq-list__items', role: 'list', 'aria-labelledby': headingId }, [...items.map(item => contentProps('row', item)), { slot: `items:${key}` }])], 'li');
    }));
  }
  if (kind === 'empty') {
    const reason = choice(p.reason === undefined ? 'empty' : p.reason, Object.keys(reasons)), variant = choice(p.variant === undefined ? 'inline' : p.variant, ['inline', 'card', 'page']);
    Object.assign(attrs, { class: `${attrs.class} lq-empty--${variant}`, 'data-reason': reason, 'data-page-empty': '' });
    const title = text(p.title === undefined ? reasons[reason] : p.title, true), description = text(p.description);
    return node('div', attrs, [block('lq-empty__copy', [block('lq-empty__title', [title], 'strong'), ...(description ? [block('lq-empty__description', [description], 'p')] : [])]), block('lq-empty__actions', [...actions(p.actions), { slot: 'actions' }], 'div', { 'data-lq-slot': 'actions' })]);
  }
  if (kind === 'page_head') {
    const title = text(p.title, true), description = text(p.description), explain = text(p.explain); text(p.eyebrow);
    const heading = [block('lq-page-head__title', [title], 'h2', p.titleId ? { id: identity(p.titleId) } : {})];
    if (explain) heading.push(action({ label: '', icon: 'circle-help', variant: 'ghost', attrs: { 'aria-label': text(p.explainLabel) || `${title}说明`, 'aria-haspopup': 'dialog', 'data-explain': '', 'data-explain-toggle': '', 'data-explain-title': title, 'data-explain-text': explain, 'data-explain-placement': 'bottom' } }));
    attrs['data-page-head'] = '';
    const pageActions = Array.isArray(p.actions) ? p.actions.map(item => item && typeof item === 'object' && !Array.isArray(item) ? { ...item, variant: ({ primary: 'prominent', outline: 'soft' })[item.variant] || (item.variant === undefined ? 'soft' : item.variant) } : item) : p.actions;
    return node('header', attrs, [block('page-head__copy', [block('lq-page-head__title-row', heading), ...(description ? [block('page-head__desc', [description], 'p')] : [])]), slot('aside', 'page-head__aside'), block('page-head__actions', [...actions(pageActions), { slot: 'actions' }], 'div', { 'data-lq-slot': 'actions' })]);
  }
  if (kind === 'filter_bar') {
    const tag = choice(p.tag === undefined ? 'form' : p.tag, ['form', 'div']);
    Object.assign(attrs, { 'data-filter-bar': '', 'aria-label': text(p.label === undefined ? '筛选' : p.label, true) });
    if (tag === 'form') { attrs.method = choice(p.method === undefined ? 'get' : p.method, ['get', 'post']); if (p.action != null) attrs.action = safeUrl(p.action, true); }
    else { if (p.action != null || p.method !== undefined) throw new TypeError('A grouped FilterBar cannot submit'); attrs.role = 'group'; }
    const children = [];
    if (p.searchId) children.push(block('lq-filter-bar__search', [formProps('input', { id: p.searchId, label: text(p.searchLabel === undefined ? '搜索' : p.searchLabel, true), type: 'search', name: p.searchName === undefined ? 'q' : p.searchName, value: text(p.searchValue), placeholder: text(p.searchPlaceholder === undefined ? '搜索…' : p.searchPlaceholder), attrs: p.searchAttrs })]));
    else if (p.searchAttrs && Object.keys(p.searchAttrs).length) throw new TypeError('Search attributes require a search id');
    return node(tag, attrs, [...children, slot('filters', 'lq-filter-bar__controls'), slot('actions', 'lq-filter-bar__actions')]);
  }
  if (kind === 'prose') return node('div', attrs, [text(p.text), { slot: 'content' }]);
  const side = choice(p.side === undefined ? 'incoming' : p.side, ['incoming', 'outgoing']), author = text(p.author, true), time = text(p.time, true);
  attrs.class += ` lq-bubble--${side}${flag(p, 'connected') ? ' is-connected' : ''}`;
  return node('article', attrs, [block('lq-bubble__author', [author], 'p'), block('lq-bubble__content', [text(p.text), { slot: 'content' }], 'div', { 'data-lq-slot': 'content' }), block('lq-bubble__time', [time], 'time', p.datetime != null ? { datetime: text(p.datetime, true) } : {})]);
}

function markup(tree) {
  if (typeof tree === 'string') return escapeHtml(tree);
  if (tree.slot) return '';
  if (tree.button) return componentMarkup('button', tree.button);
  const start = `<${tree.tag}${attributesMarkup(tree.attrs)}>`;
  return tree.tag === 'input' ? start : `${start}${tree.children.map(markup).join('')}</${tree.tag}>`;
}
export const contentMarkup = (kind, props = {}) => markup(contentProps(kind, props));
export const html = Object.freeze(Object.fromEntries(contentKinds.map(kind => [kind, props => contentMarkup(kind, props)])));

/** Validate every slot before touching caller-owned Nodes. No string-to-DOM path. */
export function createContent(kind, props = {}, slots = {}, doc = document) {
  const tree = contentProps(kind, props), allowed = new Set(), seen = new Set();
  const collect = n => { if (n?.slot) allowed.add(n.slot); for (const child of n?.children || []) collect(child); }; collect(tree);
  if (kind === 'list') for (const name of allowed) if (name !== 'items' && !name.startsWith('items:')) allowed.delete(name);
  if (!slots || typeof slots !== 'object' || Array.isArray(slots)) throw new TypeError('Content slots must be a Node mapping');
  for (const [name, nodes] of Object.entries(slots)) {
    if (!allowed.has(name) || !Array.isArray(nodes)) throw new TypeError('Unknown content slot');
    for (const item of nodes) {
      if (!(item instanceof doc.defaultView.Node) || seen.has(item) || item.nodeType === 9 || item.nodeType === 10) throw new TypeError('Content slots accept distinct existing Nodes only');
      if (kind === 'list' && !(item.nodeType === 1 && item.matches('li.lq-row'))) throw new TypeError('List slots accept direct LQ Row elements only');
      seen.add(item);
    }
  }
  // Ancestor/descendant pairs would silently move a child out of its supplied parent.
  for (const a of seen) for (const b of seen) if (a !== b && a.contains(b)) throw new TypeError('Content slot Nodes cannot overlap');
  const element = n => {
    if (typeof n === 'string') return doc.createTextNode(n);
    if (n.slot) { const result = doc.createDocumentFragment(); result.append(...(slots[n.slot] || [])); return result; }
    if (n.button) return createComponent('button', n.button, doc);
    const result = doc.createElement(n.tag); for (const [key, value] of Object.entries(n.attrs)) result.setAttribute(key, value);
    for (const child of n.children) result.append(element(child)); return result;
  };
  return element(tree);
}

const ROW_OWNER = Symbol.for('lanshare.lq.row.owner');
/** Reveal one native intent button. The caller owns confirmation, requests and data. */
export function enhanceRow(root, { onAction, onError = () => {} } = {}) {
  if (!root?.isConnected || !root.matches('li.lq-row--swipe')) throw new TypeError('A connected swipe Row is required');
  if (root[ROW_OWNER]) return root[ROW_OWNER];
  if (typeof onAction !== 'function' || typeof onError !== 'function') throw new TypeError('Row requires an intent callback');
  const doc = root.ownerDocument, win = doc.defaultView, front = root.querySelector(':scope > .lq-row__front'), tray = root.querySelector(':scope > .lq-row__swipe-actions');
  const actionButton = tray?.querySelector('[data-lq-row-action]'), toggle = front?.querySelector('[data-lq-row-reveal]');
  if (!front || !tray || !actionButton || !toggle || !root.id || actionButton.dataset.lqRowAction !== root.dataset.lqRowKey) throw new TypeError('Invalid swipe Row structure');
  const media = win.matchMedia('(max-width: 767px) and (pointer: coarse)');
  const originals = [root, front, tray, actionButton, toggle].map(el => [el, new Map([...el.attributes].map(a => [a.name, a.value]))]);
  const hadStyle = originals[1][1].has('style'), originalShift = front.style.getPropertyValue('--lq-row-shift'), originalPriority = front.style.getPropertyPriority('--lq-row-shift');
  const owned = new Map([[root, ['data-lq-row-enhanced', 'data-lq-row-open', 'data-lq-row-disabled', 'data-lq-row-busy', 'data-lq-row-dragging', 'aria-busy']],
    [front, []], [tray, ['inert', 'aria-hidden']], [actionButton, ['disabled', 'aria-disabled', 'data-lq-disabled', 'aria-busy']], [toggle, ['disabled', 'aria-disabled', 'data-lq-disabled', 'aria-expanded']]]);
  let disposed = false, opened = false, gesture = null, pending = false, suppressClick = false;
  let disabled = root.dataset.lqRowDisabled === 'true', busy = root.dataset.lqRowBusy === 'true';
  const blocked = () => disabled || busy || pending;
  const direction = () => win.getComputedStyle(root).direction === 'rtl' ? 1 : -1;
  const width = () => tray.getBoundingClientRect().width;
  const restoreFocus = () => { if (tray.contains(doc.activeElement)) toggle.focus({ preventScroll: true }); };
  const paint = () => {
    if (disposed) return;
    const blockedNow = blocked(), concealed = media.matches && !opened;
    root.dataset.lqRowEnhanced = 'true'; root.dataset.lqRowOpen = String(opened && media.matches);
    root.dataset.lqRowDisabled = String(disabled); root.dataset.lqRowBusy = String(busy || pending); root.setAttribute('aria-busy', String(busy || pending));
    // Inert is paired with a permanently visible native reveal button on mobile.
    tray.inert = concealed; if (concealed) tray.setAttribute('aria-hidden', 'true'); else tray.removeAttribute('aria-hidden');
    for (const button of [actionButton, toggle]) {
      button.disabled = blockedNow;
      button.classList.toggle('is-disabled', blockedNow);
      if (blockedNow) { button.setAttribute('aria-disabled', 'true'); button.dataset.lqDisabled = 'true'; }
      else { button.removeAttribute('aria-disabled'); button.removeAttribute('data-lq-disabled'); }
    }
    actionButton.setAttribute('aria-busy', String(busy || pending)); toggle.setAttribute('aria-expanded', String(opened && media.matches));
    front.style.setProperty('--lq-row-shift', `${opened && media.matches ? direction() * width() : 0}px`);
  };
  const release = () => {
    const previous = gesture; gesture = null; root.removeAttribute('data-lq-row-dragging');
    if (previous && front.hasPointerCapture(previous.id)) front.releasePointerCapture(previous.id);
  };
  const close = () => { if (disposed) return; restoreFocus(); release(); opened = false; paint(); };
  const reveal = (focus = false) => { if (disposed || blocked()) return false; release(); opened = media.matches; paint(); if (focus) actionButton.focus({ preventScroll: true }); return true; };
  const refresh = (state = {}) => {
    if (disposed) return;
    if (!state || typeof state !== 'object' || Array.isArray(state) || Object.keys(state).some(key => !['disabled', 'busy'].includes(key)) || Object.values(state).some(value => typeof value !== 'boolean')) throw new TypeError('Row state requires explicit disabled/busy booleans');
    if ('disabled' in state) disabled = state.disabled; if ('busy' in state) busy = state.busy;
    if (blocked() || !media.matches) { restoreFocus(); release(); opened = false; }
    paint();
  };
  const down = event => {
    if (!media.matches || blocked() || !event.isPrimary || !['touch', 'pen'].includes(event.pointerType) || event.button !== 0 || event.target.closest('button,a,input,textarea,select,summary,[contenteditable]')) return;
    suppressClick = false; gesture = { id: event.pointerId, x: event.clientX, y: event.clientY, axis: null, start: opened ? width() : 0, distance: opened ? width() : 0 };
  };
  const move = event => {
    if (!gesture || event.pointerId !== gesture.id) return;
    const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
    if (!gesture.axis) {
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 10) return;
      if (Math.abs(dy) >= Math.abs(dx) || Math.abs(dx) < Math.abs(dy) * 1.3) { release(); return; }
      gesture.axis = 'x'; front.setPointerCapture(event.pointerId); root.dataset.lqRowDragging = 'true';
    }
    // touch-action: pan-y already declares browser panning; vertical streams
    // are untouched and clicks are never invented from pointer-up.
    suppressClick = true;
    gesture.distance = Math.max(0, Math.min(width(), gesture.start + dx * direction()));
    front.style.setProperty('--lq-row-shift', `${direction() * gesture.distance}px`);
  };
  const end = event => {
    if (!gesture || event.pointerId !== gesture.id) return;
    const next = event.type === 'pointerup' && gesture.axis === 'x' && gesture.distance >= width() * .4;
    // A cancelled stream always returns flush; it cannot leave an exposed hit area.
    release(); opened = next; paint();
  };
  const click = event => {
    if (suppressClick && event.detail > 0 && !event.target.closest('button,a,input,textarea,select,summary,[contenteditable]')) { suppressClick = false; event.preventDefault(); event.stopImmediatePropagation(); return; }
    if (event.target.closest('[data-lq-row-reveal]') === toggle) { event.preventDefault(); if (!blocked()) opened ? close() : reveal(event.detail === 0); return; }
    if (event.target.closest('[data-lq-row-action]') !== actionButton) return;
    event.preventDefault();
    if (blocked() || (media.matches && !opened)) return;
    pending = true; paint();
    Promise.resolve().then(() => { if (!disposed) return onAction(Object.freeze({ key: root.dataset.lqRowKey, rowId: root.id })); })
      .catch(error => { if (!disposed) onError(error); }).finally(() => { if (!disposed) { pending = false; close(); } });
  };
  const keyboard = event => { if (event.key === 'Escape' && opened) { event.preventDefault(); close(); toggle.focus({ preventScroll: true }); } };
  const resize = () => { close(); };
  const lost = event => { if (event.target === front && gesture?.id === event.pointerId && !front.hasPointerCapture(event.pointerId)) close(); };
  front.addEventListener('pointerdown', down); front.addEventListener('pointermove', move, { passive: false });
  front.addEventListener('pointerup', end); front.addEventListener('pointercancel', end); front.addEventListener('lostpointercapture', lost);
  root.addEventListener('click', click, true); root.addEventListener('keydown', keyboard); media.addEventListener('change', resize);
  const observer = new win.ResizeObserver(() => { if (!gesture) paint(); }); observer.observe(tray);
  const handle = { reveal, close, refresh, destroy() {
    if (disposed) return; restoreFocus(); release(); disposed = true;
    front.removeEventListener('pointerdown', down); front.removeEventListener('pointermove', move); front.removeEventListener('pointerup', end); front.removeEventListener('pointercancel', end); front.removeEventListener('lostpointercapture', lost);
    root.removeEventListener('click', click, true); root.removeEventListener('keydown', keyboard); media.removeEventListener('change', resize); observer.disconnect();
    for (const [el, attrs] of originals) for (const name of owned.get(el)) attrs.has(name) ? el.setAttribute(name, attrs.get(name)) : el.removeAttribute(name);
    for (const [el, attrs] of originals) if (el === actionButton || el === toggle) el.classList.toggle('is-disabled', (attrs.get('class') || '').split(/\s+/).includes('is-disabled'));
    if (originalShift) front.style.setProperty('--lq-row-shift', originalShift, originalPriority); else front.style.removeProperty('--lq-row-shift');
    if (!hadStyle && !front.style.length) front.removeAttribute('style');
    delete root[ROW_OWNER];
  } };
  root[ROW_OWNER] = handle; paint(); return handle;
}
