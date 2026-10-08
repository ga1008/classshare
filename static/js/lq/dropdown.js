/** Native select remains the sole value/form owner; LQ owns presentation and
 * the existing layer lifecycle. Explicit declarations never replace nodes. */
import { createIcon } from './icons.js';
import { getLayerSystem } from './layer.js';
import { observeNativeSelect } from './native-select-observer.js';

const BINDINGS = Symbol.for('lanshare.lq.dropdown-bindings.v1');
const SELECTION = Symbol.for('lanshare.lq.selection-bindings.v1');
const INSTALL = Symbol.for('lanshare.lq.dropdown-install.v1');
const IDS = Symbol.for('lanshare.lq.dropdown-identities');
const string = value => { if (typeof value !== 'string') throw new TypeError('LQ dropdown text must be a string'); return value; };
const reserved = select => select.hasAttribute('data-lq-selection') || select.hasAttribute('data-lq-selection-owner') || select.ownerDocument[SELECTION]?.has(select);
const automatic = select => select.matches('select[data-lq-dropdown]') && !select.hasAttribute('data-lq-dropdown-manual') && !reserved(select);

export function dropdownResults(result) {
    if (!result || typeof result !== 'object' || Object.keys(result).some(key => !['query', 'generation', 'options', 'status', 'message'].includes(key))) throw new TypeError('Invalid dropdown result');
    string(result.query);
    if (!Number.isSafeInteger(result.generation) || result.generation < 0) throw new TypeError('Invalid dropdown generation');
    if (result.status !== undefined && !['ready', 'loading', 'error'].includes(result.status)) throw new TypeError('Invalid dropdown result state');
    if (result.message !== undefined) string(result.message);
    if (result.options === undefined) return result;
    if (!Array.isArray(result.options)) throw new TypeError('Dropdown options must be a list');
    const seen = new Set();
    return { ...result, options: result.options.map(item => {
        if (!item || typeof item !== 'object' || Object.keys(item).some(key => !['value', 'label', 'disabled', 'hint'].includes(key))) throw new TypeError('Invalid dropdown option');
        const value = string(item.value), label = string(item.label);
        if (seen.has(value) || (item.disabled !== undefined && typeof item.disabled !== 'boolean')) throw new TypeError('Invalid dropdown option state');
        seen.add(value);
        return { value, label, disabled: Boolean(item.disabled), hint: item.hint === undefined ? '' : string(item.hint) };
    }) };
}

function labelText(label) {
    const copy = label.cloneNode(true);
    copy.querySelectorAll('select,input,textarea,button,.lq-dropdown,.lq-selection,[aria-hidden="true"]').forEach(node => node.remove());
    return copy.textContent.trim();
}

