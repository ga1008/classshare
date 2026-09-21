import { attributesMarkup, escapeHtml } from './html.js';
import { badge, html as presentationHTML } from './components.js';

const idPattern = /^[A-Za-z][A-Za-z0-9_-]*$/;
const option = (props, key, fallback) => props[key] === undefined ? fallback : props[key];
const named = value => {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError('LQ navigation requires text names');
    return value;
};
const identity = value => {
    if (typeof value !== 'string' || !idPattern.test(value) || value.includes('--lq-')) throw new TypeError('Invalid LQ navigation id/key');
    return value;
};
const oneOf = (value, values) => {
    if (!values.includes(value)) throw new TypeError('Invalid LQ navigation option');
    return value;
};
const node = (tag, attrs, children = []) => ({ tag, attrs, children });

/** A typed, escaped presentation tree. Panels may instead be authored caller
 * slots (Jinja) or existing Nodes (Element); strings never become raw HTML. */
export function navigationProps(kind, props = {}) {
    if (!props || typeof props !== 'object' || Array.isArray(props)) throw new TypeError('Invalid navigation props');
    oneOf(kind, ['tabs', 'segment']);
    const id = identity(props.id), label = named(props.label);
    const orientation = oneOf(option(props, 'orientation', 'horizontal'), ['horizontal', 'vertical']);
    const activation = oneOf(option(props, 'activation', 'auto'), ['auto', 'manual']);
    const size = oneOf(option(props, 'size', 'md'), ['sm', 'md']);
    const variant = oneOf(option(props, 'variant', 'line'), ['line', 'pill']);
    if (kind === 'segment' && (orientation !== 'horizontal' || variant !== 'line')) throw new TypeError('Segment supports horizontal views only');
    if (!Array.isArray(props.items) || props.items.length < 2 || (kind === 'segment' && props.items.length > 5)) throw new TypeError('Invalid navigation item count');
    const keys = new Set();
    const items = Array.from(props.items, item => {
        if (!item || typeof item !== 'object' || Array.isArray(item)) throw new TypeError('Invalid navigation item');
        const key = identity(item.key), name = named(item.label);
        if (keys.has(key)) throw new TypeError('Duplicate navigation key');
        keys.add(key);
        const disabled = option(item, 'disabled', false);
        if (typeof disabled !== 'boolean') throw new TypeError('Disabled must be boolean');
        const panel = option(item, 'panel', '');
        if (typeof panel !== 'string') throw new TypeError('Panel content must be text');
        const count = option(item, 'badge', null);
        if (count !== null && (!Number.isSafeInteger(count) || count < 0)) throw new TypeError('Badge must be a nonnegative integer');
        return { key, label: name, disabled, panel, badge: count };
    });
    const selected = option(props, 'selected', items.find(item => !item.disabled)?.key);
    if (!items.some(item => item.key === selected && !item.disabled)) throw new TypeError('Selected view must be an enabled item');
    const tabs = items.map(item => node('button', {
        type: 'button', role: 'tab', class: 'lq-tabs__tab', id: `${id}--lq-tab-${item.key}`,
        'data-lq-tab': item.key, 'aria-controls': `${id}--lq-panel-${item.key}`,
        'aria-selected': String(item.key === selected), tabindex: item.key === selected ? '0' : '-1',
        ...(item.disabled ? { disabled: '', 'aria-disabled': 'true' } : {}),
    }, [node('span', { class: 'lq-tabs__label' }, [item.label]),
        ...(item.badge ? [{ component: 'badge', props: { value: item.badge } }] : [])]));
    return node('div', {
        id, class: `lq-tabs${kind === 'segment' ? ' lq-segment' : ''} lq-tabs--${orientation} lq-tabs--${variant} lq-tabs--${size}`,
        'data-lq-tabs': '', 'data-lq-activation': activation,
    }, [node('div', { class: 'lq-tabs__list', role: 'tablist', 'aria-label': label, 'aria-orientation': orientation }, tabs),
        node('div', { class: 'lq-tabs__panels' }, items.map(item => node('section', {
            class: 'lq-tabs__panel', role: 'tabpanel', id: `${id}--lq-panel-${item.key}`,
            'aria-labelledby': `${id}--lq-tab-${item.key}`, tabindex: '0',
            ...(item.key !== selected ? { hidden: '' } : {}),
        }, [{ slot: item.key, text: item.panel }])))]);
}

