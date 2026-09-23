import { attributesMarkup, escapeHtml, safeUrl } from './html.js';
import { createComponent, componentMarkup } from './components.js';
import { createIcon, iconMarkup } from './icons.js';
import { getLayerSystem } from './layer.js';

export const shellKinds = Object.freeze(['topbar', 'nav_item', 'sidebar', 'dock', 'fab', 'crumbs', 'steps', 'editor', 'page_layout']);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (v, required = false) => { if (typeof v !== 'string' || required && !v.trim()) throw new TypeError('Shell text must be plain text'); return v; };
const key = v => { v = text(v, true); if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(v) || v.includes('--lq-')) throw new TypeError('A stable non-reserved shell id/key is required'); return v; };
const flag = (p, key, fallback = false) => { const v = p[key] === undefined ? fallback : p[key]; if (typeof v !== 'boolean') throw new TypeError('Shell flags must be boolean'); return v; };
const choice = (v, values) => { if (!values.includes(v)) throw new TypeError('Invalid shell variant'); return v; };
const node = (tag, attrs = {}, children = []) => ({ tag, attrs, children });
const slot = name => node('div', { class: 'lq-shell-slot', 'data-lq-slot': name }, [{ slot: name }]);
function attrs(value) {
  if (value == null) return {};
  if (!object(value)) throw new TypeError('Shell attrs must be a mapping');
  const result = {};
  for (const [key, item] of Object.entries(value)) {
    if (!/^(aria|data)-[a-z][a-z0-9_.:-]*$/.test(key) && key !== 'title') throw new TypeError('Unsupported shell attribute');
    if (item == null) continue;
    if (!['string', 'boolean'].includes(typeof item)) throw new TypeError('Shell attrs must be text or boolean');
    if (key.startsWith('data-lq-') || key.startsWith('aria-') && key !== 'aria-describedby') continue;
    result[key] = String(item);
  }
  return result;
}
function items(value, maximum = Infinity) {
  if (!Array.isArray(value) || value.length > maximum) throw new TypeError('Shell items must be a bounded list');
  const seen = new Set(), result = value.map(item => {
    if (!object(item) || Object.keys(item).some(k => !['key', 'label', 'href', 'icon', 'current', 'disabled', 'state'].includes(k))) throw new TypeError('Invalid shell item');
    const id = key(item.key); if (seen.has(id)) throw new TypeError('Duplicate shell item'); seen.add(id);
    return { key: id, label: text(item.label, true), href: safeUrl(item.href), icon: item.icon == null ? null : text(item.icon, true), current: flag(item, 'current'), disabled: flag(item, 'disabled'), state: choice(item.state === undefined ? 'upcoming' : item.state, ['complete', 'current', 'upcoming']) };
  });
  if (result.filter(item => item.current).length > 1) throw new TypeError('Only one current navigation item is allowed');
  return result;
}
function itemTree(item, className = 'lq-nav-item', command = false) {
  const a = { class: className, 'data-lq-key': item.key }; let tag;
  if (item.current) a['aria-current'] = 'page';
  if (item.disabled) a['aria-disabled'] = 'true';
  if (item.href && !item.disabled) { tag = 'a'; a.href = item.href; }
  else if (command) { tag = 'button'; a.type = 'button'; a['data-lq-command'] = item.key; if (item.disabled) a.disabled = ''; }
  else tag = 'span';
  return node(tag, a, [...(item.icon ? [{ icon: item.icon }] : []), node('span', { class: 'lq-nav-item__label' }, [item.label])]);
}
const action = (item, prominent = false) => ({ button: { label: item.label, icon: item.icon, href: item.href, variant: prominent ? 'prominent' : 'glass', size: 'sm', disabled: item.disabled, attrs: { 'data-lq-command': item.key } } });
const pane = (id, key, label, name) => node('div', { id: `${id}--lq-${key}`, class: 'lq-shell-pane', 'data-lq-pane': key, 'data-lq-pane-label': label }, [node('div', { class: 'lq-shell-pane__scrim', 'data-lq-pane-close': '', 'aria-hidden': 'true' }), node('section', { class: 'lq-shell-pane__surface lq-surface', 'aria-label': label }, [node('header', { class: 'lq-shell-pane__head' }, [node('strong', {}, [label]), node('button', { type: 'button', class: 'lq-shell-pane__close lq-btn lq-btn--glass lq-btn--icon', 'data-lq-pane-close': '', 'aria-label': `关闭${label}` }, [{ icon: 'x' }])]), slot(name)])]);
const trigger = (id, key, label) => node('button', { type: 'button', class: 'lq-shell-pane__trigger lq-btn lq-btn--glass', 'data-lq-pane-open': key, 'aria-controls': `${id}--lq-${key}`, 'aria-expanded': 'false' }, [label]);