export function bindDropdown(select, options = {}) {
    const doc = select?.ownerDocument, view = doc?.defaultView;
    if (!doc || select.tagName !== 'SELECT' || !select.isConnected) throw new TypeError('LQ dropdown requires a connected native select');
    const bindings = doc[BINDINGS] ||= new WeakMap();
    if (bindings.has(select)) return bindings.get(select);
    if (reserved(select)) throw new TypeError('Native select already has a Selection owner');
    for (const key of ['onQuery', 'onError']) if (options[key] !== undefined && typeof options[key] !== 'function') throw new TypeError(`Invalid dropdown ${key}`);
    if (options.searchable !== undefined && typeof options.searchable !== 'boolean') throw new TypeError('Invalid dropdown searchable flag');
    const searchable = options.searchable ?? select.hasAttribute('data-lq-searchable');
    const placeholder = string(options.placeholder ?? select.dataset.lqPlaceholder ?? '请选择');
    const maxSummary = options.maxSummary ?? 2;
    if (!Number.isSafeInteger(maxSummary) || maxSummary < 1) throw new TypeError('Invalid dropdown summary limit');
    const labels = [...select.labels].map(label => ({ label, for: label.getAttribute('for') }));
    const name = select.getAttribute('aria-label') || (select.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => { const node = doc.getElementById(id); return node ? labelText(node) : ''; }).filter(Boolean).join(' ')
        || labels.map(item => labelText(item.label)).filter(Boolean).join(' ') || options.label || select.title;
    if (typeof name !== 'string' || !name.trim()) throw new TypeError('LQ dropdown needs an accessible name');
    let id; do { id = `lq-dropdown-${doc[IDS] = (doc[IDS] || 0) + 1}`; } while (doc.getElementById(id));
    const original = new Map(['tabindex', 'aria-hidden'].map(key => [key, select.getAttribute(key)]));
    const hadClass = select.classList.contains('lq-selection-native');
    const system = getLayerSystem(doc), listeners = [], optionIds = new WeakMap(), ownedOptions = new Set();
    const wrapper = doc.createElement('div'); wrapper.className = 'lq-dropdown';
    const trigger = doc.createElement('button'); trigger.type = 'button'; trigger.className = 'lq-control lq-dropdown__trigger'; trigger.id = id;
    trigger.setAttribute('aria-haspopup', 'listbox'); trigger.setAttribute('aria-expanded', 'false');
    const valueNode = doc.createElement('span'); valueNode.className = 'lq-dropdown__value';
    const chevron = createIcon('chevron-down', doc); chevron.classList.add('lq-dropdown__chevron');
    trigger.append(valueNode, chevron); wrapper.append(trigger);
    const popup = doc.createElement('div'); popup.className = 'lq-selection__popup lq-dropdown__popup lq-glass'; popup.setAttribute('role', 'presentation'); popup.hidden = true;
    popup.dataset.lqMaterial = 'raised'; popup.dataset.lqComponent = 'popover';
    const search = searchable ? doc.createElement('input') : null;
    if (search) { search.type = 'text'; search.className = 'lq-input lq-dropdown__search'; search.autocomplete = 'off'; search.placeholder = '输入以筛选…'; search.setAttribute('aria-label', `筛选${name}`); popup.append(search); }
    const listbox = doc.createElement('div'); listbox.className = 'lq-selection__list lq-dropdown__list'; listbox.id = `${id}-list`; listbox.setAttribute('role', 'listbox'); listbox.setAttribute('aria-label', name);
    const status = doc.createElement('p'); status.className = 'lq-selection__status'; status.id = `${id}-status`; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    popup.append(listbox); wrapper.append(status); trigger.setAttribute('aria-controls', listbox.id); search?.setAttribute('aria-controls', listbox.id);
    let handle = null, disposed = false, rendering = false, composing = false, generation = 0, opening = 0, queryText = '', requestedText = null, resultOptions = null, activeOption = null, state = 'ready', errorText = '', buffer = '', searchTimer;
    const listen = (node, event, callback, opts) => { node.addEventListener(event, callback, opts); listeners.push(() => node.removeEventListener(event, callback, opts)); };
    const disabled = option => option.disabled || option.closest('optgroup')?.disabled;
    const alive = () => !disposed && select.isConnected && !select.matches(':disabled') && !wrapper.hidden;
    const present = () => Boolean(handle && !['closed', 'destroyed'].includes(handle.state));
    const isOpen = () => Boolean(handle && ['opening', 'open', 'checking'].includes(handle.state));
    const optionId = option => { if (!optionIds.has(option)) optionIds.set(option, `${id}-option-${doc[IDS] = (doc[IDS] || 0) + 1}`); return optionIds.get(option); };
    const visibleOptions = () => [...select.options].filter(option => !option.hidden && !option.closest('optgroup')?.hidden && (resultOptions ? resultOptions.has(option) : !queryText || option.label.toLocaleLowerCase().includes(queryText.toLocaleLowerCase())));
    const enabledOptions = () => visibleOptions().filter(option => !disabled(option));
    const focusTarget = () => search || (select.multiple ? listbox : trigger);
    function summary() {
        const chosen = [...select.selectedOptions];
        if (!chosen.length) return { text: placeholder, empty: true };
        if (!select.multiple) return { text: chosen[0].label || placeholder, empty: chosen[0].value === '' };
        const shown = chosen.slice(0, maxSummary).map(option => option.label).join('、');
        return { text: chosen.length > maxSummary ? `${shown} 等 ${chosen.length} 项` : shown, empty: false };
    }
    function paintActive(scroll = false) {
        const target = focusTarget();
        if (isOpen() && activeOption && visibleOptions().includes(activeOption) && !disabled(activeOption)) target.setAttribute('aria-activedescendant', optionId(activeOption));
        else target.removeAttribute('aria-activedescendant');
        for (const row of listbox.querySelectorAll('[role="option"]')) row.toggleAttribute('data-active', row.id === target.getAttribute('aria-activedescendant'));
        if (scroll) doc.getElementById(target.getAttribute('aria-activedescendant'))?.scrollIntoView?.({ block: 'nearest' });
    }
    function render() {
        if (disposed || rendering) return; rendering = true;
        if (!select.classList.contains('lq-selection-native')) select.classList.add('lq-selection-native');
        if (wrapper.parentNode !== select.parentNode) select.after(wrapper);
        const disabledNow = select.matches(':disabled'), multiple = select.multiple;
        const sourceStyle = view.getComputedStyle(select);
        wrapper.hidden = select.hidden || sourceStyle.display === 'none' || sourceStyle.visibility === 'hidden' || sourceStyle.visibility === 'collapse';
        wrapper.classList.toggle('lq-dropdown--multi', multiple);
        for (const size of ['sm', 'md', 'lg']) trigger.classList.toggle(`lq-control--${size}`, select.classList.contains(`lq-control--${size}`));
        trigger.disabled = disabledNow; trigger.setAttribute('aria-disabled', String(disabledNow));
        const { text, empty } = summary(); valueNode.textContent = text; trigger.title = text; wrapper.classList.toggle('is-empty', empty);
        trigger.setAttribute('aria-label', multiple || searchable ? `${name}：${text}` : name);
        if (!multiple && !searchable) { trigger.setAttribute('role', 'combobox'); trigger.setAttribute('aria-required', String(select.required)); }
        else { trigger.removeAttribute('role'); trigger.removeAttribute('aria-required'); }
        listbox.setAttribute('aria-multiselectable', String(multiple)); listbox.setAttribute('aria-required', String(select.required)); listbox.tabIndex = multiple && !search ? 0 : -1;
        if (search) { search.disabled = disabledNow; search.setAttribute('role', multiple ? 'searchbox' : 'combobox'); search.setAttribute('aria-autocomplete', 'list'); if (!multiple) search.setAttribute('aria-expanded', String(isOpen())); else search.removeAttribute('aria-expanded'); }
        const description = [select.getAttribute('aria-describedby'), status.id].filter(Boolean).join(' '); trigger.setAttribute('aria-describedby', description);
        const invalid = select.getAttribute('aria-invalid') === 'true' || (state === 'invalid' && !select.validity.valid);
        if (invalid) trigger.setAttribute('aria-invalid', 'true'); else trigger.removeAttribute('aria-invalid');
        if (select.hasAttribute('aria-errormessage')) trigger.setAttribute('aria-errormessage', select.getAttribute('aria-errormessage')); else trigger.removeAttribute('aria-errormessage');
        if ((disabledNow || wrapper.hidden) && present()) void close(disabledNow ? 'disabled' : 'hidden');
        listbox.replaceChildren();
        const groups = new Map();
        for (const option of visibleOptions()) {
            let host = listbox; const group = option.closest('optgroup');
            if (group) {
                if (!groups.has(group)) {
                    const section = doc.createElement('div'); section.className = 'lq-dropdown__group'; section.setAttribute('role', 'group'); section.setAttribute('aria-label', group.label);
                    const label = doc.createElement('div'); label.className = 'lq-dropdown__group-label'; label.setAttribute('aria-hidden', 'true'); label.textContent = group.label; section.append(label); listbox.append(section); groups.set(group, section);
                }
                host = groups.get(group);
            }
            const row = doc.createElement('div'); row.className = 'lq-selection__option lq-dropdown__option'; row.id = optionId(option); row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(option.selected));
            if (disabled(option)) row.setAttribute('aria-disabled', 'true');
            if (multiple) { const box = doc.createElement('span'); box.className = 'lq-dropdown__check'; box.setAttribute('aria-hidden', 'true'); row.append(box); }
            const label = doc.createElement('span'); label.className = 'lq-dropdown__option-label'; label.textContent = option.label; row.append(label);
            if (option.dataset.hint) { const hint = doc.createElement('small'); hint.className = 'lq-dropdown__option-hint'; hint.textContent = option.dataset.hint; row.append(hint); }
            host.append(row);
        }
        status.textContent = invalid ? (select.validationMessage || '请检查所选内容。') : state === 'error' ? (errorText || '查找失败，请重试。') : state === 'loading' ? '正在查找…' : listbox.children.length ? '' : '没有匹配选项';
        listbox.setAttribute('aria-busy', String(state === 'loading')); paintActive(); rendering = false;
    }
    function refresh() { if (!disposed) nativeObserver.refresh(); }
    function invalidate() { generation++; requestedText = null; state = 'ready'; errorText = ''; clearTimeout(searchTimer); buffer = ''; }
    function afterClose() {
        if (disposed) return;
        trigger.setAttribute('aria-expanded', 'false'); wrapper.classList.remove('is-open'); activeOption = null; queryText = ''; resultOptions = null; wrapper.append(status);
        trigger.removeAttribute('aria-activedescendant'); search?.removeAttribute('aria-activedescendant'); if (search) search.value = ''; render();
    }
    function open() {
        if (!alive()) return null; opening++;
        if (handle && ['opening', 'open'].includes(handle.state)) { render(); return handle; }
        const host = system.getPortalHost({ trigger, parentLayer: options.parentLayer }); if (popup.parentNode !== host) host.append(popup); popup.append(status);
        trigger.setAttribute('aria-expanded', 'true'); wrapper.classList.add('is-open');
        activeOption = [...select.selectedOptions].find(option => !disabled(option)) || enabledOptions()[0] || null; render();
        handle = system.open(popup, { type: 'popover', modality: 'non-modal', trigger, owner: select, anchor: trigger, ...(options.parentLayer ? { parentLayer: options.parentLayer } : {}),
            initialFocus: () => focusTarget(), returnFocus: true, onCloseRequested: invalidate,
            onReturnFocus: event => { if (handle?.closeReason === 'tab') event.preventDefault(); },
            onClose: () => { popup.remove(); afterClose(); }, onDestroy: () => destroy() });
        if (search && !select.multiple) search.setAttribute('aria-expanded', 'true'); paintActive(true); return handle;
    }
    function close(reason = 'programmatic') {
        invalidate();
        if (!present()) { afterClose(); return Promise.resolve(true); }
        return system.close(handle, reason);
    }
    function query(value) {
        if (!alive()) return null;
        queryText = string(value); requestedText = queryText; generation++; resultOptions = null; activeOption = null; state = options.onQuery ? 'loading' : 'ready'; errorText = ''; if (search) search.value = queryText; render();
        const ticket = { query: queryText, generation };
        if (options.onQuery) {
            const fail = error => { if (!disposed && ticket.generation === generation) { state = 'error'; render(); try { options.onError?.(error); } catch { /* Consumer errors cannot retain resources. */ } } };
            try { Promise.resolve(options.onQuery(ticket)).catch(fail); } catch (error) { fail(error); }
        }
        return ticket;
    }
    function setResults(result) {
        if (!alive() || !result || result.query !== queryText || result.generation !== generation) return false;
        const nextResult = dropdownResults(result);
        if (nextResult.options) {
            const selected = new Set(select.selectedOptions), byValue = new Map([...select.options].map(option => [option.value, option])), next = new Set();
            for (const item of nextResult.options) {
                let option = byValue.get(item.value);
                if (!option) { option = doc.createElement('option'); option.value = item.value; ownedOptions.add(option); select.append(option); }
                option.textContent = item.label; option.label = item.label; option.disabled = item.disabled;
                if (item.hint) option.dataset.hint = item.hint; else delete option.dataset.hint;
                next.add(option);
            }
            for (const option of [...ownedOptions]) if (!next.has(option) && !option.selected && !option.defaultSelected) { option.remove(); ownedOptions.delete(option); }
            for (const option of select.options) option.selected = selected.has(option);
            if (!select.multiple && !selected.size) select.selectedIndex = -1;
            resultOptions = next;
        }
        state = nextResult.status || 'ready'; errorText = nextResult.message || ''; activeOption = null; refresh(); return true;
    }
    function commit(option) {
        if (!alive() || !option || disabled(option)) return;
        const priorOpening = opening, priorGeneration = generation, before = [...select.options].map(item => item.selected).join(',');
        if (select.multiple) option.selected = !option.selected; else option.selected = true;
        activeOption = option; state = 'ready'; render();
        if (before !== [...select.options].map(item => item.selected).join(',')) { select.dispatchEvent(new view.Event('input', { bubbles: true })); if (select.isConnected) select.dispatchEvent(new view.Event('change', { bubbles: true })); }
        if (!select.multiple && !disposed && priorOpening === opening && priorGeneration === generation) void close('selection');
    }
    function move(step) {
        const values = enabledOptions(); if (!values.length) return;
        const index = values.indexOf(activeOption);
        activeOption = step === 'first' ? values[0] : step === 'last' ? values.at(-1) : values[Math.max(0, Math.min(values.length - 1, index < 0 ? (step > 0 ? 0 : values.length - 1) : index + step))];
        paintActive(true);
    }
    function destroy() {
        if (disposed) return; disposed = true; invalidate(); nativeObserver.destroy(); for (const remove of listeners) remove(); handle?.destroy(); popup.remove(); wrapper.remove();
        for (const [attr, value] of original) value === null ? select.removeAttribute(attr) : select.setAttribute(attr, value);
        if (!hadClass) select.classList.remove('lq-selection-native');
        for (const item of labels) if (item.label.getAttribute('for') === trigger.id) item.for === null ? item.label.removeAttribute('for') : item.label.setAttribute('for', item.for);
        bindings.delete(select);
    }
    const keys = event => {
        if (event.defaultPrevented || composing || event.isComposing || event.keyCode === 229 || !alive()) return;
        if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); if (!isOpen()) open(); if (!event.altKey) move(event.key === 'ArrowDown' ? 1 : -1); }
        else if (['Home', 'End'].includes(event.key) && isOpen() && event.target !== search) { event.preventDefault(); move(event.key === 'Home' ? 'first' : 'last'); }
        else if ((event.key === 'Enter' || (event.key === ' ' && event.target !== search)) && isOpen()) { event.preventDefault(); if (activeOption) commit(activeOption); }
        else if (event.key === 'Tab' && isOpen()) { if (event.target !== trigger) trigger.focus(); void close('tab'); }
        else if (select.multiple && isOpen() && event.target !== search && event.key.toLowerCase() === 'a' && (event.ctrlKey || event.metaKey)) {
            event.preventDefault(); const changed = enabledOptions().some(option => !option.selected); for (const option of enabledOptions()) option.selected = true; render();
            if (changed) { select.dispatchEvent(new view.Event('input', { bubbles: true })); if (select.isConnected) select.dispatchEvent(new view.Event('change', { bubbles: true })); }
        } else if (!search && event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey && event.key !== ' ') {
            event.preventDefault(); if (!isOpen()) open(); clearTimeout(searchTimer); buffer += event.key.toLocaleLowerCase();
            const values = enabledOptions(), index = values.indexOf(activeOption);
            activeOption = [...values.slice(index + 1), ...values.slice(0, index + 1)].find(option => option.label.toLocaleLowerCase().startsWith(buffer)) || activeOption;
            paintActive(true); searchTimer = setTimeout(() => { buffer = ''; }, 500);
        }
    };
    listen(trigger, 'click', () => { if (alive()) { if (isOpen()) void close('button'); else open(); } });
    listen(trigger, 'keydown', keys); listen(listbox, 'keydown', keys);
    listen(listbox, 'pointerdown', event => { if (event.button === 0) event.preventDefault(); });
    listen(listbox, 'click', event => { const row = event.target.closest('[role="option"]'); const option = [...select.options].find(item => optionId(item) === row?.id); if (option) commit(option); });
    if (search) {
        listen(search, 'keydown', keys); listen(search, 'compositionstart', () => { composing = true; });
        listen(search, 'compositionend', () => { composing = false; query(search.value); });
        listen(search, 'input', () => { if (!composing && search.value !== requestedText) query(search.value); });
    }
    listen(select, 'change', refresh); listen(select, 'input', refresh); listen(select, 'focus', () => trigger.focus());
    listen(select, 'invalid', event => { event.preventDefault(); state = 'invalid'; render(); trigger.focus({ preventScroll: true }); trigger.scrollIntoView({ block: 'nearest' }); });
    const nativeObserver = observeNativeSelect(select, { onRefresh: render, onDetach: destroy, onReset: () => { invalidate(); queryText = ''; resultOptions = null; activeOption = null; void close('reset'); } });
    for (const item of labels) item.label.htmlFor = trigger.id;
    select.classList.add('lq-selection-native'); select.tabIndex = -1; select.setAttribute('aria-hidden', 'true'); select.after(wrapper);
    const binding = { select, trigger, wrapper, popup, listbox, search, open, close, refresh, query, setResults, destroy, get handle() { return handle; } };
    bindings.set(select, binding); refresh(); return binding;
}

