import { normalizeAttributes, safeUrl, text } from './html.js';

const tones = ['primary', 'success', 'warning', 'danger', 'info', 'neutral'];
const sizes = ['sm', 'md', 'lg'];
const option = (props, key, fallback) => props[key] === undefined ? fallback : props[key];
const choice = (value, values, name) => {
  if (!values.includes(value)) throw new TypeError(`Invalid LQ ${name}`);
  return value;
};
const flag = (value, name) => {
  if (typeof value !== 'boolean') throw new TypeError(`LQ ${name} must be a boolean`);
  return value;
};
const number = (value, name) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new TypeError(`LQ ${name} must be a finite number`);
  return value;
};
function accessibleName(label, attrs = {}) {
  const name = text(label).trim() || (attrs['aria-label'] || '').trim();
  if (!name) throw new TypeError('LQ component requires an accessible name');
  return name;
}
function nameAttributes(attrs, name) {
  delete attrs['aria-labelledby'];
  attrs['aria-label'] = name;
}
function badgeValue(value) {
  if (typeof value === 'boolean') throw new TypeError('LQ badge value cannot be a boolean');
  if (value == null || value === '' || value === 0 || value === '0') return null;
  if (typeof value === 'number' && number(value, 'badge value') < 0) throw new TypeError('LQ badge value cannot be negative');
  return text(value);
}

function button(p) {
  const label = text(p.label ?? '');
  const attrs = normalizeAttributes(p.attrs);
  const name = accessibleName(label, attrs);
  const variant = choice(option(p, 'variant', 'soft'), ['prominent', 'glass', 'soft', 'ghost', 'destructive', 'link'], 'button variant');
  const size = choice(option(p, 'size', 'md'), sizes, 'size');
  const icon = p.icon ?? null;
  if (icon !== null && (typeof icon !== 'string' || !/^[a-z][a-z0-9-]*$/.test(icon))) throw new TypeError('LQ icon must be a registered icon name');
  if (!label.trim() && !icon) throw new TypeError('An icon-only LQ button requires an icon');
  const href = safeUrl(p.href);
  const type = choice(option(p, 'type', 'button'), ['button', 'submit', 'reset'], 'button type');
  const disabled = flag(option(p, 'disabled', false), 'disabled');
  const ariaDisabled = flag(option(p, 'ariaDisabled', false), 'ariaDisabled');
  const loading = flag(option(p, 'loading', false), 'loading');
  let classes = `lq-btn lq-btn--${variant} lq-btn--${size}`;
  if (!label.trim()) classes += ' lq-btn--icon';
  if (loading) classes += ' is-loading';
  if (disabled || ariaDisabled) classes += ' is-disabled';
  for (const key of ['aria-busy', 'aria-disabled', 'data-lq-disabled']) delete attrs[key];
  nameAttributes(attrs, name);
  if (p.id != null) attrs.id = text(p.id);
  if (href === null) {
    delete attrs.target; delete attrs.rel; attrs.type = type;
    if (disabled) attrs.disabled = '';
  } else attrs.href = href;
  if (disabled || ariaDisabled || loading) Object.assign(attrs, { 'aria-disabled': 'true', 'data-lq-disabled': 'true' });
  if (loading) attrs['aria-busy'] = 'true';
  return { tag: href === null ? 'button' : 'a', classes, attrs, label, icon,
    badge: badgeValue(p.badge), loading, icon_only: !label.trim() };
}