export function shellProps(kind, p = {}) {
  if (!shellKinds.includes(kind) || !object(p)) throw new TypeError('Unknown shell component');
  const allowed = { topbar: ['title', 'variant', 'back', 'lockNav', 'actions', 'primary', 'viewTransition'], nav_item: ['item'], sidebar: ['label', 'groups', 'persist'], dock: ['label', 'mode', 'items', 'overflow'], fab: ['item', 'size', 'stackIndex', 'variant'], crumbs: ['label', 'items'], steps: ['label', 'items'], editor: ['title', 'kind', 'railLabel', 'asideLabel', 'primary'], page_layout: ['kind', 'label'] }[kind].concat(['id', 'attrs']);
  if (Object.keys(p).some(k => !allowed.includes(k))) throw new TypeError('Unknown shell property or raw HTML');
  const id = key(p.id), a = { ...attrs(p.attrs), id, class: `lq-${kind.replaceAll('_', '-')}`, 'data-lq-shell': kind };
  if (kind === 'nav_item') { const result = itemTree(items([p.item])[0]); Object.assign(result.attrs, a); return result; }
  if (kind === 'fab') { const item = items([p.item])[0]; if (!item.icon) throw new TypeError('FAB requires a named icon'); const variant = choice(p.variant === undefined ? 'glass' : p.variant, ['glass', 'prominent']); a.class += `${variant === 'glass' ? ' lq-glass' : ' lq-fab--prominent'} lq-fab--${choice(p.size === undefined ? 'md' : p.size, ['sm', 'md'])}`; const index = p.stackIndex === undefined ? 0 : p.stackIndex; if (!Number.isInteger(index) || index < 0 || index > 2) throw new TypeError('FAB stack index must be 0, 1 or 2'); a['data-lq-fab-slot'] = String(index); const result = itemTree(item, undefined, true); Object.assign(result.attrs, a, { 'aria-label': item.label }); return result; }
  if (['crumbs', 'steps'].includes(kind)) {
    const list = items(p.items === undefined ? [] : p.items);
    if (!list.length || kind === 'steps' && list.filter(item => item.state === 'current').length !== 1) throw new TypeError('Navigation needs items and Steps needs one current step');
    a['aria-label'] = text(p.label === undefined ? kind === 'crumbs' ? '当前位置' : '步骤' : p.label, true);
    return node('nav', a, [node('ol', {}, list.map((item, i) => {
      const child = itemTree(item);
      if (kind === 'crumbs' && i === list.length - 1) child.attrs['aria-current'] = 'page';
      if (kind === 'steps') { delete child.attrs['aria-current']; if (item.state === 'current') child.attrs['aria-current'] = 'step'; child.children = [node('span', { class: 'lq-steps__node', 'aria-hidden': 'true' }, item.icon ? [{ icon: item.icon }] : [String(i + 1)]), node('span', { class: 'lq-nav-item__label' }, [item.label])]; }
      return node('li', kind === 'steps' ? { 'data-lq-step': item.state } : { 'data-lq-crumb': i === list.length - 2 ? 'parent' : i === list.length - 1 ? 'current' : 'ancestor' }, [child, ...(kind === 'crumbs' && i < list.length - 1 ? [node('span', { class: 'lq-crumbs__separator', 'aria-hidden': 'true' }, [{ icon: 'chevron-right' }])] : [])]);
    }))]);
  }
  if (kind === 'dock') {
    const mode = choice(p.mode === undefined ? 'navigation' : p.mode, ['navigation', 'actions', 'tabs']), overflow = items(p.overflow === undefined ? [] : p.overflow, 50), list = items(p.items === undefined ? [] : p.items, overflow.length ? 4 : 5);
    if (list.some(item => overflow.some(other => item.key === other.key))) throw new TypeError('Dock item keys must be distinct across overflow');
    if (mode === 'tabs' && (list.length || overflow.length)) throw new TypeError('Tab Dock takes an existing Tabs slot');
    if (mode === 'navigation' && [...list, ...overflow].some(item => !item.href)) throw new TypeError('Navigation Dock items need explicit hrefs');
    Object.assign(a, { class: 'lq-dock lq-glass', 'data-lq-dock-mode': mode, 'aria-label': text(p.label === undefined ? mode === 'actions' ? '页内操作' : '导航' : p.label, true) });
    if (mode === 'actions') a.role = 'group';
    const more = overflow.length ? [node('details', { class: 'lq-dock__more', 'data-lq-dock-more': '' }, [node('summary', { class: 'lq-dock__item' }, ['更多']), node('div', { class: 'lq-dock__overflow lq-surface', 'data-lq-dock-overflow': '' }, overflow.map(item => itemTree(item, undefined, mode === 'actions')))])] : [];
    return node(mode === 'navigation' ? 'nav' : 'div', a, [...list.map(item => itemTree(item, 'lq-dock__item', mode === 'actions')), ...more, slot(mode === 'tabs' ? 'tabs' : 'more')]);
  }
  if (kind === 'topbar') {
    const variant = choice(p.variant === undefined ? 'standard' : p.variant, ['standard', 'immersive']), list = items(p.actions === undefined ? [] : p.actions, 3);
    Object.assign(a, { class: `lq-topbar lq-topbar--${variant} lq-glass lq-scroll-edge`, 'data-lq-lock-nav': String(flag(p, 'lockNav')), 'data-lq-view-transition': String(flag(p, 'viewTransition')) });
    const lead = [slot('lead')]; if (p.back != null) { const back = items([p.back])[0]; if (!back.href) throw new TypeError('Back navigation needs an href'); lead.unshift(itemTree(back)); }
    const panel = pane(id, 'actions', '更多操作', 'more'); panel.tag = 'dialog'; Object.assign(panel.attrs, { class: 'lq-shell-pane lq-topbar__overflow', open: '', 'aria-label': '更多操作' });
    // Inline actions share the topbar's glass. Only an open drawer owns a surface.
    panel.children[1].attrs.class = 'lq-shell-pane__surface';
    panel.children[1].children[1] = node('div', { class: 'lq-topbar__actions' }, [...list.map(item => action(item)), ...(p.primary == null ? [] : [action(items([p.primary])[0], true)]), slot('more')]);
    return node('header', a, [node('div', { class: 'lq-topbar__lead' }, lead), node('div', { class: 'lq-topbar__title' }, [node('h1', {}, [text(p.title, true)]), slot('status')]), trigger(id, 'actions', '更多'), panel]);
  }
  if (kind === 'sidebar') {
    a.class += ' lq-sidebar-root'; const label = text(p.label === undefined ? '工作台导航' : p.label, true), groups = p.groups === undefined ? [] : p.groups;
    if (!Array.isArray(groups) || !groups.length) throw new TypeError('Sidebar needs explicit navigation groups');
    const seen = new Set(), keys = new Set(), details = groups.map(group => {
      if (!object(group) || Object.keys(group).some(k => !['key', 'label', 'items', 'open'].includes(k))) throw new TypeError('Invalid sidebar group');
      const id = key(group.key), list = items(group.items === undefined ? [] : group.items);
      if (seen.has(id) || list.some(item => keys.has(item.key) || !item.href)) throw new TypeError('Sidebar needs unique items with explicit hrefs'); seen.add(id); list.forEach(item => keys.add(item.key));
      const ga = { class: 'lq-sidebar__group', 'data-lq-nav-group': id }; if (flag(group, 'open') || list.some(item => item.current)) ga.open = '';
      return node('details', ga, [node('summary', {}, [text(group.label, true)]), node('ul', {}, list.map(item => node('li', { 'data-lq-nav-search': item.label }, [itemTree(item)])))]);
    });
    if (details.filter(group => Object.hasOwn(group.attrs, 'open')).length > 1) throw new TypeError('Only one sidebar group starts open');
    if (p.persist != null) {
      if (!object(p.persist) || Object.keys(p.persist).sort().join() !== 'identity,key,resource') throw new TypeError('Scoped persistence requires identity/resource/key');
      const values = ['identity', 'resource', 'key'].map(k => text(p.persist[k], true));
      if (values.some(v => [...v].length > 256 || /[\x00-\x1f\x7f]/.test(v))) throw new TypeError('Invalid scoped persistence key'); a['data-lq-persist'] = JSON.stringify(values);
    }
    const panel = pane(id, 'nav', label, 'user'), surface = panel.children[1]; surface.attrs.class += ' lq-sidebar__surface';
    surface.children.splice(1, 0, slot('brand'), node('label', { class: 'lq-sidebar__search' }, [node('span', {}, ['搜索菜单']), node('input', { type: 'search', class: 'lq-input', 'data-lq-nav-search-input': '', 'aria-label': '搜索菜单', autocomplete: 'off' })]), node('nav', { 'aria-label': label }, details), node('p', { 'data-lq-nav-empty': '', hidden: '' }, ['没有匹配的菜单']));
    return node('div', a, [trigger(id, 'nav', label), panel]);
  }
  if (kind === 'editor') {
    const variant = choice(p.kind === undefined ? 'exam' : p.kind, ['exam', 'take', 'lesson-plan', 'assessment', 'evaluation']), rail = text(p.railLabel === undefined ? '目录' : p.railLabel, true), aside = text(p.asideLabel === undefined ? '属性与预览' : p.asideLabel, true);
    Object.assign(a, { class: 'lq-editor', 'data-lq-editor': variant }); const controls = [trigger(id, 'rail', rail), trigger(id, 'aside', aside)], primary = p.primary == null ? [] : [action(items([p.primary])[0], true)];
    return node('section', a, [node('header', { class: 'lq-editor__bar lq-glass' }, [slot('lead'), node('h1', {}, [text(p.title, true)]), slot('status'), node('div', { class: 'lq-editor__controls' }, [...controls, ...primary, slot('actions')])]), node('div', { class: 'lq-editor__workspace' }, [pane(id, 'rail', rail, 'rail'), node('main', { id: `${id}--lq-main`, class: 'lq-editor__main lq-surface', tabindex: '-1' }, [slot('main')]), pane(id, 'aside', aside, 'aside')]), node('footer', { class: 'lq-editor__mobile lq-surface' }, [...controls, ...primary])]);
  }
  const variant = choice(p.kind, ['list', 'dashboard', 'detail', 'editor', 'take', 'immersive', 'reading']);
  Object.assign(a, { class: `lq-page-layout lq-page-layout--${variant}`, 'data-lq-layout': variant, 'aria-label': text(p.label === undefined ? '页面内容' : p.label, true) });
  return node('section', a, [slot('head'), slot('filter'), node('div', { class: 'lq-page-layout__body' }, [node('div', { class: 'lq-page-layout__main' }, [slot('main')]), node('aside', { class: 'lq-page-layout__aside' }, [slot('aside')])]), slot('footer')]);
}

