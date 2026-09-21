import { normalizeAttributes, escapeHtml, attributesMarkup } from './html.js';

const controls = ['input', 'textarea', 'select', 'checkbox', 'radio', 'range', 'switch'];
export const formKinds = Object.freeze(['field', ...controls, 'form_section', 'form_actions', 'error_summary']);
const inputTypes = ['text', 'search', 'email', 'password', 'url', 'tel', 'number', 'date', 'time', 'datetime-local', 'month', 'week'];
const own = (p, key, fallback) => p[key] === undefined ? fallback : p[key];
function text(value) {
  if (value == null) return '';
  if (!['string', 'number', 'boolean'].includes(typeof value) || (typeof value === 'number' && !Number.isFinite(value))) throw new TypeError('LQ form text must be finite and scalar');
  return String(value);
}
function id(value) {
  const result = text(value);
  if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(result) || result.includes('--lq-')) throw new TypeError('Invalid or reserved LQ form id');
  return result;
}
function flag(p, key, fallback = false) { const value = own(p, key, fallback); if (typeof value !== 'boolean') throw new TypeError(`LQ ${key} must be boolean`); return value; }
function integer(value, name, minimum = 0) { if (!Number.isInteger(value) || value < minimum) throw new TypeError(`Invalid LQ ${name}`); return value; }
function attrs(value) {
  if (value != null && Object.keys(value).some(key => ['target', 'rel'].includes(key))) throw new TypeError('Unsupported LQ form attribute');
  const result = normalizeAttributes(value);
  for (const key of Object.keys(result)) if (key.startsWith('data-lq-')) delete result[key];
  for (const key of ['id', 'name', 'form', 'aria-hidden', 'aria-live', 'aria-label', 'aria-labelledby', 'aria-invalid', 'aria-required', 'aria-disabled', 'aria-readonly', 'aria-checked', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext']) delete result[key];
  return result;
}
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });

function control(kind, p) {
  const identity = id(p.id), label = text(p.label).trim(), size = own(p, 'size', 'md');
  if (!label || !['sm', 'md', 'lg'].includes(size)) throw new TypeError('A visible label and valid control size are required');
  const required = flag(p, 'required'), disabled = flag(p, 'disabled'), readonly = flag(p, 'readOnly');
  if (readonly && !['input', 'textarea'].includes(kind)) throw new TypeError('This native control does not support readonly');
  for (const [key, allowed] of [['clearable', ['input']], ['count', ['textarea']], ['autoGrow', ['textarea']], ['checked', ['checkbox', 'radio', 'switch']]]) {
    if (key in p && flag(p, key) && !allowed.includes(kind)) throw new TypeError(`${key} is not supported by this native control`);
  }
  const a = attrs(p.attrs);
  Object.assign(a, { id: identity, class: `lq-${kind} lq-control--${size}` });
  for (const key of ['name', 'form']) if (p[key] != null) a[key] = key === 'form' ? id(p[key]) : text(p[key]);
  for (const [key, enabled] of [['required', required], ['disabled', disabled], ['readonly', readonly]]) if (enabled) a[key] = '';
  const help = text(p.help), error = text(p.error), count = kind === 'textarea' && flag(p, 'count');
  const descriptions = (a['aria-describedby'] || '').split(/\s+/).filter(item => item && !item.includes('--lq-'));
  for (const [suffix, present] of [['help', help], ['error', error], ['count', count]]) if (present) descriptions.push(`${identity}--lq-${suffix}`);
  delete a['aria-describedby'];
  if (descriptions.length) a['aria-describedby'] = [...new Set(descriptions)].join(' ');
  if (error) a['aria-invalid'] = 'true';
  const labelChildren = [label];
  if (required) labelChildren.push(node('span', { class: 'lq-field__required', 'aria-hidden': 'true' }, [' *']));
  const labelNode = node('label', { class: 'lq-field__label', for: identity, id: `${identity}--lq-label` }, labelChildren);
  let value = text(own(p, 'value', ['checkbox', 'radio', 'switch'].includes(kind) ? 'on' : ''));
  let children = [];
  if (kind === 'input') {
    const type = own(p, 'type', 'text');
    if (!inputTypes.includes(type)) throw new TypeError('Unsupported native input type');
    Object.assign(a, { type, value });
  } else if (kind === 'textarea') {
    value = value.replace(/\r\n?/g, '\n'); a.rows = text(integer(own(p, 'rows', 4), 'textarea rows', 1));
    if (flag(p, 'autoGrow')) a['data-lq-auto-grow'] = 'true';
    if (count) a['data-lq-count'] = `${identity}--lq-count`;
    children = [value];
  } else if (kind === 'select') {
    const options = own(p, 'options', []), values = new Set();
    if (!Array.isArray(options)) throw new TypeError('Select options must be a list');
    for (const item of options) {
      if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(key => !['value', 'label', 'disabled'].includes(key))) throw new TypeError('Invalid select option');
      const v = text(item.value); if (values.has(v)) throw new TypeError('Duplicate select option value'); values.add(v);
      const optionAttrs = { value: v };
      if (v === value) optionAttrs.selected = '';
      if (flag(item, 'disabled')) optionAttrs.disabled = '';
      children.push(node('option', optionAttrs, [text(item.label)]));
    }
    if (options.length && !values.has(value)) throw new TypeError('Select value must match an option');
  } else if (['checkbox', 'radio', 'switch'].includes(kind)) {
    Object.assign(a, { type: kind === 'radio' ? 'radio' : 'checkbox', value });
    if (kind === 'radio' && !a.name) throw new TypeError('Radio controls require a group name');
    if (flag(p, 'checked')) a.checked = '';
    if (kind === 'switch') a.role = 'switch';
  } else {
    const min = own(p, 'min', 0), max = own(p, 'max', 100), step = own(p, 'step', 1), current = own(p, 'value', min);
    if (![min, max, step, current].every(v => typeof v === 'number' && Number.isFinite(v)) || min >= max || step <= 0 || current < min || current > max) throw new TypeError('Invalid range bounds');
    value = text(current); Object.assign(a, { type: 'range', min: text(min), max: text(max), step: text(step), value, 'data-lq-range-output': `${identity}--lq-value` });
  }
  // A datalist lives outside the control, so the component owns the `list`
  // attribute rather than letting callers smuggle it through `attrs`. Checked
  // for every kind: silently dropping it on a select would hide the mistake.
  if (p.datalist != null) {
    if (kind !== 'input') throw new TypeError('Only an input can reference a datalist');
    const token = text(p.datalist).trim();
    if (!/^[A-Za-z][\w:.-]*$/.test(token)) throw new TypeError('Invalid LQ datalist id');
    a.list = token;
  }
  if (['input', 'textarea'].includes(kind)) {
    for (const key of ['placeholder', 'autocomplete', 'pattern', 'min', 'max', 'step']) if (p[key] != null) a[key] = text(p[key]);
    if (p.inputMode != null) {
      if (!['none', 'text', 'decimal', 'numeric', 'tel', 'search', 'email', 'url'].includes(p.inputMode)) throw new TypeError('Invalid inputMode');
      a.inputmode = p.inputMode;
    }
    for (const [prop, attr] of [['minLength', 'minlength'], ['maxLength', 'maxlength']]) if (p[prop] != null) a[attr] = text(integer(p[prop], attr));
    if (a.minlength !== undefined && a.maxlength !== undefined && Number(a.minlength) > Number(a.maxlength)) throw new TypeError('Invalid length bounds');
  }
  const element = node(['select', 'textarea'].includes(kind) ? kind : 'input', a, children);
  let fieldChildren;
  if (['checkbox', 'radio', 'switch'].includes(kind)) {
    labelNode.attrs.class += ' lq-choice'; labelNode.children = [element, node('span', { class: 'lq-choice__text' }, labelChildren)]; fieldChildren = [labelNode];
  } else {
    const inner = [];
    if (p.prefix) inner.push(node('span', { class: 'lq-field__prefix', 'aria-hidden': 'true' }, [text(p.prefix)]));
    inner.push(element);
    if (p.suffix) inner.push(node('span', { class: 'lq-field__suffix', 'aria-hidden': 'true' }, [text(p.suffix)]));
    if (flag(p, 'clearable')) {
      if (kind !== 'input' || !['text', 'search', 'email', 'password', 'url', 'tel', 'number'].includes(a.type)) throw new TypeError('Clearable is only supported for text-like inputs');
      const clear = { class: 'lq-field__clear', type: 'button', 'aria-label': `清除${label}`, 'data-lq-clear': identity };
      if (disabled || readonly) clear.disabled = '';
      if (!value) clear.hidden = '';
      inner.push(node('button', clear, [node('span', { 'aria-hidden': 'true' }, ['×'])]));
    }
    if (kind === 'range') inner.push(node('output', { class: 'lq-range__value', id: `${identity}--lq-value`, for: identity, 'aria-hidden': 'true' }, [value]));
    fieldChildren = [labelNode, node('div', { class: 'lq-field__control' }, inner)];
  }
  if (help) fieldChildren.push(node('p', { class: 'lq-field__help', id: `${identity}--lq-help` }, [help]));
  if (error) fieldChildren.push(node('p', { class: 'lq-field__error', id: `${identity}--lq-error` }, [node('span', { 'aria-hidden': 'true' }, ['! ']), error]));
  if (count) fieldChildren.push(node('p', { class: 'lq-field__count', id: `${identity}--lq-count` }, [`${value.length}${a.maxlength !== undefined ? ` / ${a.maxlength}` : ''} 字`]));
  return node('div', { class: `lq-field${error ? ' has-error' : ''}${disabled ? ' is-disabled' : ''}`, 'data-lq-field': identity }, fieldChildren);
}