function chip(p) {
  const label = text(p.label ?? ''); const attrs = normalizeAttributes(p.attrs);
  const name = accessibleName(label, attrs);
  const kind = choice(option(p, 'kind', 'status'), ['filter', 'status', 'tag'], 'chip kind');
  const size = choice(option(p, 'size', 'md'), ['sm', 'md'], 'chip size');
  const tone = choice(option(p, 'tone', 'neutral'), tones, 'tone');
  const pressed = flag(option(p, 'pressed', false), 'pressed');
  const disabled = flag(option(p, 'disabled', false), 'disabled');
  const removable = flag(option(p, 'removable', false), 'removable');
  if (removable && kind !== 'tag') throw new TypeError('Only a tag chip can be removable');
  for (const key of ['aria-pressed', 'aria-current', 'aria-live', 'aria-disabled', 'aria-labelledby',
    'data-lq-disabled', 'href', 'rel', 'target']) delete attrs[key];
  // A filter can be a real link when the page drives filtering through the URL.
  // A link is not a toggle, so it carries aria-current instead of aria-pressed,
  // and a disabled one has to stop being a link.
  const href = safeUrl(p.href);
  if (href !== null && kind !== 'filter') throw new TypeError('Only a filter chip can be a link');
  attrs['data-tone'] = tone;
  if (p.id != null) attrs.id = text(p.id);
  let classes = `lq-chip lq-chip--${kind} lq-chip--${size}`;
  if (kind === 'filter') {
    nameAttributes(attrs, name);
    if (href !== null && !disabled) Object.assign(attrs, { href, 'aria-current': text(pressed) });
    else Object.assign(attrs, { type: 'button', 'aria-pressed': text(pressed) });
    if (pressed) classes += ' is-selected';
    if (disabled) Object.assign(attrs, { disabled: '', 'aria-disabled': 'true', 'data-lq-disabled': 'true' });
  } else delete attrs['aria-label'];
  if (disabled) classes += ' is-disabled';
  return { tag: kind === 'filter' ? ('href' in attrs ? 'a' : 'button') : 'span', classes, attrs, kind, label,
    removable, remove_label: text(p.removeLabel).trim() || `移除${name}`, disabled };
}
function badge(p) {
  const attrs = normalizeAttributes(p.attrs); const dot = flag(option(p, 'dot', false), 'dot');
  const value = badgeValue(p.value);
  const visible = value !== null || (dot && p.value == null);
  delete attrs['aria-labelledby'];
  attrs['data-tone'] = choice(option(p, 'tone', 'neutral'), tones, 'tone');
  if (dot && visible) { attrs.role = 'img'; nameAttributes(attrs, accessibleName(p.label, attrs)); }
  else if (p.label != null) nameAttributes(attrs, text(p.label));
  return { classes: `lq-badge${dot ? ' lq-badge--dot' : ''}`, attrs, value, dot, visible };
}
function avatar(p) {
  const attrs = normalizeAttributes(p.attrs); const name = accessibleName(p.name);
  const size = choice(option(p, 'size', 40), [24, 32, 40, 56], 'avatar size');
  let hash = 0;
  for (const char of name) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  const bucket = hash % tones.length;
  Object.assign(attrs, { role: 'img', 'data-tone': tones[bucket], 'data-avatar-bucket': text(bucket) });
  nameAttributes(attrs, name);
  return { classes: `lq-avatar lq-avatar--${size}`, attrs, name, initial: [...name][0].toUpperCase(), src: safeUrl(p.src, true) };
}
function spinner(p) {
  const attrs = normalizeAttributes(p.attrs);
  delete attrs['aria-label']; delete attrs['aria-labelledby']; attrs['aria-hidden'] = 'true';
  return { classes: `lq-spinner lq-spinner--${choice(option(p, 'size', 'md'), sizes, 'spinner size')}`, attrs };
}
function progress(p) {
  const attrs = normalizeAttributes(p.attrs); const name = accessibleName(p.label);
  const variant = choice(option(p, 'variant', 'bar'), ['bar', 'ring'], 'progress variant');
  const max = number(option(p, 'max', 100), 'progress max');
  if (max <= 0) throw new TypeError('LQ progress max must be positive');
  const value = p.value ?? null;
  if (value !== null && (number(value, 'progress value') < 0 || value > max)) throw new TypeError('LQ progress value is out of range');
  for (const key of ['aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext']) delete attrs[key];
  Object.assign(attrs, { 'data-tone': 'primary' }); nameAttributes(attrs, name);
  if (variant === 'ring') {
    Object.assign(attrs, { role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': text(max) });
    if (value !== null) attrs['aria-valuenow'] = text(value);
  } else {
    attrs.max = text(max);
    if (value !== null) attrs.value = text(value);
  }
  return { tag: variant === 'ring' ? 'span' : 'progress', classes: `lq-progress${variant === 'ring' ? ' lq-progress--ring' : ''}`, attrs,
    variant, percent: value === null ? null : Math.round(value / max * 100) };
}
function skeleton(p) {
  const attrs = normalizeAttributes(p.attrs);
  for (const key of ['aria-label', 'aria-labelledby', 'aria-describedby', 'aria-busy']) delete attrs[key];
  attrs['aria-hidden'] = 'true';
  const shape = choice(option(p, 'shape', 'text'), ['text', 'avatar', 'block'], 'skeleton shape');
  const lines = option(p, 'lines', 1);
  if (!Number.isInteger(lines) || lines < 1 || lines > 8 || (shape !== 'text' && lines !== 1)) throw new TypeError('Invalid LQ skeleton lines');
  return { classes: `lq-skeleton lq-skeleton--${shape}`, attrs, lines };
}
const normalizers = { button, chip, badge, avatar, spinner, progress, skeleton };
export function componentProps(kind, props = {}) {
  if (!Object.hasOwn(normalizers, kind)) throw new TypeError('Unknown LQ component');
  return normalizers[kind](props);
}