function markup(tree) {
  if (typeof tree === 'string') return escapeHtml(tree);
  if (tree.slot) return '';
  if (tree.icon) return iconMarkup(tree.icon);
  if (tree.button) return componentMarkup('button', tree.button);
  const start = `<${tree.tag}${attributesMarkup(tree.attrs)}>`;
  return tree.tag === 'input' ? start : `${start}${tree.children.map(markup).join('')}</${tree.tag}>`;
}
export const shellMarkup = (kind, props = {}) => markup(shellProps(kind, props));
export const html = Object.freeze(Object.fromEntries(shellKinds.map(kind => [kind, props => shellMarkup(kind, props)])));

export function createShell(kind, props, slots = {}, doc = document) {
  const tree = shellProps(kind, props), allowed = new Set(), seen = new Set();
  const collect = tree => { if (tree.slot) allowed.add(tree.slot); for (const child of tree.children || []) if (typeof child !== 'string') collect(child); }; collect(tree);
  if (!object(slots)) throw new TypeError('Shell slots must map names to Node arrays');
  for (const [name, nodes] of Object.entries(slots)) {
    if (!allowed.has(name) || !Array.isArray(nodes)) throw new TypeError('Unknown shell slot');
    for (const n of nodes) {
      if (!(n instanceof doc.defaultView.Node) || n.ownerDocument !== doc || ![1, 3, 8, 11].includes(n.nodeType) || seen.has(n) || n === doc.body || n === doc.documentElement) throw new TypeError('Shell slots accept distinct existing Nodes only');
      seen.add(n);
    }
  }
  for (const a of seen) for (const b of seen) if (a !== b && a.contains(b)) throw new TypeError('Shell slots cannot overlap');
  // Moving an existing live frame is not a state-preserving mount operation.
  for (const n of seen) {
    const elements = n.nodeType === 1 ? [n, ...n.querySelectorAll('*')] : [...n.querySelectorAll?.('*') || []];
    for (const el of elements) {
      if (el.isConnected && el.matches('iframe,object,embed')) throw new TypeError('Live document slots must be authored in place');
      if (el.form && ![...seen].some(root => root.contains(el.form)) && el.getAttribute('form') !== el.form.id) throw new TypeError('A moved form control needs its complete form or explicit owner');
      const fieldset = el.closest('fieldset:disabled');
      if (fieldset && ![...seen].some(root => root.contains(fieldset))) throw new TypeError('Cannot detach disabled fieldset semantics');
    }
  }
  const element = tree => {
    if (typeof tree === 'string') return doc.createTextNode(tree);
    if (tree.slot) { const result = doc.createDocumentFragment(); result.append(...(slots[tree.slot] || [])); return result; }
    if (tree.icon) return createIcon(tree.icon, doc);
    if (tree.button) return createComponent('button', tree.button, doc);
    const result = doc.createElement(tree.tag); for (const [key, value] of Object.entries(tree.attrs)) result.setAttribute(key, value); for (const child of tree.children) result.append(element(child)); return result;
  };
  return element(tree);
}

