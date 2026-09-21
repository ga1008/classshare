// Segmented filter chips that proxy an existing <select>. Lets manage pages
// offer "selection over typing" filters while reusing their current filter
// logic untouched: clicking a chip sets the target select's value and fires a
// native change event, so existing onchange handlers run as before.

const managerKey = Symbol.for('lanshare.manage-filter-chips');
const groupKey = Symbol.for('lanshare.manage-filter-chips.group');
// Matches both the legacy hand-rolled chip markup and the frozen LQ
// lq_chip(kind='filter') markup, so a group can migrate its chip markup to
// LQ without losing this proxy (both always render a real <button>).
const CHIP_SELECTOR = 'button.filter-chip[data-value], button.lq-chip--filter[data-value]';
function resolveTarget(group) {
  try { const target = group.ownerDocument.querySelector(group.getAttribute('data-filter-target')); return target?.matches('select') ? target : null; } catch { return null; }
}

function createGroup(group) {
  const doc = group.ownerDocument;
  const target = resolveTarget(group);
  if (!target) return null;
  const originals = new Map();
  const role = group.getAttribute('role');
  if (role === null) group.setAttribute('role', 'group');
  let chips = [], more = null, expanded = false, destroyed = false;
  const option = value => [...target.options].find(item => item.value === value);
  const blocked = chip => {
    const item = option(chip.getAttribute('data-value') ?? '');
    return chip.disabled || target.matches(':disabled') || !item || item.disabled || item.parentElement?.matches('optgroup:disabled');
  };
  function sync() {
    const value = String(target.value ?? '');
    for (const chip of chips) {
      const selected = chip.getAttribute('data-value') === value;
      chip.classList.toggle('is-active', selected); chip.setAttribute('aria-pressed', String(selected));
      if (blocked(chip)) chip.setAttribute('aria-disabled', 'true'); else chip.removeAttribute('aria-disabled');
    }
    if (more) {
      const extras = chips.slice(8);
      for (const chip of extras) {
        const hide = originals.get(chip).hidden || (!expanded && chip.getAttribute('data-value') !== value);
        if (hide && chip.contains(doc.activeElement)) more.focus();
        chip.hidden = hide;
      }
      const hidden = extras.filter(chip => chip.hidden && !originals.get(chip).hidden).length;
      more.textContent = expanded ? '收起' : `更多 (${hidden})`;
      more.setAttribute('aria-expanded', String(expanded));
      more.setAttribute('aria-controls', extras.map(chip => chip.id).join(' '));
      more.hidden = !extras.length;
    }
  }
  function refresh() {
    if (destroyed) return;
    chips = [...group.querySelectorAll(CHIP_SELECTOR)];
    for (const chip of chips) if (!originals.has(chip)) {
      originals.set(chip, { pressed: chip.getAttribute('aria-pressed'), disabled: chip.getAttribute('aria-disabled'), type: chip.getAttribute('type'), id: chip.getAttribute('id'), hidden: chip.hidden });
      chip.type = 'button';
    }
    // The two existing consumers have 4/6 chips and receive no layout mutation.
    // More/scroll is explicitly opt-in; a stable id is required for controls.
    if (group.dataset.filterOverflow === 'more' && group.id && chips.length > 8) {
      if (!more) { more = doc.createElement('button'); more.type = 'button'; more.className = 'lq-filter-more'; more.dataset.filterMore = ''; chips[7].after(more); }
      chips.slice(8).forEach((chip, index) => { if (!chip.id) chip.id = `${group.id}--lq-extra-${index}`; });
    }
    sync();
  }
  const click = event => {
    if (event.target.closest?.('[data-filter-more]') === more && more) { expanded = !expanded; sync(); return; }
    const chip = event.target.closest?.(CHIP_SELECTOR);
    if (!chips.includes(chip) || blocked(chip)) return;
    const value = chip.getAttribute('data-value') ?? '';
    if (String(target.value ?? '') === value) return;
    target.value = value;
    target.dispatchEvent(new doc.defaultView.Event('change', { bubbles: true }));
    sync();
  };
  const focus = event => { if (group.dataset.filterOverflow === 'scroll' && chips.includes(event.target)) event.target.scrollIntoView({ block: 'nearest', inline: 'nearest' }); };
  const reset = () => doc.defaultView.queueMicrotask(() => { if (!destroyed) sync(); });
  group.addEventListener('click', click); group.addEventListener('focusin', focus); target.addEventListener('change', sync); target.form?.addEventListener('reset', reset);
  refresh();
  return { refresh, target, destroy() {
    if (destroyed) return; destroyed = true;
    group.removeEventListener('click', click); group.removeEventListener('focusin', focus); target.removeEventListener('change', sync); target.form?.removeEventListener('reset', reset);
    for (const [chip, original] of originals) {
      for (const [key, value] of [['aria-pressed', original.pressed], ['aria-disabled', original.disabled], ['type', original.type], ['id', original.id]]) if (value === null) chip.removeAttribute(key); else chip.setAttribute(key, value);
      chip.hidden = original.hidden;
    }
    if (role === null) group.removeAttribute('role'); else group.setAttribute('role', role);
    more?.remove(); originals.clear();
  } };
}

function bindGroup(group) {
  let owner = group[groupKey];
  if (owner && owner.api.target !== resolveTarget(group)) { owner.api.destroy(); delete group[groupKey]; owner = null; }
  if (!owner) { const api = createGroup(group); if (!api) return null; owner = { api, count: 0 }; group[groupKey] = owner; }
  owner.count++;
  let released = false;
  return { target: owner.api.target, refresh: () => owner.api.refresh(), destroy() {
    if (released) return; released = true;
    if (--owner.count === 0) { owner.api.destroy(); if (group[groupKey] === owner) delete group[groupKey]; }
  } };
}

export function initFilterChips(root = document) {
  if (root[managerKey]) { root[managerKey].refresh(); return root[managerKey]; }
  const groups = new Map(); let destroyed = false;
  const manager = { refresh() {
    if (destroyed) return;
    const current = [...root.querySelectorAll('[data-filter-chips]')];
    if (root.matches?.('[data-filter-chips]')) current.unshift(root);
    for (const [group, owner] of groups) if (!current.includes(group) || owner.target !== resolveTarget(group)) { owner.destroy(); groups.delete(group); }
    for (const group of current) {
      if (!groups.has(group)) { const owner = bindGroup(group); if (owner) groups.set(group, owner); }
      else groups.get(group).refresh();
    }
  }, destroy() { if (destroyed) return; destroyed = true; for (const owner of groups.values()) owner.destroy(); groups.clear(); delete root[managerKey]; } };
  root[managerKey] = manager; manager.refresh(); return manager;
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => initFilterChips(), { once: true });
} else {
  initFilterChips();
}