export function formProps(kind, props = {}) {
  if (!formKinds.includes(kind)) throw new TypeError('Unknown LQ form component');
  if (kind === 'field') {
    const controlKind = own(props, 'control', 'input'), inner = own(props, 'controlProps', {});
    if (!controls.includes(controlKind) || !inner || typeof inner !== 'object' || Array.isArray(inner)) throw new TypeError('Invalid typed Field control');
    return control(controlKind, { ...inner, ...props });
  }
  if (controls.includes(kind)) return control(kind, props);
  const a = attrs(props.attrs);
  if (kind === 'form_actions') {
    a.class = 'lq-form-actions'; const children = [node('div', { class: 'lq-form-actions__content', 'data-lq-slot': 'content' }, [{ slot: 'content' }])];
    if (props.hint) children.push(node('p', { class: 'lq-form-actions__hint' }, [text(props.hint)]));
    return node('div', a, children);
  }
  const identity = id(props.id), title = text(props.title).trim();
  if (!title) throw new TypeError('A form section or summary requires a title');
  Object.assign(a, { id: identity, class: `lq-${kind.replaceAll('_', '-')}` });
  if (kind === 'form_section') {
    if (flag(props, 'surface')) a.class += ' lq-surface';
    if (flag(props, 'disabled')) a.disabled = '';
    const children = [node('legend', { class: 'lq-form-section__title' }, [title])];
    if (props.description) {
      a['aria-describedby'] = `${identity}--lq-description`;
      children.push(node('p', { class: 'lq-form-section__description', id: a['aria-describedby'] }, [text(props.description)]));
    }
    children.push(node('div', { class: 'lq-form-section__content', 'data-lq-slot': 'content' }, [{ slot: 'content' }]));
    return node('fieldset', a, children);
  }
  const errors = own(props, 'errors', []);
  if (!Array.isArray(errors) || !errors.length) throw new TypeError('ErrorSummary requires a nonempty error list');
  const links = errors.map(error => {
    if (!error || typeof error !== 'object' || Array.isArray(error) || Object.keys(error).some(key => !['id', 'message'].includes(key))) throw new TypeError('Invalid summary error');
    const target = id(error.id), message = text(error.message).trim();
    if (!message) throw new TypeError('Summary errors require a message');
    return node('li', {}, [node('a', { href: `#${target}`, 'data-lq-error-target': target }, [message])]);
  });
  Object.assign(a, { tabindex: '-1', 'aria-labelledby': `${identity}--lq-title` });
  return node('section', a, [node('h2', { class: 'lq-error-summary__title', id: a['aria-labelledby'] }, [title]), node('ul', {}, links)]);
}