function markup(tree) {
    if (typeof tree === 'string') return escapeHtml(tree);
    if (tree.slot !== undefined) return escapeHtml(tree.text);
    if (tree.component) return presentationHTML[tree.component](tree.props);
    return `<${tree.tag}${attributesMarkup(tree.attrs)}>${tree.children.map(markup).join('')}</${tree.tag}>`;
}
function element(tree, doc, panels) {
    if (typeof tree === 'string') return doc.createTextNode(tree);
    if (tree.slot !== undefined) {
        const content = panels?.get(tree.slot);
        if (content !== undefined) {
            if (!(content instanceof doc.defaultView.Node)) throw new TypeError('Panel slots require Nodes');
            return content;
        }
        return doc.createTextNode(tree.text);
    }
    if (tree.component) return badge(tree.props, doc);
    const result = doc.createElement(tree.tag);
    Object.entries(tree.attrs).forEach(([key, value]) => result.setAttribute(key, value));
    tree.children.forEach(child => result.append(element(child, doc, panels)));
    return result;
}
export const navigationMarkup = (kind, props) => markup(navigationProps(kind, props));
export function createNavigation(kind, props, doc = document, panels) {
    const tree = navigationProps(kind, props);
    if (panels != null) {
        if (!(panels instanceof Map)) throw new TypeError('Panel slots require a Map');
        const keys = new Set(props.items.map(item => item.key)), used = new Set();
        // Validate the entire slot set before moving any caller-owned node.
        for (const [key, content] of panels) {
            if (!keys.has(key) || !(content instanceof doc.defaultView.Node)
                || ![1, 3, 11].includes(content.nodeType) || content.host || used.has(content)) throw new TypeError('Invalid or duplicate panel Node');
            used.add(content);
        }
    }
    return element(tree, doc, panels);
}
export const createTabs = (props, doc, panels) => createNavigation('tabs', props, doc, panels);
export const createSegment = (props, doc, panels) => createNavigation('segment', props, doc, panels);
export const html = Object.freeze({ tabs: props => navigationMarkup('tabs', props), segment: props => navigationMarkup('segment', props) });

const ownerKey = Symbol.for('lanshare.lq.tabs.v1');
/** One behavior owner per root. Call destroy before handing the DOM to another
 * controller. No network, account preference, subtree remount or global key. */