const shellOwner = Symbol.for('lanshare.lq.shell-owner');
const visible = el => Boolean(el?.isConnected && el.getClientRects().length && !el.closest('[hidden],[inert]') && getComputedStyle(el).visibility !== 'hidden');
function safeHost(pane, doc, ownsModal = false) {
  const win = doc.defaultView;
  // A native modal in the top layer cannot be overtaken by document z-index.
  if ([...doc.querySelectorAll('dialog[open]')].some(el => el.matches(':modal') && !el.contains(pane))) throw new TypeError('Pane is outside the active native modal');
  if (doc.fullscreenElement && !doc.fullscreenElement.contains(pane)) throw new TypeError('Pane is outside the fullscreen host');
  if ([...doc.querySelectorAll('[popover]')].some(el => el.matches(':popover-open') && !el.contains(pane))) throw new TypeError('Pane is outside an active top-layer popover');
  // A native dialog stays in its authored form but paints in the top layer.
  if (pane.tagName === 'DIALOG' && typeof pane.showModal === 'function') return;
  for (let el = pane.parentElement; el; el = el.parentElement) {
    const s = win.getComputedStyle(el);
    const ownedScrollLock = ownsModal && el === doc.body;
    if (s.transform !== 'none' || s.perspective !== 'none' || s.filter !== 'none' || s.backdropFilter && s.backdropFilter !== 'none' || s.contain !== 'none' || s.contentVisibility !== 'visible' || s.willChange !== 'auto' || s.isolation === 'isolate' || Number(s.opacity) < 1 || s.mixBlendMode !== 'normal' || s.zIndex !== 'auto' || !ownedScrollLock && (['hidden', 'clip', 'scroll', 'auto'].includes(s.overflowX) || ['hidden', 'clip', 'scroll', 'auto'].includes(s.overflowY))) throw new TypeError('Pane ancestor creates an unsupported containing, clipping or stacking context');
  }
}

