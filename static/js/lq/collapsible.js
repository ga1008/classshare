import { normalizeAttributes, escapeHtml, attributesMarkup } from './html.js';

const stateKeys = { open: 'default-open', keepOpen: 'keep-open', hasError: 'has-error', current: 'current', dirty: 'dirty' };
export function collapsibleProps(props = {}) {
  const { id, title } = props, description = props.description === undefined ? '' : props.description;
  if (typeof id !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]*$/.test(id) || id.includes('--lq-')) throw new TypeError('A unique non-reserved Collapsible id is required');
  if (typeof title !== 'string' || !title.trim() || typeof description !== 'string') throw new TypeError('Collapsible title and description must be text');
  const mode = props.mode === undefined ? 'responsive' : props.mode;
  if (!['responsive', 'always'].includes(mode)) throw new TypeError('Invalid Collapsible mode');
  const states = {};
  for (const key of Object.keys(stateKeys)) {
    const value = props[key] === undefined ? key === 'open' : props[key];
    if (typeof value !== 'boolean') throw new TypeError('Collapsible states must be boolean');
    states[key] = value;
  }
  if (props.attrs != null && (typeof props.attrs !== 'object' || Array.isArray(props.attrs))) throw new TypeError('Collapsible attrs must be a mapping');
  for (const [key, value] of Object.entries(props.attrs || {})) {
    if ((!/^(?:aria|data)-[a-z][a-z0-9_.:-]*$/.test(key) && key !== 'title') || (value != null && !['string', 'boolean'].includes(typeof value))) throw new TypeError('Unsupported Collapsible attribute');
  }
  const attrs = normalizeAttributes(props.attrs);
  for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || ['aria-hidden', 'aria-label', 'aria-labelledby', 'aria-expanded', 'aria-controls', 'aria-disabled', 'aria-live'].includes(key)) delete attrs[key];
  Object.assign(attrs, { id, class: 'lq-collapsible lq-surface', 'data-lq-collapsible': '', 'data-lq-mode': mode });
  for (const [key, value] of Object.entries(states)) attrs[`data-lq-${stateKeys[key]}`] = String(value);
  if (Object.values(states).some(Boolean)) attrs.open = '';
  if (props.persist != null) {
    const p = props.persist;
    if (typeof p !== 'object' || Array.isArray(p) || Object.keys(p).sort().join(',') !== 'identity,key,resource') throw new TypeError('Persistence requires identity, resource and key');
    const parts = ['identity', 'resource', 'key'].map(key => p[key]);
    if (parts.some(part => typeof part !== 'string' || !part.trim() || [...part].length > 256 || /[\x00-\x1f\x7f]/.test(part))) throw new TypeError('Invalid scoped persistence key');
    attrs['data-lq-persist'] = JSON.stringify(parts);
  }
  return { attrs, title, description, summary_id: `${id}--lq-summary`, content_id: `${id}--lq-content` };
}
export function collapsibleMarkup(props = {}) {
  const p = collapsibleProps(props);
  return `<details${attributesMarkup(p.attrs)}><summary class="lq-collapsible__summary" id="${escapeHtml(p.summary_id)}" aria-controls="${escapeHtml(p.content_id)}"><span class="lq-collapsible__title">${escapeHtml(p.title)}</span><span class="lq-collapsible__indicator" aria-hidden="true">⌄</span></summary><div class="lq-collapsible__content" id="${escapeHtml(p.content_id)}" role="region" aria-labelledby="${escapeHtml(p.summary_id)}">${p.description ? `<p class="lq-collapsible__description">${escapeHtml(p.description)}</p>` : ''}<div class="lq-collapsible__slot" data-lq-slot="content"></div></div></details>`;
}
export function createCollapsible(props = {}, children = [], doc = document) {
  const p = collapsibleProps(props);
  if (!Array.isArray(children) || children.some(child => !(child instanceof doc.defaultView.Node))) throw new TypeError('Collapsible slots accept existing Nodes only');
  const make = (tag, attrs = {}, text) => { const el = doc.createElement(tag); for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value); if (text !== undefined) el.textContent = text; return el; };
  const root = make('details', p.attrs), summary = make('summary', { class: 'lq-collapsible__summary', id: p.summary_id, 'aria-controls': p.content_id });
  summary.append(make('span', { class: 'lq-collapsible__title' }, p.title), make('span', { class: 'lq-collapsible__indicator', 'aria-hidden': 'true' }, '⌄'));
  const content = make('div', { class: 'lq-collapsible__content', id: p.content_id, role: 'region', 'aria-labelledby': p.summary_id });
  if (p.description) content.append(make('p', { class: 'lq-collapsible__description' }, p.description));
  const slot = make('div', { class: 'lq-collapsible__slot', 'data-lq-slot': 'content' }); slot.append(...children); content.append(slot); root.append(summary, content); return root;
}
export const html = Object.freeze({ collapsible: collapsibleMarkup });
const ownerKey = Symbol.for('lanshare.lq.collapsible');
const preferenceKey = Symbol.for('lanshare.lq.collapsible.preference');