function markup(tree) {
  if (typeof tree === 'string') return escapeHtml(tree);
  if (tree.slot) return '';
  const open = `<${tree.tag}${attributesMarkup(tree.attrs)}>`;
  // The HTML parser strips one initial newline in textarea; compensate only in
  // serialized HTML. Element factories set a real text node directly.
  const leading = tree.tag === 'textarea' && tree.children[0]?.startsWith('\n') ? '\n' : '';
  return tree.tag === 'input' ? open : `${open}${leading}${tree.children.map(markup).join('')}</${tree.tag}>`;
}
function element(tree, doc, slots) {
  if (typeof tree === 'string') return doc.createTextNode(tree);
  if (tree.slot) { const fragment = doc.createDocumentFragment(); for (const child of slots) fragment.append(child); return fragment; }
  const el = doc.createElement(tree.tag);
  for (const [key, value] of Object.entries(tree.attrs)) el.setAttribute(key, value);
  for (const child of tree.children) el.append(element(child, doc, slots));
  return el;
}
export const formMarkup = (kind, props = {}) => markup(formProps(kind, props));
export function createForm(kind, props = {}, children = [], doc = document) {
  if (!Array.isArray(children) || children.some(child => !(child instanceof doc.defaultView.Node))) throw new TypeError('Form slots accept existing DOM Nodes only');
  if (children.length && !['form_section', 'form_actions'].includes(kind)) throw new TypeError('This form component has no content slot');
  return element(formProps(kind, props), doc, children);
}
const htmlNames = { nativeSelect: 'select', switchControl: 'switch', formSection: 'form_section', formActions: 'form_actions', errorSummary: 'error_summary' };
export const html = Object.freeze({ ...Object.fromEntries(formKinds.map(kind => [kind, props => formMarkup(kind, props)])),
  ...Object.fromEntries(Object.entries(htmlNames).map(([name, kind]) => [name, props => formMarkup(kind, props)])) });
export const field = (p, doc) => createForm('field', p, [], doc);
export const input = (p, doc) => createForm('input', p, [], doc);
export const textarea = (p, doc) => createForm('textarea', p, [], doc);
export const nativeSelect = (p, doc) => createForm('select', p, [], doc);
export const checkbox = (p, doc) => createForm('checkbox', p, [], doc);
export const radio = (p, doc) => createForm('radio', p, [], doc);
export const range = (p, doc) => createForm('range', p, [], doc);
export const switchControl = (p, doc) => createForm('switch', p, [], doc);
export const formSection = (p, children = [], doc) => createForm('form_section', p, children, doc);
export const formActions = (p, children = [], doc) => createForm('form_actions', p, children, doc);
export const errorSummary = (p, doc) => createForm('error_summary', p, [], doc);

