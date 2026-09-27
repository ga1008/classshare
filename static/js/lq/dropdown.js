/**
 * LQ dropdown — the liquid-glass replacement for a native <select> used as a
 * picker: single dropdown, searchable dropdown and multi-select dropdown.
 *
 * The native <select> stays the sole value owner (forms, validation, existing
 * change listeners keep working); this module only renders a glass trigger and
 * a floating listbox through the shared layer system. Contract mirrors
 * `bindSelection` (selection.js): `bindDropdown(select, options)` →
 * `{ open, close, refresh, query, setResults, destroy }`.
 *
 * options: { searchable?: boolean, placeholder?: string, onQuery?(ticket), onError?(e),
 *            parentLayer?, maxSummary?: number }
 *   - searchable: shows a filter input at the top of the popup (type-to-filter);
 *     with `onQuery` the caller supplies async options via `setResults`.
 *   - `select.multiple` → checkbox rows, popup stays open while toggling.
 * Declarative: `<select data-lq-dropdown data-lq-searchable>` is enhanced by
 * `enhanceDropdowns(root)`.
 */
import { createComponent } from './components.js';
import { getLayerSystem } from './layer.js';

const BINDINGS = Symbol.for('lanshare.lq.dropdown-bindings.v1');
const IDS = Symbol.for('lanshare.lq.dropdown-identities');
const string = value => { if (typeof value !== 'string') throw new TypeError('LQ dropdown text must be a string'); return value; };

