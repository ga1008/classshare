/** Native table presentation. No sorting, pagination requests or selected-id store. */
import { normalizeAttributes, escapeHtml, attributesMarkup, safeUrl } from './html.js';
import { componentProps } from './component-props.js';
import { componentMarkup, createComponent } from './components.js';

export const tableKinds = Object.freeze(['table', 'pager', 'bulk_bar', 'result_count']);
const text = (value, required = false) => { value = value ?? ''; if (typeof value !== 'string' || (required && !value.trim())) throw new TypeError('Table text must be plain text'); return value; };
const number = (value, minimum = 0) => { if (!Number.isSafeInteger(value) || value < minimum) throw new TypeError('Table counts must be safe integers'); return value; };
const key = (value, identity = false) => { value = text(value, true); if (!(identity ? /^[A-Za-z][A-Za-z0-9_-]*$/ : /^[A-Za-z0-9_-]+$/).test(value) || value.includes('--lq-')) throw new TypeError('A stable non-reserved key is required'); return value; };
const flag = (p, name, fallback = false) => { const value = p[name] === undefined ? fallback : p[name]; if (typeof value !== 'boolean') throw new TypeError('Table flags must be boolean'); return value; };
const choice = (value, choices) => { if (!choices.includes(value)) throw new TypeError('Invalid table variant'); return value; };
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function attributes(value) {
  if (value != null && !object(value)) throw new TypeError('Table attrs must be a mapping');
  for (const [name, item] of Object.entries(value || {})) if ((!/^(?:aria|data)-[a-z][a-z0-9_.:-]*$/.test(name) && !['id', 'title'].includes(name)) || (item != null && !['string', 'boolean'].includes(typeof item))) throw new TypeError('Unsupported table attribute');
  const attrs = normalizeAttributes(value);
  for (const name of Object.keys(attrs)) if (name.startsWith('data-lq-') || ['id', 'aria-label', 'aria-labelledby', 'aria-sort', 'aria-checked', 'aria-busy'].includes(name)) delete attrs[name];
  return attrs;
}
function button(value) {
  if (!object(value) || Object.keys(value).some(key => !['label', 'href', 'id', 'attrs', 'variant', 'disabled', 'loading'].includes(key))) throw new TypeError('Bulk actions require structured Button props');
  const p = { label: text(value.label, true), href: value.href ?? null, id: value.id ?? null, attrs: value.attrs ?? null,
    variant: value.variant === undefined ? 'soft' : value.variant, size: 'sm', icon: null, disabled: value.disabled === undefined ? false : value.disabled, ariaDisabled: false, loading: value.loading === undefined ? false : value.loading, type: 'button' };
  const normalized = componentProps('button', p); p.href = normalized.attrs.href ?? null;
  if (p.id !== null) p.id = key(p.id, true);
  p.attrs = Object.fromEntries(Object.entries(p.attrs || {}).filter(([, value]) => value != null).map(([name, value]) => [name, String(value)]));
  return { button: p };
}
const checkbox = (label, attrs) => node('label', { class: 'lq-table__check' }, [node('input', { type: 'checkbox', 'aria-label': label, ...attrs })]);
function table(p, attrs) {
  const id = key(p.id, true), caption = text(p.caption, true), mode = choice(p.mode === undefined ? 'record' : p.mode, ['record', 'matrix']);
  const density = choice(p.density === undefined ? 'comfortable' : p.density, ['comfortable', 'dense']), selectable = flag(p, 'selectable');
  const rows = p.rows === undefined ? [] : p.rows;
  if (!Array.isArray(p.columns) || !p.columns.length || !Array.isArray(rows)) throw new TypeError('Table columns and rows must be lists');
  const keys = new Set(); let activeSorts = 0, rowHeaders = 0;
  const columns = p.columns.map(item => {
    if (!object(item) || Object.keys(item).some(key => !['key', 'label', 'align', 'sortable', 'sort', 'rowHeader'].includes(key))) throw new TypeError('Invalid table column');
    const k = key(item.key); if (keys.has(k)) throw new TypeError('Duplicate table column key'); keys.add(k);
    const col = { key: k, label: text(item.label, true), align: choice(item.align === undefined ? 'start' : item.align, ['start', 'end']), sortable: flag(item, 'sortable'), sort: choice(item.sort === undefined ? 'none' : item.sort, ['none', 'ascending', 'descending']), rowHeader: flag(item, 'rowHeader') };
    if (col.sort !== 'none') { if (!col.sortable) throw new TypeError('Only sortable columns have sort state'); activeSorts++; }
    if (col.rowHeader) rowHeaders++; return col;
  });
  if (activeSorts > 1 || rowHeaders > 1) throw new TypeError('Table supports one active sort and one row header');
  const rowKeys = new Set();
  const normalizedRows = rows.map(item => {
    if (!object(item) || Object.keys(item).some(key => !['key', 'label', 'cells', 'selected', 'disabled'].includes(key))) throw new TypeError('Invalid table row');
    const k = key(item.key); if (rowKeys.has(k)) throw new TypeError('Duplicate table row key'); rowKeys.add(k);
    if (!object(item.cells) || Object.keys(item.cells).length !== keys.size || Object.keys(item.cells).some(name => !keys.has(name))) throw new TypeError('Every row must provide exactly the declared cells');
    const cells = Object.fromEntries(Object.entries(item.cells).map(([name, value]) => [name, typeof value === 'number' ? String(number(value, -Number.MAX_SAFE_INTEGER)) : text(value)]));
    return { key: k, label: text(item.label, selectable), cells, selected: flag(item, 'selected'), disabled: flag(item, 'disabled') };
  });
  Object.assign(attrs, { id: `${id}--lq-wrap`, class: `lq-table-shell lq-surface lq-table-shell--${mode} lq-table-shell--${density}`, 'data-lq-table': '' });
  const head = [], body = [], selectionHeader = `${id}--lq-select`;
  const eligible = normalizedRows.filter(row => !row.disabled), selected = eligible.filter(row => row.selected).length;
  if (selectable) {
    const state = selected > 0 && selected < eligible.length ? 'mixed' : eligible.length > 0 && selected === eligible.length ? 'true' : 'false';
    const master = { 'data-lq-select-all': '', 'aria-checked': state, ...(state === 'true' ? { checked: '' } : {}), ...(!eligible.length ? { disabled: '' } : {}) };
    head.push(node('th', { scope: 'col', id: selectionHeader, role: 'columnheader', class: 'lq-table__selection' }, [checkbox('选择本页可操作行', master)]));
  }
  for (const col of columns) {
    const th = { scope: 'col', role: 'columnheader', id: `${id}--lq-col-${col.key}`, 'data-align': col.align };
    let label = [col.label];
    if (col.sortable) { th['aria-sort'] = col.sort; label = [node('button', { type: 'button', class: 'lq-table__sort', 'data-lq-sort': col.key, 'aria-label': `按${col.label}排序` }, [col.label, node('span', { 'aria-hidden': 'true', class: 'lq-table__sort-mark' }, ['↕'])])]; }
    head.push(node('th', th, label));
  }
  const selectionName = text(p.selectionName === undefined ? 'selected' : p.selectionName, true);
  for (const row of normalizedRows) {
    const rowId = `${id}--lq-row-${row.key}`, cells = [];
    if (selectable) {
      const attrs = { 'data-lq-select-row': '', name: selectionName, value: row.key, ...(row.selected ? { checked: '' } : {}), ...(row.disabled ? { disabled: '' } : {}) };
      cells.push(node('td', { role: 'cell', headers: selectionHeader + (rowHeaders ? ` ${rowId}` : ''), 'data-label': '选择', class: 'lq-table__selection' }, [checkbox(`选择 ${row.label}`, attrs)]));
    }
    for (const col of columns) {
      const attrs = { role: col.rowHeader ? 'rowheader' : 'cell', headers: `${id}--lq-col-${col.key}` + (rowHeaders && !col.rowHeader ? ` ${rowId}` : ''), 'data-label': col.label, 'data-align': col.align, ...(col.rowHeader ? { scope: 'row', id: rowId } : {}) };
      const name = `cell:${row.key}:${col.key}`;
      cells.push(node(col.rowHeader ? 'th' : 'td', attrs, [node('span', { class: 'lq-table__label', 'aria-hidden': 'true' }, [col.label]), node('div', { class: 'lq-table__cell', 'data-lq-slot': name }, [row.cells[col.key], { slot: name }])]));
    }
    body.push(node('tr', { role: 'row', 'data-lq-row-key': row.key }, cells));
  }
  const nativeTable = node('table', { id, class: 'lq-table', role: 'table' }, [node('caption', { id: `${id}--lq-caption` }, [caption]), node('thead', { role: 'rowgroup' }, [node('tr', { role: 'row' }, head)]), node('tbody', { role: 'rowgroup' }, body)]);
  return node('div', attrs, [node('div', { class: 'lq-table__scroll', tabindex: '0', role: 'region', 'aria-labelledby': `${id}--lq-caption` }, [nativeTable]), node('div', { class: 'lq-table__empty', 'data-lq-slot': 'empty' }, [{ slot: 'empty' }])]);
}