/** Original connected panes stay inside their form and never enter a portal. */
export function enhanceShell(root, { paneGuards = {}, onError } = {}) {
  if (!root?.matches?.('[data-lq-shell="editor"],[data-lq-shell="sidebar"],[data-lq-shell="topbar"]') || !root.isConnected) throw new TypeError('Expected a connected shell root');
  if (root[shellOwner]) return root[shellOwner];
  const doc = root.ownerDocument, win = doc.defaultView, mode = root.dataset.lqShell;
  if (mode === 'topbar' && root.dataset.lqViewTransition === 'true' && [...doc.querySelectorAll('[data-lq-shell="topbar"][data-lq-view-transition="true"][data-lq-enhanced]')].some(el => el !== root)) throw new TypeError('Only one enhanced topbar may own the view transition name');
  const panels = [...root.querySelectorAll('[data-lq-pane]')].filter(el => el.closest('[data-lq-shell]') === root), layer = getLayerSystem(doc);
  for (const panel of panels) if (mode !== 'topbar' || typeof panel.showModal === 'function') safeHost(panel, doc);
  const surfaceClasses = new Map(panels.map(panel => { const surface = panel.querySelector(':scope > .lq-shell-pane__surface'); return [surface, surface.className]; }));
  const triggers = [...root.querySelectorAll('[data-lq-pane-open]')], saved = new Map(), releases = [];
  const save = (el, name) => { let values = saved.get(el); if (!values) saved.set(el, values = new Map()); if (!values.has(name)) values.set(name, el.getAttribute(name)); };
  const set = (el, name, value) => {
    save(el, name);
    const next = value == null ? null : String(value);
    if (el.getAttribute(name) === next) return;
    if (next === null) el.removeAttribute(name); else el.setAttribute(name, next);
  };
  const listen = (el, name, fn, options) => { el.addEventListener(name, fn, options); releases.push(() => el.removeEventListener(name, fn, options)); };
  let disposed = false, active = null, guards = {}, generation = 0;
  const narrow = (key, width = win.innerWidth) => width < (key === 'aside' ? 1280 : 1024) && (mode !== 'topbar' || typeof panels[0]?.showModal === 'function');
  const hasActions = () => mode !== 'topbar' || [...root.querySelector('.lq-topbar__actions').childNodes].some(el => el.nodeType === 1 ? !el.matches('.lq-shell-slot') || [...el.childNodes].some(child => child.nodeType === 1 || child.nodeType === 3 && child.textContent.trim()) : el.nodeType === 3 && el.textContent.trim());
  const keep = key => guards[key] && Object.values(guards[key]).some(Boolean);
  function validateGuards(value) {
    if (!object(value)) throw new TypeError('Pane guards must be explicit mappings');
    for (const [key, states] of Object.entries(value)) if (!panels.some(p => p.dataset.lqPane === key) || !object(states) || Object.entries(states).some(([name, v]) => !['dirty', 'hasError', 'current', 'keepOpen'].includes(name) || typeof v !== 'boolean')) throw new TypeError('Unknown pane or non-boolean guard');
    return value;
  }
  guards = validateGuards(paneGuards);
  const focused = () => { const el = doc.activeElement; return { el, start: el?.selectionStart, end: el?.selectionEnd, direction: el?.selectionDirection }; };
  const restoreFocus = prior => { if (!layer.top() && visible(prior?.el)) { prior.el.focus({ preventScroll: true }); if (typeof prior.start === 'number') try { prior.el.setSelectionRange(prior.start, prior.end, prior.direction); } catch { /* Not a text selection control. */ } } };
  function sync() {
    if (disposed) return;
    if (active && (!narrow(active.key) || keep(active.key))) { const prior = focused(); active.handle.destroy(); active = null; apply(); restoreFocus(prior); } else apply();
  }
  function apply() {
    // Snapshot viewport reads before material writes can invalidate layout.
    const width = win.innerWidth, scrollTop = mode === 'topbar' ? win.scrollY : 0;
    set(root, 'data-lq-enhanced', 'true');
    if (mode === 'topbar') { save(root, 'class'); root.classList.toggle('lq-glass', !active); }
    for (const panel of panels) {
      const key = panel.dataset.lqPane, collapsed = narrow(key, width) && !keep(key), open = active?.key === key;
      set(panel, 'data-lq-pane-mode', collapsed ? 'drawer' : 'inline');
      const surface = panel.querySelector(':scope > .lq-shell-pane__surface');
      set(surface, 'class', surfaceClasses.get(surface) + (open ? ' lq-glass lq-glass--thick' : mode === 'sidebar' ? ' lq-glass' : ''));
      for (const trigger of triggers.filter(el => el.dataset.lqPaneOpen === key)) { set(trigger, 'hidden', collapsed && hasActions() ? null : ''); set(trigger, 'aria-expanded', String(open)); }
      if (!open) {
        if (collapsed && panel.contains(doc.activeElement)) triggers.find(el => el.dataset.lqPaneOpen === key && visible(el))?.focus({ preventScroll: true });
        set(panel, 'hidden', collapsed || !hasActions() ? '' : null);
      }
      if (panel.tagName === 'DIALOG') set(panel, 'open', open || !collapsed && hasActions() ? '' : null);
    }
    if (mode === 'topbar') { set(root, 'data-lq-condensed', String(scrollTop > 80)); set(root, 'data-lq-scroll-edge', String(scrollTop > 0)); }
  }
  function openPane(key, suppliedTrigger) {
    if (disposed) throw new Error('Shell is destroyed');
    const panel = panels.find(el => el.dataset.lqPane === key);
    if (!panel) throw new TypeError('Unknown shell pane');
    if (!hasActions()) return null;
    if (!narrow(key) || keep(key)) { panel.querySelector('input,button,a[href],[tabindex]')?.focus(); return null; }
    if (active?.key === key) return active.handle;
    safeHost(panel, doc, Boolean(active));
    if (active) active.handle.destroy();
    const current = ++generation, trigger = suppliedTrigger || triggers.find(el => el.dataset.lqPaneOpen === key && visible(el));
    const surface = panel.querySelector(':scope > .lq-shell-pane__surface'), parent = panel.parentNode, next = panel.nextSibling;
    set(panel, 'data-lq-pane-mode', 'drawer');
    // The native layer owns dialog display. Keep it hidden until it can batch
    // background isolation and scroll locking with showModal's layout flush.
    if (panel.tagName === 'DIALOG') save(panel, 'hidden');
    else set(panel, 'hidden', null);
    if (panel.tagName === 'DIALOG') set(panel, 'open', null);
    const done = reason => { if (generation !== current || disposed) return; active = null; if (reason === 'parent-destroyed') { handle.destroy(); return; } apply(); };
    try {
      const handle = layer.open(panel, { type: 'drawer', surface: panel.tagName === 'DIALOG' ? panel : surface, trigger, owner: root, returnFocus: () => visible(trigger) ? trigger : root.querySelector('main'), onClose: done, onDestroy: done });
      if (panel.parentNode !== parent || panel.nextSibling !== next) { handle.destroy(); throw new Error('Layer moved an in-place pane'); }
      active = { key, panel, handle }; apply(); return handle;
    } catch (error) { apply(); throw error; }
  }
  const closePane = () => active ? layer.close(active.handle, 'button') : Promise.resolve(true);
  listen(root, 'click', event => {
    const open = event.target.closest?.('[data-lq-pane-open]'), close = event.target.closest?.('[data-lq-pane-close]');
    if (open && root.contains(open)) { event.preventDefault(); try { openPane(open.dataset.lqPaneOpen, open); } catch (error) { try { onError?.(error); } catch { /* An error observer cannot own this shell. */ } } }
    else if (close && active?.panel.contains(close)) { event.preventDefault(); void closePane(); }
  });
  listen(win, 'resize', sync);
  if (mode === 'topbar') listen(win, 'scroll', apply, { passive: true });
  let observer;
  const measured = mode === 'topbar' ? root : root.querySelector(':scope > .lq-editor__bar');
  const oldHeight = root.style.getPropertyValue('--lq-topbar-h'), oldPriority = root.style.getPropertyPriority('--lq-topbar-h');
  if (measured && win.ResizeObserver) { observer = new win.ResizeObserver(() => { if (!disposed) root.style.setProperty('--lq-topbar-h', `${measured.getBoundingClientRect().height}px`); }); observer.observe(measured); }
  if (mode === 'sidebar') {
    const search = root.querySelector('[data-lq-nav-search-input]'), groups = [...root.querySelectorAll('[data-lq-nav-group]')], entries = [...root.querySelectorAll('[data-lq-nav-search]')], empty = root.querySelector('[data-lq-nav-empty]');
    let previous = null, storageKey = null;
    if (root.dataset.lqPersist) try { storageKey = `lq.sidebar:${root.dataset.lqPersist}`; const value = win.localStorage.getItem(storageKey); if (groups.some(group => group.dataset.lqNavGroup === value)) for (const group of groups) set(group, 'open', group.dataset.lqNavGroup === value ? '' : null); } catch { /* Preferences are optional. */ }
    // Native toggle events are queued and can be coalesced. Remember internal
    // writes so a late search-restoration event cannot undo the next user choice.
    const groupStates = new Map(groups.map(group => [group, group.open]));
    const setGroupOpen = (group, open) => { groupStates.set(group, open); group.open = open; };
    for (const group of groups) {
      save(group, 'open');
      listen(group, 'toggle', () => {
        if (disposed || group.open === groupStates.get(group)) return;
        groupStates.set(group, group.open);
        if (search.value.trim()) return;
        if (group.open) { for (const other of groups) if (other !== group) setGroupOpen(other, false); if (storageKey) try { win.localStorage.setItem(storageKey, group.dataset.lqNavGroup); } catch { /* Optional. */ } }
      });
    }
    listen(search, 'input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      if (query && !previous) previous = groups.map(group => group.open);
      for (const item of entries) set(item, 'hidden', !query || item.dataset.lqNavSearch.toLocaleLowerCase().includes(query) ? null : '');
      for (let i = 0; i < groups.length; i++) { const group = groups[i], match = [...group.querySelectorAll('[data-lq-nav-search]')].some(item => !item.hidden); set(group, 'hidden', match ? null : ''); if (query) setGroupOpen(group, match); else if (previous) setGroupOpen(group, previous[i]); }
      if (!query) previous = null; set(empty, 'hidden', entries.some(item => !item.hidden) ? '' : null);
    });
    listen(doc, 'keydown', event => {
      if (event.defaultPrevented || !(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'k' || !root.isConnected || root.closest('[inert]')) return;
      try { if (narrow('nav')) openPane('nav'); } catch (error) { onError?.(error); return; }
      event.preventDefault(); set(root, 'data-lq-searching', 'true'); search.focus({ preventScroll: true });
    });
    listen(root, 'focusout', () => win.queueMicrotask(() => { if (!disposed && !root.contains(doc.activeElement) && !search.value) set(root, 'data-lq-searching', null); }));
  }
  const handle = { openPane, closePane, refresh(value = guards) { guards = validateGuards(value); sync(); }, destroy() {
    if (disposed) return; disposed = true; generation++; active?.handle.destroy(); active = null; observer?.disconnect(); releases.reverse().forEach(fn => fn());
    for (const [el, values] of saved) for (const [name, value] of values) if (value === null) el.removeAttribute(name); else el.setAttribute(name, value);
    if (oldHeight) root.style.setProperty('--lq-topbar-h', oldHeight, oldPriority); else root.style.removeProperty('--lq-topbar-h'); delete root[shellOwner];
  } };
  root[shellOwner] = handle; sync(); return handle;
}

