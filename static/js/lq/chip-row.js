import { componentProps } from './component-props.js';
import { componentMarkup, createComponent } from './components.js';
import { attributesMarkup } from './html.js';

const OWNER = Symbol.for('lanshare.lq.chip-row');
const LIFETIME = Symbol.for('lanshare.lq.chip-row-lifetime');
const COLLAPSED = 'data-lq-chip-collapsed';
const MORE = { label: '更多', variant: 'soft', icon: 'chevron-down' };
const itemKeys = new Set(['label', 'kind', 'tone', 'size', 'pressed', 'disabled', 'removable', 'removeLabel', 'id', 'attrs']);
const record = value => value && typeof value === 'object' && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
/** Typed chip props only. Existing business Nodes are enhanced in place with bindChipRow. */
export function chipRowProps(props = {}) {
  if (!record(props) || Object.keys(props).some(key => !['id', 'label', 'items'].includes(key))) throw new TypeError('Invalid LQ chip row props');
  if (typeof props.id !== 'string' || !/^[A-Za-z][\w:-]*$/.test(props.id)) throw new TypeError('Chip row requires an id');
  if (typeof props.label !== 'string' || !props.label.trim()) throw new TypeError('Chip row requires a label');
  if (!Array.isArray(props.items) || Object.keys(props.items).length !== props.items.length) throw new TypeError('Chip row items must be a dense array');
  const items = Array.from(props.items, item => {
    if (!record(item) || Object.keys(item).some(key => !itemKeys.has(key)) || typeof item.label !== 'string' || !item.label.trim()) throw new TypeError('Invalid chip row item');
    componentProps('chip', item);
    return { ...item };
  });
  return { attrs: { id: props.id, role: 'group', 'aria-label': props.label.trim() }, track_id: `${props.id}--lq-track`, items };
}
export function chipRowMarkup(props) {
  const p = chipRowProps(props);
  return `<div class="lq-chip-row"${attributesMarkup(p.attrs)}><div class="lq-chip-row__track" id="${p.track_id}" tabindex="0">${p.items.map(item => componentMarkup('chip', item)).join('')}</div><div class="lq-chip-row__disclosure" hidden>${componentMarkup('button', MORE)}</div></div>`;
}
export function createChipRow(props, doc = document) {
  const p = chipRowProps(props), root = doc.createElement('div'), track = doc.createElement('div'), disclosure = doc.createElement('div');
  root.className = 'lq-chip-row'; for (const [key, value] of Object.entries(p.attrs)) root.setAttribute(key, value);
  track.className = 'lq-chip-row__track'; track.id = p.track_id; track.tabIndex = 0;
  for (const item of p.items) track.append(createComponent('chip', item, doc));
  disclosure.className = 'lq-chip-row__disclosure'; disclosure.hidden = true; disclosure.append(createComponent('button', MORE, doc));
  root.append(track, disclosure); return root;
}
function watch(root, refresh, destroy) {
  const doc = root.ownerDocument;
  let record = doc[LIFETIME];
  if (!record) {
    const owners = new Map();
    const observer = new doc.defaultView.MutationObserver(changes => {
      for (const [node, owner] of owners) {
        if (!node.isConnected) owner.destroy();
        else if (changes.some(change => node.contains(change.target) || [...change.addedNodes].some(child => child.contains?.(node)))) owner.refresh();
      }
    });
    observer.observe(doc.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden'] });
    record = doc[LIFETIME] = { owners, observer };
  }
  record.owners.set(root, { refresh, destroy });
  return () => { record.owners.delete(root); if (!record.owners.size) { record.observer.disconnect(); if (doc[LIFETIME] === record) delete doc[LIFETIME]; } };
}
/** No reparenting, cloning, selection state, or application event ownership. */
export function bindChipRow(root) {
  if (root?.[OWNER]) return root[OWNER];
  const doc = root?.ownerDocument, win = doc?.defaultView;
  if (!root?.matches?.('.lq-chip-row') || !root.isConnected) throw new TypeError('A connected chip row is required');
  const track = root.querySelector(':scope > .lq-chip-row__track'), disclosure = root.querySelector(':scope > .lq-chip-row__disclosure');
  const button = disclosure?.querySelector(':scope > button.lq-btn'), label = button?.querySelector('.lq-btn__label');
  if (!track?.id || !button || !label || [...track.children].some(item => !item.matches('.lq-chip'))) throw new TypeError('Invalid chip row structure');
  const saved = new Map();
  const remember = (node, key) => { let attrs = saved.get(node); if (!attrs) saved.set(node, attrs = new Map()); if (!attrs.has(key)) attrs.set(key, node.getAttribute(key)); };
  const set = (node, key, value) => { remember(node, key); if (value === null) node.removeAttribute(key); else if (node.getAttribute(key) !== value) node.setAttribute(key, value); };
  const oldLabel = label.textContent;
  let destroyed = false, expanded = false, overflow = [], frame = 0;
  const focusMore = () => { if (root.isConnected && !disclosure.hidden) button.focus({ preventScroll: true }); };
  const edges = () => {
    frame = 0; if (destroyed) return;
    const bounds = track.getBoundingClientRect(), children = [...track.children].filter(item => !item.hidden && !item.hasAttribute(COLLAPSED));
    const left = children.some(item => item.getBoundingClientRect().left < bounds.left - 1), right = children.some(item => item.getBoundingClientRect().right > bounds.right + 1);
    set(track, 'data-lq-fade-left', left ? '' : null); set(track, 'data-lq-fade-right', right ? '' : null);
  };
  const schedule = () => { if (!destroyed && !frame) frame = win.requestAnimationFrame(edges); };
  function refresh() {
    if (destroyed) return;
    if (!track.isConnected || track.parentElement !== root || !root.contains(button)) { destroy(); return; }
    for (const [node, attrs] of saved) if (attrs.has(COLLAPSED) && node.parentElement !== track) {
      const value = attrs.get(COLLAPSED); if (value === null) node.removeAttribute(COLLAPSED); else node.setAttribute(COLLAPSED, value);
      saved.delete(node);
    }
    const items = [...track.children].filter(item => item.matches('.lq-chip') && !item.hidden);
    overflow = items.slice(8);
    if (overflow.length && !expanded && overflow.some(item => item.contains(doc.activeElement))) { set(disclosure, 'hidden', null); focusMore(); if (destroyed) return; }
    for (const [node, attrs] of saved) if (attrs.has(COLLAPSED) && !overflow.includes(node)) set(node, COLLAPSED, attrs.get(COLLAPSED));
    for (const item of overflow) set(item, COLLAPSED, expanded ? null : '');
    const hidden = !overflow.length;
    if (hidden && button.contains(doc.activeElement)) track.focus({ preventScroll: true });
    if (destroyed) return;
    set(disclosure, 'hidden', hidden ? '' : null);
    set(button, 'aria-expanded', String(expanded)); set(button, 'aria-controls', track.id);
    const text = expanded ? '收起' : `更多 (${overflow.length})`;
    if (label.textContent !== text) label.textContent = text;
    set(button, 'aria-label', text); schedule();
  }
  function setExpanded(value) {
    if (typeof value !== 'boolean') throw new TypeError('expanded must be boolean');
    if (destroyed) return false;
    expanded = value; refresh(); return !destroyed;
  }
  const click = () => setExpanded(!expanded);
  const invalid = event => { if (overflow.some(item => item.contains(event.target))) setExpanded(true); };
  function destroy() {
    if (destroyed) return; destroyed = true;
    // Restore every item before restoring a potentially hidden SSR disclosure button.
    for (const [node, attrs] of saved) if (attrs.has(COLLAPSED)) { const value = attrs.get(COLLAPSED); if (value === null) node.removeAttribute(COLLAPSED); else node.setAttribute(COLLAPSED, value); }
    if (root.isConnected && button.contains(doc.activeElement)) track.focus({ preventScroll: true });
    for (const [node, attrs] of saved) for (const [key, value] of attrs) { if (value === null) node.removeAttribute(key); else node.setAttribute(key, value); }
    label.textContent = oldLabel;
    button.removeEventListener('click', click); root.removeEventListener('invalid', invalid, true); track.removeEventListener('scroll', schedule);
    resize.disconnect(); unwatch(); if (frame) win.cancelAnimationFrame(frame); frame = 0;
    if (root[OWNER] === handle) delete root[OWNER];
  }
  const handle = { root, refresh, setExpanded, destroy, get expanded() { return expanded; }, get destroyed() { return destroyed; } };
  const resize = new win.ResizeObserver(schedule), unwatch = watch(root, refresh, destroy);
  root[OWNER] = handle; resize.observe(track);
  button.addEventListener('click', click); root.addEventListener('invalid', invalid, true); track.addEventListener('scroll', schedule, { passive: true });
  refresh(); return handle;
}