export function tableProps(kind, p = {}) {
  if (!tableKinds.includes(kind) || ['html', 'rawHTML', 'raw_html'].some(key => key in p)) throw new TypeError('Invalid table component or raw HTML');
  const attrs = attributes(p.attrs); if (p.id != null) attrs.id = key(p.id, true);
  if (kind === 'table') return table(p, attrs);
  if (kind === 'result_count') {
    const state = choice(p.state === undefined ? 'ready' : p.state, ['ready', 'loading']), label = text(p.label === undefined ? '条结果' : p.label, true), count = p.count == null ? null : number(p.count);
    if (state === 'ready' && count === null) throw new TypeError('Ready results require a known count');
    Object.assign(attrs, { class: 'lq-result-count', 'data-state': state === 'loading' ? 'loading' : count === 0 ? 'empty' : 'ready', ...(state === 'loading' ? { 'aria-busy': 'true' } : {}) });
    return node('p', attrs, [state === 'loading' ? '正在加载…' : `${count} ${label}`]);
  }
  if (kind === 'bulk_bar') {
    const count = number(p.selectedCount), actions = p.actions === undefined ? [] : p.actions;
    if (!Array.isArray(actions)) throw new TypeError('Bulk actions must be a list');
    const buttons = actions.map(button);
    Object.assign(attrs, { class: 'lq-bulk-bar', role: 'group', 'aria-label': text(p.label === undefined ? '批量操作' : p.label, true), ...(count === 0 ? { hidden: '' } : {}) });
    return node('div', attrs, [node('span', { class: 'lq-bulk-bar__count' }, [`已选择 ${count} 项`]), node('div', { class: 'lq-bulk-bar__actions' }, count ? buttons : [])]);
  }
  const page = number(p.page), total = number(p.totalPages), disabled = flag(p, 'disabled'), links = p.links;
  if ((total === 0 && page !== 0) || (total > 0 && (page < 1 || page > total))) throw new TypeError('Page is outside the declared total');
  if (links != null) {
    if (!object(links)) throw new TypeError('Pager links must map page numbers to safe URLs');
    for (const [key, value] of Object.entries(links)) { if (!/^[1-9][0-9]*$/.test(key) || Number(key) > total) throw new TypeError('Invalid pager link page'); if (safeUrl(value) == null) throw new TypeError('Pager links require explicit safe URLs'); }
  }
  Object.assign(attrs, { class: 'lq-pager', 'aria-label': text(p.label === undefined ? '分页' : p.label, true) });
  const control = (target, label, blocked = false, current = false) => {
    const attrs = { class: 'lq-pager__control', 'data-lq-page': String(target), 'aria-label': label, ...(current ? { 'aria-current': 'page' } : {}) };
    blocked = blocked || disabled || current;
    if (links != null && !blocked) { if (!Object.hasOwn(links, String(target))) throw new TypeError('Every enabled displayed page needs an explicit href'); attrs.href = safeUrl(links[target]); return node('a', attrs, [label]); }
    attrs.type = 'button'; if (blocked) Object.assign(attrs, { disabled: '', 'aria-disabled': 'true' }); return node('button', attrs, [label]);
  };
  const children = [control(Math.max(1, page - 1), '上一页', page <= 1)];
  const pages = new Set(total ? [1, total] : []);
  for (let target = Math.max(1, page - 1); target <= Math.min(total, page + 1); target++) pages.add(target);
  let previous = 0;
  for (const target of [...pages].sort((a, b) => a - b)) {
    if (previous && target - previous > 1) children.push(node('span', { class: 'lq-pager__ellipsis', 'aria-hidden': 'true' }, ['…']));
    const item = control(target, `第 ${target} 页`, false, target === page); item.children = [String(target)]; children.push(item); previous = target;
  }
  children.push(control(Math.min(total, page + 1), '下一页', page >= total), node('span', { class: 'lq-pager__summary' }, [`第 ${page} / ${total} 页`]));
  return node('nav', attrs, children);
}