export function tabs(root, options = {}) {
    if (!root?.matches?.('[data-lq-tabs]')) throw new TypeError('Expected an LQ tabs root');
    if (root[ownerKey]) return root[ownerKey];
    const doc = root.ownerDocument, win = doc.defaultView;
    let persist = null;
    if (options.persist !== undefined && options.persist !== false) {
        const scope = options.persist;
        if (!scope || typeof scope !== 'object' || !['identity', 'resource', 'key'].every(key => typeof scope[key] === 'string' && scope[key].trim())) throw new TypeError('Persist requires identity, resource and key');
        persist = `lq:tabs:v1:${JSON.stringify([scope.identity, scope.resource, scope.key])}`;
    }
    const hash = oneOf(option(options, 'hash', false), [false, true, 'replace', 'push']);
    if (options.onChange !== undefined && typeof options.onChange !== 'function') throw new TypeError('onChange must be a function');
    const activation = oneOf(options.activation ?? root.dataset.lqActivation ?? 'auto', ['auto', 'manual']);
    const lists = [...root.querySelectorAll('[role="tablist"]')].filter(el => el.closest('[data-lq-tabs]') === root);
    if (lists.length !== 1) throw new TypeError('Expected one owned tablist');
    const list = lists[0];
    const entries = [...list.querySelectorAll('[data-lq-tab]')].filter(tab => tab.closest('[role="tablist"]') === list).map(tab => {
        const matches = [...root.querySelectorAll('[role="tabpanel"]')].filter(panel => panel.id === tab.getAttribute('aria-controls') && panel.closest('[data-lq-tabs]') === root);
        identity(tab.dataset.lqTab);
        if (tab.tagName !== 'BUTTON' || tab.getAttribute('role') !== 'tab' || !tab.id || matches.length !== 1 || matches[0].getAttribute('aria-labelledby') !== tab.id) throw new TypeError('Invalid tab/panel relationship');
        return { key: tab.dataset.lqTab, tab, panel: matches[0] };
    });
    if (entries.length < 2 || new Set(entries.map(entry => entry.key)).size !== entries.length || new Set(entries.map(entry => entry.tab.id)).size !== entries.length || new Set(entries.map(entry => entry.panel)).size !== entries.length) throw new TypeError('Ambiguous tabs');
    const enabled = entry => !entry.tab.disabled && entry.tab.getAttribute('aria-disabled') !== 'true';
    const byKey = key => entries.find(entry => entry.key === key && enabled(entry));
    if (!entries.some(enabled)) throw new TypeError('Tabs require an enabled view');
    let current = null, destroyed = false, generation = 0, resizeFrame = 0;
    const animations = new Set();
    const motion = win.matchMedia('(prefers-reduced-motion: reduce)');
    const thumbProperties = ['--lq-thumb-x', '--lq-thumb-y', '--lq-thumb-w', '--lq-thumb-h'];
    const originalThumb = thumbProperties.map(key => [key, list.style.getPropertyValue(key), list.style.getPropertyPriority(key)]);
    const originalMarker = list.getAttribute('data-lq-thumb');
    const originalListTabindex = list.getAttribute('tabindex');
    const restoreAttribute = (key, value) => value === null ? list.removeAttribute(key) : list.setAttribute(key, value);
    let fallbackFocus = false;
    const readStored = () => { try { return persist ? win.localStorage.getItem(persist) : null; } catch { return null; } };
    const fromHash = () => {
        try { const id = decodeURIComponent(win.location.hash.slice(1)); return entries.find(entry => entry.panel.id === id && enabled(entry)); }
        catch { return null; }
    };
    function measure() {
        if (destroyed || !current) return;
        const box = current.tab.getBoundingClientRect(), base = list.getBoundingClientRect();
        list.style.setProperty('--lq-thumb-x', `${box.left - base.left - list.clientLeft + list.scrollLeft}px`);
        list.style.setProperty('--lq-thumb-y', `${box.top - base.top - list.clientTop + list.scrollTop}px`);
        list.style.setProperty('--lq-thumb-w', `${box.width}px`);
        list.style.setProperty('--lq-thumb-h', `${box.height}px`);
        list.setAttribute('data-lq-thumb', '');
    }
    function resized() {
        if (!resizeFrame) resizeFrame = win.requestAnimationFrame(() => { resizeFrame = 0; measure(); });
    }
    function cancelPresence() { for (const animation of animations) animation.cancel(); animations.clear(); }
    function select(key, { focus = false, reason = 'programmatic', sync = true, notify = true } = {}) {
        const next = byKey(key);
        if (destroyed || !next) return false;
        if (current === next) {
            if (focus) { entries.forEach(entry => { entry.tab.tabIndex = entry === next ? 0 : -1; }); next.tab.focus(); }
            return false;
        }
        const previous = current;
        const focusHidden = previous?.panel.contains(doc.activeElement);
        const ticket = ++generation;
        cancelPresence(); current = next;
        if (fallbackFocus) { fallbackFocus = false; restoreAttribute('tabindex', originalListTabindex); }
        // Move focus before hiding its ancestor; the panel's real nodes and
        // drafts stay in place, even during fast keyboard navigation.
        if (focus || focusHidden) next.tab.focus({ preventScroll: true });
        for (const entry of entries) {
            const selected = entry === next;
            entry.tab.setAttribute('aria-selected', String(selected));
            entry.tab.tabIndex = selected ? 0 : -1;
            entry.panel.hidden = !selected;
        }
        measure();
        if (focus) next.tab.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
        if (sync) {
            try { if (persist) win.localStorage.setItem(persist, next.key); } catch { /* Storage is optional. */ }
            if (hash) try {
                const url = new URL(win.location.href); url.hash = next.panel.id;
                if (url.href !== win.location.href) win.history[hash === 'push' ? 'pushState' : 'replaceState'](win.history.state, '', url);
            } catch { /* Sandboxed histories still permit local view changes. */ }
        }
        let presence = Promise.resolve();
        if (previous && !motion.matches && next.panel.animate) {
            const token = win.getComputedStyle(root).getPropertyValue('--ls-dur-fast').trim();
            const time = parseFloat(token) * (token.endsWith('s') && !token.endsWith('ms') ? 1000 : 1);
            const duration = Number.isFinite(time) && time >= 0 ? time : 120;
            const animation = next.panel.animate([{ opacity: 0 }, { opacity: 1 }], { duration, easing: 'ease-out' });
            animations.add(animation);
            presence = animation.finished.catch(() => {}).finally(() => { animations.delete(animation); });
        }
        if (notify && previous) presence.then(() => {
            if (destroyed || ticket !== generation) return;
            const detail = { key: next.key, previousKey: previous.key, panel: next.panel, reason };
            root.dispatchEvent(new win.CustomEvent('lq:tab-change', { detail, bubbles: true }));
            if (!destroyed && ticket === generation) options.onChange?.(detail);
        });
        return true;
    }
    function ownedTab(target) { return entries.find(entry => entry.tab === target?.closest?.('[data-lq-tab]')); }
    function click(event) {
        const entry = ownedTab(event.target);
        if (!entry || event.defaultPrevented) return;
        event.preventDefault();
        if (enabled(entry)) select(entry.key, { focus: true, reason: 'click' });
    }
    function keydown(event) {
        const entry = ownedTab(event.target);
        if (!entry || !enabled(entry) || event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey) return;
        const choices = entries.filter(enabled), index = choices.indexOf(entry);
        const vertical = list.getAttribute('aria-orientation') === 'vertical';
        const rtl = win.getComputedStyle(list).direction === 'rtl';
        let step = event.key === (vertical ? 'ArrowDown' : 'ArrowRight') ? 1 : event.key === (vertical ? 'ArrowUp' : 'ArrowLeft') ? -1 : 0;
        if (!vertical && rtl) step = -step;
        const next = event.key === 'Home' ? choices[0] : event.key === 'End' ? choices.at(-1) : step ? choices[(index + step + choices.length) % choices.length] : null;
        if (next) {
            event.preventDefault();
            if (activation === 'auto') select(next.key, { focus: true, reason: 'keyboard' });
            else { choices.forEach(item => { item.tab.tabIndex = item === next ? 0 : -1; }); next.tab.focus(); next.tab.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' }); }
        } else if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault(); select(entry.key, { focus: true, reason: 'keyboard' });
        }
    }
    function focusout(event) {
        if (!list.contains(event.relatedTarget) && current) entries.forEach(entry => { entry.tab.tabIndex = entry === current ? 0 : -1; });
    }
    function focusin(event) {
        const entry = ownedTab(event.target);
        if (activation === 'auto' && entry && enabled(entry)) select(entry.key, { reason: 'focus' });
    }
    function historyChanged() { const entry = fromHash(); if (entry) select(entry.key, { reason: 'history', sync: false }); }
    const initial = (hash && fromHash()) || byKey(readStored()) || entries.find(entry => enabled(entry) && entry.tab.getAttribute('aria-selected') === 'true') || entries.find(enabled);
    select(initial.key, { sync: false, notify: false });
    list.addEventListener('click', click); list.addEventListener('keydown', keydown); list.addEventListener('focusout', focusout); list.addEventListener('focusin', focusin);
    if (hash) { win.addEventListener('hashchange', historyChanged); win.addEventListener('popstate', historyChanged); }
    const observer = win.ResizeObserver ? new win.ResizeObserver(resized) : null;
    observer?.observe(list); entries.forEach(entry => observer?.observe(entry.tab));
    if (!observer) win.addEventListener('resize', resized);
    const motionChanged = event => { if (event.matches) cancelPresence(); };
    motion.addEventListener('change', motionChanged);
    const api = {
        get value() { return current?.key; }, select,
        refresh() {
            if (destroyed) return;
            if (!current || !enabled(current)) {
                const next = entries.find(enabled);
                if (next) select(next.key, { reason: 'refresh', sync: false, focus: doc.activeElement === list || doc.activeElement === current?.tab });
                else {
                    generation++; cancelPresence(); current = null;
                    if (list.contains(doc.activeElement) || entries.some(entry => entry.panel.contains(doc.activeElement))) {
                        fallbackFocus = true; list.tabIndex = -1; list.focus({ preventScroll: true });
                    }
                    entries.forEach(entry => { entry.tab.setAttribute('aria-selected', 'false'); entry.tab.tabIndex = -1; entry.panel.hidden = true; });
                    list.removeAttribute('data-lq-thumb');
                }
            }
            measure();
        },
        destroy() {
            if (destroyed) return;
            destroyed = true; generation++; cancelPresence();
            list.removeEventListener('click', click); list.removeEventListener('keydown', keydown); list.removeEventListener('focusout', focusout); list.removeEventListener('focusin', focusin);
            win.removeEventListener('hashchange', historyChanged); win.removeEventListener('popstate', historyChanged);
            win.removeEventListener('resize', resized); observer?.disconnect();
            if (resizeFrame) win.cancelAnimationFrame(resizeFrame);
            motion.removeEventListener('change', motionChanged);
            for (const [key, value, priority] of originalThumb) list.style.setProperty(key, value, priority);
            restoreAttribute('data-lq-thumb', originalMarker);
            if (fallbackFocus) restoreAttribute('tabindex', originalListTabindex);
            delete root[ownerKey];
        },
    };
    root[ownerKey] = api;
    return api;
}
export const segment = tabs;