/** Explicit owner call after creating DOM; includes root itself when a select. */
export function enhanceDropdowns(root = document) {
    const nodes = [...(root.matches?.('select[data-lq-dropdown]') ? [root] : []), ...root.querySelectorAll('select[data-lq-dropdown]')];
    return nodes.filter(automatic).flatMap(select => {
        try { return [bindDropdown(select)]; }
        catch (error) { console.warn('LQ dropdown declaration could not initialize', error.message); return []; }
    });
}

/** One initial declaration pass and delegated activation listeners. Dynamic
 * owners can enhance eagerly; a newly inserted declared select is caught before
 * the browser opens its native picker. No DOM replacement or global rescan. */
export function installDropdowns(doc = document) {
    if (doc[INSTALL]) return doc[INSTALL];
    let disposed = false, pendingPointer = null;
    const enhance = select => {
        if (!automatic(select)) return null;
        try { return bindDropdown(select); } catch (error) { console.warn('LQ dropdown declaration could not initialize', error.message); return null; }
    };
    const start = () => { if (!disposed) for (const select of doc.querySelectorAll('select[data-lq-dropdown]')) enhance(select); };
    const activate = event => {
        if (event.type === 'pointerdown') pendingPointer = null;
        const select = event.target?.closest?.('select[data-lq-dropdown]');
        if (!select || !automatic(select)) return;
        const binding = enhance(select); if (!binding || select.matches(':disabled')) return;
        if (event.type === 'pointerdown' && event.button === 0) {
            // The source becomes visually hidden during this gesture. Consume its
            // eventual click once, then open: otherwise a wrapping label forwards
            // a second click or the layer mistakes the original source for outside.
            event.preventDefault(); binding.trigger.focus(); pendingPointer = binding;
        }
        else binding.trigger.focus();
    };
    const activateClick = event => {
        const binding = pendingPointer; pendingPointer = null;
        if (!binding) return;
        event.preventDefault(); event.stopPropagation(); binding.open();
    };
    const cancelPointer = () => { pendingPointer = null; };
    doc.addEventListener('pointerdown', activate, true); doc.addEventListener('focusin', activate, true);
    doc.addEventListener('click', activateClick, true); doc.addEventListener('pointercancel', cancelPointer, true);
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start, { once: true }); else start();
    const api = { enhance: enhanceDropdowns, destroy() {
        if (disposed) return; disposed = true; doc.removeEventListener('DOMContentLoaded', start); doc.removeEventListener('pointerdown', activate, true); doc.removeEventListener('focusin', activate, true); delete doc[INSTALL];
        doc.removeEventListener('click', activateClick, true); doc.removeEventListener('pointercancel', cancelPointer, true); pendingPointer = null;
    } };
    doc[INSTALL] = api; return api;
}

export function getDropdown(select) { return select?.ownerDocument?.[BINDINGS]?.get(select) || null; }