function markup(tree) {
  if (typeof tree === 'string') return escapeHtml(tree);
  if (tree.slot) return '';
  if (tree.button) return componentMarkup('button', tree.button);
  const start = `<${tree.tag}${attributesMarkup(tree.attrs)}>`;
  return tree.tag === 'input' ? start : `${start}${tree.children.map(markup).join('')}</${tree.tag}>`;
}
export const tableMarkup = (kind, props = {}) => markup(tableProps(kind, props));
export const html = Object.freeze(Object.fromEntries(tableKinds.map(kind => [kind, props => tableMarkup(kind, props)])));
export function createTable(kind, props = {}, slots = {}, doc = document) {
  const tree = tableProps(kind, props), allowed = new Set(), seen = new Set();
  const collect = n => { if (n?.slot) allowed.add(n.slot); for (const child of n?.children || []) collect(child); }; collect(tree);
  if (!object(slots)) throw new TypeError('Table slots must map names to Nodes');
  for (const [name, nodes] of Object.entries(slots)) {
    if (!allowed.has(name) || !Array.isArray(nodes)) throw new TypeError('Unknown table slot');
    for (const n of nodes) { if (!(n instanceof doc.defaultView.Node) || seen.has(n) || ![1, 3, 8, 11].includes(n.nodeType)) throw new TypeError('Table slots accept distinct existing Nodes only'); seen.add(n); }
  }
  for (const a of seen) for (const b of seen) if (a !== b && a.contains(b)) throw new TypeError('Table slot Nodes cannot overlap');
  const element = n => {
    if (typeof n === 'string') return doc.createTextNode(n);
    if (n.slot) { const result = doc.createDocumentFragment(); result.append(...(slots[n.slot] || [])); return result; }
    if (n.button) return createComponent('button', n.button, doc);
    const result = doc.createElement(n.tag); for (const [key, value] of Object.entries(n.attrs)) result.setAttribute(key, value);
    for (const child of n.children) result.append(element(child)); return result;
  };
  return element(tree);
}