export function bindDropdown(select, options = {}) {
    const doc = select?.ownerDocument, view = doc?.defaultView;
    if (!doc || select.tagName !== 'SELECT' || !select.isConnected) throw new TypeError('LQ dropdown requires a connected native select');
    for (const key of ['onQuery', 'onError']) if (options[key] !== undefined && typeof options[key] !== 'function') throw new TypeError(`Invalid dropdown ${key}`);
    const bindings = doc[BINDINGS] ||= new WeakMap(); if (bindings.has(select)) return bindings.get(select);
    const system = getLayerSystem(doc), listeners = [], ownedOptions = new Set(), optionIds = new WeakMap();
    const searchable = Boolean(options.searchable ?? select.hasAttribute('data-lq-searchable'));
    const placeholder = string(options.placeholder ?? select.dataset.lqPlaceholder ?? '请选择');
    const maxSummary = Number(options.maxSummary || 2);
    let id; do { id = `lq-dropdown-${doc[IDS] = (doc[IDS] || 0) + 1}`; } while (doc.getElementById(id));
    const original = new Map(['tabindex', 'aria-hidden'].map(name => [name, select.getAttribute(name)]));
    const labels = [...select.labels].map(label => ({ label, for: label.getAttribute('for') }));
    const name = select.getAttribute('aria-label') || labels.map(row => row.label.textContent.trim()).join(' ') || options.label;
    if (typeof name !== 'string' || !name.trim()) throw new TypeError('LQ dropdown needs an accessible name');

    const wrapper = doc.createElement('div'); wrapper.className = `lq-dropdown${select.multiple ? ' lq-dropdown--multi' : ''}`;
    const trigger = doc.createElement('button'); trigger.type = 'button'; trigger.className = 'lq-control lq-dropdown__trigger'; trigger.id = id;
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false'); trigger.setAttribute('aria-label', name);
    const valueNode = doc.createElement('span'); valueNode.className = 'lq-dropdown__value';
    // LQ button props reject aria-*/tabindex attrs; set the decorative state after creation.
    const chevron = createComponent('button', { icon: 'chevron-down', variant: 'ghost', attrs: { 'aria-label': `展开${name}` } }, doc);
    chevron.classList.add('lq-dropdown__chevron'); chevron.tabIndex = -1; chevron.setAttribute('aria-hidden', 'true');
    trigger.append(valueNode, chevron); wrapper.append(trigger);

    const popup = doc.createElement('div'); popup.className = 'lq-selection__popup lq-dropdown__popup lq-glass'; popup.setAttribute('role', 'presentation'); popup.hidden = true;
    const search = searchable ? doc.createElement('input') : null;
    if (search) { search.type = 'text'; search.className = 'lq-input lq-dropdown__search'; search.autocomplete = 'off'; search.placeholder = '输入以筛选…'; search.setAttribute('aria-label', `筛选${name}`); search.setAttribute('role', 'combobox'); search.setAttribute('aria-autocomplete', 'list'); popup.append(search); }
    const listbox = doc.createElement('div'); listbox.className = 'lq-selection__list lq-dropdown__list'; listbox.id = `${id}-list`; listbox.setAttribute('role', 'listbox'); listbox.setAttribute('aria-label', name); listbox.tabIndex = search ? -1 : 0;
    if (select.multiple) listbox.setAttribute('aria-multiselectable', 'true');
    const status = doc.createElement('p'); status.className = 'lq-selection__status'; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    popup.append(listbox, status);
    trigger.setAttribute('aria-controls', listbox.id);
    if (search) search.setAttribute('aria-controls', listbox.id);

    let handle = null, disposed = false, generation = 0, queryText = '', resultOptions = null, activeOption = null, state = 'ready', errorText = '', rendering = false, composing = false;
    const listen = (node, event, callback, opts) => { node.addEventListener(event, callback, opts); listeners.push(() => node.removeEventListener(event, callback, opts)); };
    const disabled = option => option.disabled || option.closest('optgroup')?.disabled;
    const alive = () => !disposed && select.isConnected && !select.matches(':disabled');
    const isOpen = () => Boolean(handle && ['opening', 'open'].includes(handle.state));
    const optionId = option => { if (!optionIds.has(option)) optionIds.set(option, `${id}-option-${doc[IDS] = (doc[IDS] || 0) + 1}`); return optionIds.get(option); };
    const visibleOptions = () => [...select.options].filter(option => !option.hidden && (resultOptions ? resultOptions.has(option) : !queryText || option.label.toLocaleLowerCase().includes(queryText.toLocaleLowerCase())));
    const enabledOptions = () => visibleOptions().filter(option => !disabled(option));
    const focusTarget = () => search || listbox;

    function summary() {
        const chosen = [...select.selectedOptions].filter(option => option.value !== '' || select.multiple);
        if (!chosen.length) return { text: placeholder, empty: true };
        if (!select.multiple) return { text: chosen[0].label, empty: false };
        const shown = chosen.slice(0, maxSummary).map(option => option.label).join('、');
        return { text: chosen.length > maxSummary ? `${shown} 等 ${chosen.length} 项` : shown, empty: false };
    }
    function paintActive() {
        const target = focusTarget();
        if (activeOption && visibleOptions().includes(activeOption) && !disabled(activeOption)) target.setAttribute('aria-activedescendant', optionId(activeOption)); else { activeOption = null; target.removeAttribute('aria-activedescendant'); }
        for (const row of listbox.children) row.toggleAttribute('data-active', row.id === target.getAttribute('aria-activedescendant'));
        doc.getElementById(target.getAttribute('aria-activedescendant'))?.scrollIntoView?.({ block: 'nearest' });
    }
    function render() {
        if (disposed || rendering) return; rendering = true;
        const disabledNow = select.matches(':disabled');
        trigger.disabled = disabledNow; trigger.setAttribute('aria-disabled', String(disabledNow)); trigger.setAttribute('aria-required', String(select.required));
        const { text, empty } = summary(); valueNode.textContent = text; wrapper.classList.toggle('is-empty', empty);
        if (disabledNow && isOpen()) void close('disabled');
        listbox.replaceChildren();
        for (const option of visibleOptions()) {
            const row = doc.createElement('div'); row.className = 'lq-selection__option lq-dropdown__option'; row.id = optionId(option); row.setAttribute('role', 'option');
            row.setAttribute('aria-selected', String(option.selected)); if (disabled(option)) row.setAttribute('aria-disabled', 'true');
            if (select.multiple) { const box = doc.createElement('span'); box.className = 'lq-dropdown__check'; box.setAttribute('aria-hidden', 'true'); row.append(box); }
            const label = doc.createElement('span'); label.className = 'lq-dropdown__option-label'; label.textContent = option.label; row.append(label);
            if (option.dataset.hint) { const hint = doc.createElement('small'); hint.className = 'lq-dropdown__option-hint'; hint.textContent = option.dataset.hint; row.append(hint); }
            listbox.append(row);
        }
        status.textContent = state === 'error' ? (errorText || '查找失败，请重试。') : state === 'loading' ? '正在查找…' : listbox.children.length ? '' : '没有匹配选项';
        paintActive(); rendering = false;
    }
    function refresh() { if (!disposed) render(); }
    function afterClose() { if (disposed) return; trigger.setAttribute('aria-expanded', 'false'); wrapper.classList.remove('is-open'); activeOption = null; queryText = ''; resultOptions = null; if (search) search.value = ''; render(); }
    function open() {
        if (!alive() || isOpen()) return handle;
        const host = system.getPortalHost({ trigger, parentLayer: options.parentLayer }); if (popup.parentNode !== host) host.append(popup);
        trigger.setAttribute('aria-expanded', 'true'); wrapper.classList.add('is-open');
        activeOption = [...select.selectedOptions].find(option => !disabled(option)) || null; render();
        handle = system.open(popup, { type: 'popover', modality: 'non-modal', trigger, owner: select, anchor: trigger, ...(options.parentLayer ? { parentLayer: options.parentLayer } : {}),
            initialFocus: () => focusTarget(), returnFocus: true, onClose: () => { popup.remove(); afterClose(); }, onDestroy: () => destroy() });
        return handle;
    }
    function close(reason = 'programmatic') { generation++; if (!isOpen()) { afterClose(); return Promise.resolve(true); } return system.close(handle, reason); }
    function query(value) {
        if (!alive()) return null;
        queryText = string(value); generation++; resultOptions = null; activeOption = null; state = options.onQuery ? 'loading' : 'ready'; errorText = ''; render();
        const ticket = { query: queryText, generation };
        if (options.onQuery) {
            const fail = error => { if (!disposed && ticket.generation === generation) { state = 'error'; render(); try { options.onError?.(error); } catch { /* consumer errors never retain state */ } } };
            try { Promise.resolve(options.onQuery(ticket)).catch(fail); } catch (error) { fail(error); }
        }
        return ticket;
    }
    function setResults(result) {
        if (!alive() || !result || result.query !== queryText || result.generation !== generation) return false;
        const values = Array.isArray(result.options) ? result.options : null;
        if (values) {
            const selected = new Set(select.selectedOptions), byValue = new Map([...select.options].map(option => [option.value, option])), next = new Set();
            for (const item of values) {
                let option = byValue.get(string(item.value));
                if (!option) { option = doc.createElement('option'); option.value = item.value; ownedOptions.add(option); select.append(option); }
                option.textContent = string(item.label); option.disabled = Boolean(item.disabled); if (item.hint) option.dataset.hint = String(item.hint); next.add(option);
            }
            for (const option of [...ownedOptions]) if (!next.has(option) && !option.selected) { option.remove(); ownedOptions.delete(option); }
            for (const option of select.options) option.selected = selected.has(option);
            resultOptions = next;
        }
        state = result.status || 'ready'; errorText = result.message || ''; activeOption = null; render(); return true;
    }
    function commit(option) {
        if (!alive() || !option || disabled(option)) return;
        const before = [...select.options].map(item => item.selected).join(',');
        if (select.multiple) option.selected = !option.selected; else for (const item of select.options) item.selected = item === option;
        activeOption = option; render();
        if (before !== [...select.options].map(item => item.selected).join(',')) { select.dispatchEvent(new view.Event('input', { bubbles: true })); if (!disposed) select.dispatchEvent(new view.Event('change', { bubbles: true })); }
        if (!select.multiple && !disposed) void close('selection');
    }
    function move(step) {
        const list = enabledOptions(); if (!list.length) return;
        const index = list.indexOf(activeOption);
        activeOption = step === 'first' ? list[0] : step === 'last' ? list.at(-1) : list[Math.max(0, Math.min(list.length - 1, index < 0 ? (step > 0 ? 0 : list.length - 1) : index + step))];
        paintActive();
    }
    function destroy() {
        if (disposed) return; disposed = true; generation++; observer.disconnect(); for (const remove of listeners) remove(); handle?.destroy(); popup.remove(); wrapper.remove();
        for (const [attr, value] of original) value === null ? select.removeAttribute(attr) : select.setAttribute(attr, value);
        select.classList.remove('lq-selection-native');
        for (const item of labels) if (item.label.getAttribute('for') === trigger.id) item.for === null ? item.label.removeAttribute('for') : item.label.setAttribute('for', item.for);
        bindings.delete(select);
    }
    const keys = event => {
        if (event.defaultPrevented || composing || event.isComposing || event.keyCode === 229 || !alive()) return;
        if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); if (!isOpen()) open(); move(event.key === 'ArrowDown' ? 1 : -1); }
        else if (['Home', 'End'].includes(event.key) && isOpen() && !search) { event.preventDefault(); move(event.key === 'Home' ? 'first' : 'last'); }
        else if ((event.key === 'Enter' || (event.key === ' ' && !search)) && isOpen()) { event.preventDefault(); if (activeOption) commit(activeOption); }
        else if (event.key === 'Tab' && isOpen()) { void close('tab'); }
    };
    listen(trigger, 'click', () => { if (!alive()) return; if (isOpen()) void close('button'); else open(); });
    listen(trigger, 'keydown', keys); listen(listbox, 'keydown', keys);
    listen(listbox, 'pointerdown', event => { if (event.button === 0 && search) event.preventDefault(); });
    listen(listbox, 'click', event => { const row = event.target.closest('[role="option"]'); const option = [...select.options].find(item => optionId(item) === row?.id); if (option) commit(option); });
    listen(listbox, 'pointermove', event => { if (event.pointerType === 'touch') return; const row = event.target.closest('[role="option"]'); const option = row && [...select.options].find(item => optionId(item) === row.id); if (option && option !== activeOption && !disabled(option)) { activeOption = option; paintActive(); } });
    if (search) {
        listen(search, 'keydown', keys);
        listen(search, 'compositionstart', () => { composing = true; });
        listen(search, 'compositionend', () => { composing = false; query(search.value); });
        listen(search, 'input', () => { if (!composing) query(search.value); });
    }
    listen(select, 'change', refresh); listen(select, 'input', refresh); listen(select, 'focus', () => trigger.focus());
    listen(select, 'invalid', event => { event.preventDefault(); trigger.setAttribute('aria-invalid', 'true'); status.textContent = select.validationMessage; trigger.focus({ preventScroll: true }); });
    const observer = new view.MutationObserver(records => { if (!select.isConnected || !wrapper.isConnected) { destroy(); return; } if (records.some(record => record.target === select || select.contains(record.target))) refresh(); });
    for (const item of labels) item.label.htmlFor = trigger.id;
    select.classList.add('lq-selection-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true'); select.after(wrapper);
    observer.observe(doc.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'required', 'multiple', 'selected', 'label', 'value'] });
    const binding = { select, trigger, listbox, search, open, close, refresh, query, setResults, destroy, get handle() { return handle; } };
    bindings.set(select, binding); refresh(); return binding;
}

/** Enhance every `<select data-lq-dropdown>` under root that is not bound yet. */
export function enhanceDropdowns(root = document) {
    const doc = root.ownerDocument || root;
    const bindings = doc[BINDINGS] ||= new WeakMap();
    const result = [];
    for (const select of root.querySelectorAll('select[data-lq-dropdown]')) {
        if (bindings.has(select)) { result.push(bindings.get(select)); continue; }
        result.push(bindDropdown(select, { searchable: select.hasAttribute('data-lq-searchable'), placeholder: select.dataset.lqPlaceholder }));
    }
    return result;
}

export function getDropdown(select) { return select?.ownerDocument?.[BINDINGS]?.get(select) || null; }
