/** LQ presentation inputs. No arbitrary markup, event strings or style bypasses. */
export const text = value => value == null ? '' : String(value);
export const escapeHtml = value => text(value).replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);

const extraAttributes = new Set(['id', 'name', 'title', 'form', 'target', 'rel']);
export function normalizeAttributes(value = {}) {
  if (value == null) return {};
  if (typeof value !== 'object' || Array.isArray(value)) throw new TypeError('LQ attrs must be a mapping');
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (!extraAttributes.has(key) && !/^(?:aria|data)-[a-z][a-z0-9_.:-]*$/.test(key)) {
      throw new TypeError('Unsupported LQ attribute');
    }
    if (item == null) continue;
    if (!['string', 'number', 'boolean'].includes(typeof item)) throw new TypeError('LQ attribute values must be scalar');
    if (typeof item === 'number' && !Number.isFinite(item)) throw new TypeError('LQ attribute values must be finite');
    result[key] = text(item);
  }
  delete result['aria-hidden'];
  delete result['aria-live'];
  if (result.target === '_blank') {
    const rel = new Set((result.rel || '').toLowerCase().split(/\s+/).filter(Boolean));
    rel.delete('opener'); rel.add('noopener'); rel.add('noreferrer');
    result.rel = [...rel].sort().join(' ');
  }
  return result;
}

export function safeUrl(value, image = false) {
  if (value == null) return null;
  if (typeof value !== 'string' || /[\x00-\x1f\x7f]/.test(value)) throw new TypeError('Invalid LQ URL');
  value = value.trim();
  if (!value || /[\x00-\x20\x7f\\]/.test(value) || value.startsWith('//')) throw new TypeError('Invalid LQ URL');
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(value)?.[1].toLowerCase() || '';
  if (!(image ? ['', 'http', 'https'] : ['', 'http', 'https', 'mailto', 'tel']).includes(scheme)) {
    throw new TypeError('Unsupported LQ URL scheme');
  }
  return value;
}

export function attributesMarkup(attributes) {
  return Object.entries(attributes).map(([key, value]) => ` ${escapeHtml(key)}="${escapeHtml(value)}"`).join('');
}