export function focusFirstError(summary) {
  const target = summary?.querySelector('[data-lq-error-target]');
  const control = target?.ownerDocument.getElementById(target.dataset.lqErrorTarget);
  if (!control || control.matches(':disabled')) return false;
  control.focus(); control.scrollIntoView({ block: 'nearest' }); return control.ownerDocument.activeElement === control;
}
const enhancementKey = Symbol.for('lanshare.lq.forms');
export function enhanceForms(root = document) {
  let owner = root[enhancementKey];
  if (owner) { owner.count++; return lease(owner); }
  const doc = root.ownerDocument || root, win = doc.defaultView;
  const composing = new WeakSet(), originals = new Map();
  const contains = el => root === doc ? doc.documentElement.contains(el) : root.contains(el);
  const find = identity => { const el = doc.getElementById(identity); return el && contains(el) ? el : null; };
  function update(el) {
    if (!el?.matches?.('.lq-field input,.lq-field textarea,.lq-field select')) return;
    const field = el.closest('.lq-field');
    const clear = field.querySelector('[data-lq-clear]');
    if (clear) { clear.hidden = !el.value; clear.disabled = el.matches(':disabled') || el.readOnly; }
    if (el.dataset.lqCount) {
      const counter = find(el.dataset.lqCount);
      if (counter) counter.textContent = `${el.value.length}${el.maxLength >= 0 ? ` / ${el.maxLength}` : ''} 字`;
    }
    if (el.dataset.lqRangeOutput) { const out = find(el.dataset.lqRangeOutput); if (out) out.value = el.value; }
    if (el.matches('textarea[data-lq-auto-grow="true"]') && el.getClientRects().length) {
      if (!originals.has(el)) originals.set(el, { height: el.style.height, overflowY: el.style.overflowY });
      el.style.height = 'auto';
      const css = win.getComputedStyle(el), extra = css.boxSizing === 'border-box' ? parseFloat(css.borderTopWidth) + parseFloat(css.borderBottomWidth) : -parseFloat(css.paddingTop) - parseFloat(css.paddingBottom);
      el.style.height = `${el.scrollHeight + extra}px`; el.style.overflowY = 'hidden';
    }
  }
  function refresh() { for (const el of root.querySelectorAll('.lq-field input,.lq-field textarea,.lq-field select')) update(el); }
  const input = event => update(event.target);
  const start = event => composing.add(event.target);
  const end = event => { composing.delete(event.target); update(event.target); };
  const click = event => {
    const clear = event.target.closest?.('[data-lq-clear]');
    if (clear && contains(clear)) {
      const control = find(clear.dataset.lqClear);
      if (!control || control.matches(':disabled') || control.readOnly || clear.matches(':disabled') || composing.has(control)) return;
      control.value = ''; control.dispatchEvent(new win.Event('input', { bubbles: true })); control.dispatchEvent(new win.Event('change', { bubbles: true })); control.focus(); return;
    }
    const link = event.target.closest?.('[data-lq-error-target]');
    if (link && contains(link)) {
      const control = find(link.dataset.lqErrorTarget);
      if (control && !control.matches(':disabled')) { event.preventDefault(); control.focus(); control.scrollIntoView({ block: 'nearest' }); }
    }
  };
  const reset = () => win.queueMicrotask(() => { if (owner.count) refresh(); });
  const listeners = [['input', input], ['change', input], ['focusin', input], ['compositionstart', start], ['compositionend', end], ['click', click], ['reset', reset]];
  for (const [type, fn] of listeners) root.addEventListener(type, fn);
  win.addEventListener('resize', refresh);
  const observer = new win.MutationObserver(records => {
    // Ignore unrelated streamed content. Detached textarea nodes must not stay
    // retained by a document-lifetime enhancement owner.
    if (records.some(record => record.removedNodes.length)) for (const [el, original] of originals) {
      if (!contains(el)) { el.style.height = original.height; el.style.overflowY = original.overflowY; originals.delete(el); }
    }
    const addedFields = records.some(record => [...record.addedNodes].some(node => node.nodeType === 1 && (node.matches('.lq-field') || node.querySelector('.lq-field'))));
    const fieldsetChanged = records.some(record => record.type === 'attributes' && record.target.matches('fieldset'));
    if (addedFields || fieldsetChanged) refresh();
    else for (const record of records) if (record.type === 'attributes') update(record.target);
  });
  observer.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'readonly', 'maxlength'] });
  owner = { count: 1, refresh, dispose() {
    observer.disconnect(); win.removeEventListener('resize', refresh);
    for (const [type, fn] of listeners) root.removeEventListener(type, fn);
    for (const [el, original] of originals) { el.style.height = original.height; el.style.overflowY = original.overflowY; }
    originals.clear(); delete root[enhancementKey];
  } };
  root[enhancementKey] = owner; refresh(); return lease(owner);
}
function lease(owner) { let disposed = false; return { refresh: () => { if (!disposed) owner.refresh(); }, dispose: () => { if (!disposed) { disposed = true; if (--owner.count === 0) owner.dispose(); } } }; }