export function enhanceCollapsible(root) {
  if (!root?.matches?.('details[data-lq-collapsible]')) throw new TypeError('Expected a native LQ details element');
  if (root[ownerKey]) return root[ownerKey];
  const summary = root.querySelector(':scope > summary'), content = root.querySelector(':scope > .lq-collapsible__content');
  if (!summary || !content) throw new TypeError('Collapsible requires summary and content slots');
  const doc = root.ownerDocument, win = doc.defaultView, narrow = win.matchMedia('(max-width: 767px)');
  const original = Object.fromEntries(['aria-expanded', 'aria-disabled', 'tabindex'].map(key => [key, summary.getAttribute(key)]));
  const previous = root[preferenceKey];
  let desired = previous ? (root.open === previous.rendered ? previous.desired : root.open) : root.dataset.lqDefaultOpen !== 'false';
  let destroyed = false, pendingOpen = null, observedOpen = root.open;
  let key = null;
  if (root.dataset.lqPersist) {
    try {
      const parts = JSON.parse(root.dataset.lqPersist);
      if (Array.isArray(parts) && parts.length === 3 && parts.every(part => typeof part === 'string' && part.trim() && [...part].length <= 256 && !/[\x00-\x1f\x7f]/.test(part))) key = `lq.collapsible:${JSON.stringify(parts)}`;
      const stored = key && win.localStorage.getItem(key); if (stored === '1' || stored === '0') desired = stored === '1';
    } catch { /* Storage is optional; the native disclosure remains usable. */ }
  }
  const guarded = () => ['KeepOpen', 'HasError', 'Current', 'Dirty'].some(key => root.dataset[`lq${key}`] === 'true');
  const locked = () => guarded() || (root.dataset.lqMode !== 'always' && !narrow.matches);
  const save = () => { if (key) try { win.localStorage.setItem(key, desired ? '1' : '0'); } catch { /* Optional preference. */ } };
  function apply() {
    const open = locked() || desired;
    if (!open && content.contains(doc.activeElement)) summary.focus();
    if (root.open !== open) { pendingOpen = open; root.open = open; }
    observedOpen = root.open;
    root[preferenceKey] = { desired, rendered: root.open };
    summary.setAttribute('aria-expanded', String(open)); summary.setAttribute('aria-disabled', String(locked()));
    if (locked()) summary.setAttribute('tabindex', '-1'); else if (original.tabindex === null) summary.removeAttribute('tabindex'); else summary.setAttribute('tabindex', original.tabindex);
  }
  const click = event => { if (locked()) event.preventDefault(); };
  const keyboard = event => { if (locked() && (event.key === 'Enter' || event.key === ' ')) event.preventDefault(); };
  const toggle = () => {
    if (destroyed) return;
    if (pendingOpen !== null && root.open === pendingOpen) { pendingOpen = null; apply(); return; }
    pendingOpen = null;
    if (root.open === observedOpen) { apply(); return; }
    if (locked()) { apply(); return; }
    desired = root.open; save(); apply();
  };
  const breakpoint = () => { if (!destroyed) apply(); };
  summary.addEventListener('click', click); summary.addEventListener('keydown', keyboard); root.addEventListener('toggle', toggle); narrow.addEventListener('change', breakpoint);
  const handle = {
    setOpen(value) { if (typeof value !== 'boolean') throw new TypeError('open must be boolean'); if (destroyed) return root.open; desired = value; save(); apply(); return root.open; },
    refresh(states = {}) {
      if (destroyed) return;
      if (Object.entries(states).some(([name, value]) => !['keepOpen', 'hasError', 'current', 'dirty'].includes(name) || typeof value !== 'boolean')) throw new TypeError('Only explicit boolean guard states can be refreshed');
      for (const [name, value] of Object.entries(states)) {
        root.setAttribute(`data-lq-${stateKeys[name]}`, String(value));
      }
      apply();
    },
    destroy() {
      if (destroyed) return; destroyed = true;
      summary.removeEventListener('click', click); summary.removeEventListener('keydown', keyboard); root.removeEventListener('toggle', toggle); narrow.removeEventListener('change', breakpoint);
      for (const [name, value] of Object.entries(original)) if (value === null) summary.removeAttribute(name); else summary.setAttribute(name, value);
      delete root[ownerKey];
    },
  };
  root[ownerKey] = handle; apply(); return handle;
}