const ownerKey = Symbol.for('lanshare.lq.table');
export function enhanceTable(root, { selection = 'controller' } = {}) {
  if (!root?.matches?.('[data-lq-table]') || !['controller', 'native'].includes(selection)) throw new TypeError('Expected an LQ table and explicit selection mode');
  if (root[ownerKey]) { if (root[ownerKey].selection !== selection) throw new TypeError('Destroy the previous selection owner before changing modes'); return root[ownerKey]; }
  const table = root.querySelector(':scope > .lq-table__scroll > table');
  if (!table) throw new TypeError('Native table structure required');
  const master = table.querySelector('thead [data-lq-select-all]'), win = root.ownerDocument.defaultView;
  let destroyed = false, batching = false;
  const owns = input => input?.closest?.('table') === table;
  const rows = () => [...table.querySelectorAll('input[data-lq-select-row]')].filter(input => owns(input) && !input.matches(':disabled') && !input.closest('[hidden]'));
  const refresh = () => {
    if (destroyed || !master) return;
    const candidates = rows(), checked = candidates.filter(input => input.checked).length;
    master.checked = candidates.length > 0 && checked === candidates.length;
    master.indeterminate = checked > 0 && checked < candidates.length;
    master.disabled = candidates.length === 0 || root.dataset.lqSelectionDisabled === 'true';
    master.setAttribute('aria-checked', master.indeterminate ? 'mixed' : String(master.checked));
  };
  const change = event => {
    if (destroyed || batching || !owns(event.target)) return;
    if (event.target === master && selection === 'native' && !master.disabled) {
      const checked = master.checked; batching = true;
      try {
        for (const input of rows()) {
          if (destroyed || !root.contains(input)) break;
          if (!input.matches(':disabled') && input.checked !== checked) {
            input.checked = checked;
            input.dispatchEvent(new win.Event('input', { bubbles: true }));
            if (destroyed || !root.contains(input) || !owns(input)) break;
            input.dispatchEvent(new win.Event('change', { bubbles: true }));
          }
        }
      } finally { batching = false; refresh(); }
    } else win.queueMicrotask(refresh);
  };
  const form = table.closest('form'), reset = () => win.queueMicrotask(refresh);
  root.addEventListener('change', change); form?.addEventListener('reset', reset);
  const handle = { selection, refresh, destroy() { if (destroyed) return; destroyed = true; root.removeEventListener('change', change); form?.removeEventListener('reset', reset); delete root[ownerKey]; } };
  root[ownerKey] = handle; refresh(); return handle;
}