const dockOwner = Symbol.for('lanshare.lq.dock-owner'), viewportKey = Symbol.for('lanshare.lq.viewport');
function subscribeViewport(win, callback) {
  let service = win[viewportKey];
  if (!service) {
    const subscribers = new Set(), fire = event => subscribers.forEach(fn => fn(event));
    service = { subscribers, fire }; win[viewportKey] = service;
    win.addEventListener('resize', fire); win.visualViewport?.addEventListener('resize', fire); win.document.addEventListener('focusin', fire); win.document.addEventListener('focusout', fire);
  }
  service.subscribers.add(callback);
  return () => { service.subscribers.delete(callback); if (!service.subscribers.size) { win.removeEventListener('resize', service.fire); win.visualViewport?.removeEventListener('resize', service.fire); win.document.removeEventListener('focusin', service.fire); win.document.removeEventListener('focusout', service.fire); delete win[viewportKey]; } };
}
export function enhanceDock(root, { contentRoot, fallbacks = {} } = {}) {
  if (!root?.matches?.('[data-lq-shell="dock"]') || !root.isConnected || !contentRoot?.isConnected || contentRoot.ownerDocument !== root.ownerDocument || root.contains(contentRoot) || !object(fallbacks)) throw new TypeError('Dock needs a connected, separate content root');
  if (root[dockOwner]) return root[dockOwner];
  const doc = root.ownerDocument, win = doc.defaultView;
  const commands = [...root.querySelectorAll('button[data-lq-command]')].map(el => el.dataset.lqCommand);
  if (Object.keys(fallbacks).some(key => !commands.includes(key))) throw new TypeError('Fallback does not match a Dock command');
  for (const [key, target] of Object.entries(fallbacks)) if (target?.ownerDocument !== doc || root.contains(target) || target.dataset.lqCommand !== key) throw new TypeError('Fallback must be the same explicit command outside the Dock');
  const original = { hidden: root.hidden, inert: root.inert, state: root.getAttribute('data-lq-keyboard-hidden'), padding: contentRoot.style.getPropertyValue('--lq-dock-h'), priority: contentRoot.style.getPropertyPriority('--lq-dock-h'), compensated: contentRoot.getAttribute('data-lq-dock-content') };
  let disposed = false, moreHandle = null, morePending = null;
  const more = root.querySelector('[data-lq-dock-more]'), summary = more?.querySelector('summary'), overflow = more?.querySelector('[data-lq-dock-overflow]'), originalMoreOpen = more?.open;
  const openMore = () => {
    if (disposed || !overflow || !root.isConnected) return Promise.resolve(null);
    if (moreHandle && !['closed', 'destroyed'].includes(moreHandle.state)) return Promise.resolve(moreHandle);
    if (!morePending) morePending = import('./dialogs.js').then(module => {
      if (disposed || !root.isConnected) return null;
      more.open = false;
      moreHandle = module.openDialog({ type: 'sheet', title: '更多操作', body: overflow }, { trigger: summary, owner: root, onClose: () => { moreHandle = null; }, onDestroy: () => { moreHandle = null; } }, doc);
      return moreHandle;
    }).finally(() => { morePending = null; });
    return morePending;
  };
  const moreClick = event => { event.preventDefault(); void openMore().catch(() => { if (!disposed && more) more.open = true; }); };
  summary?.addEventListener('click', moreClick);
  const measure = () => {
    if (disposed) return;
    const height = root.hidden || win.innerWidth >= 768 ? '0px' : `${root.getBoundingClientRect().height + 24}px`;
    if (contentRoot.style.getPropertyValue('--lq-dock-h') !== height) contentRoot.style.setProperty('--lq-dock-h', height);
  };
  const usable = key => visible(fallbacks[key]) && !fallbacks[key].matches(':disabled,[aria-disabled="true"]');
  const update = event => {
    if (disposed) return;
    const viewport = win.visualViewport, focus = doc.activeElement;
    const editing = focus?.matches?.('input:not([type=button]):not([type=checkbox]):not([type=radio]),textarea,[contenteditable="true"]');
    const keyboard = viewport && Math.abs(viewport.scale - 1) < .01 && win.innerHeight - viewport.height > 150 && editing;
    const hide = Boolean(keyboard && commands.every(usable));
    if (hide && root.contains(focus)) { const target = fallbacks[focus.dataset.lqCommand]; if (!usable(focus.dataset.lqCommand)) return; target.focus({ preventScroll: true }); }
    const hidden = original.hidden || hide, inert = original.inert || hide;
    const visibilityChanged = root.hidden !== hidden;
    if (visibilityChanged) root.hidden = hidden;
    if (root.inert !== inert) root.inert = inert;
    if (root.getAttribute('data-lq-keyboard-hidden') !== String(hide)) root.setAttribute('data-lq-keyboard-hidden', String(hide));
    // Modal focus changes do not resize an unchanged Dock. Reading its rect
    // here would synchronously flush the modal's pending style/layout writes.
    // Geometry changes remain owned by ResizeObserver, resize and refresh.
    const focusEvent = event?.type === 'focusin' || event?.type === 'focusout';
    if (!focusEvent || visibilityChanged || !win.ResizeObserver) measure();
  };
  contentRoot.setAttribute('data-lq-dock-content', '');
  const release = subscribeViewport(win, update), observer = win.ResizeObserver ? new win.ResizeObserver(measure) : null; observer?.observe(root);
  const handle = { refresh: update, openMore, destroy() {
    if (disposed) return; disposed = true; moreHandle?.destroy(); summary?.removeEventListener('click', moreClick); if (more) more.open = originalMoreOpen; release(); observer?.disconnect(); root.hidden = original.hidden; root.inert = original.inert;
    if (original.state === null) root.removeAttribute('data-lq-keyboard-hidden'); else root.setAttribute('data-lq-keyboard-hidden', original.state);
    if (original.compensated === null) contentRoot.removeAttribute('data-lq-dock-content'); else contentRoot.setAttribute('data-lq-dock-content', original.compensated);
    if (original.padding) contentRoot.style.setProperty('--lq-dock-h', original.padding, original.priority); else contentRoot.style.removeProperty('--lq-dock-h'); delete root[dockOwner];
  } };
  root[dockOwner] = handle; update(); return handle;
}
