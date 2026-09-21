import { attributesMarkup, escapeHtml, normalizeAttributes } from './html.js';
import { createComponent } from './components.js';
import { getLayerSystem } from './layer.js';

const BINDINGS = Symbol.for('lanshare.lq.selection-bindings.v1');
const IDS = Symbol.for('lanshare.lq.selection-identities');
export const selectionKinds = ['combobox', 'listbox'];
const string = value => { if (typeof value !== 'string') throw new TypeError('LQ selection text must be a string'); return value; };
const flag = (value, fallback = false) => { if (value === undefined) return fallback; if (typeof value !== 'boolean') throw new TypeError('Invalid LQ selection flag'); return value; };
export function selectionOptions(value) {
    if (!Array.isArray(value)) throw new TypeError('Selection options must be a list');
    const seen = new Set();
    return value.map(item => {
        if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).some(key => !['value', 'label', 'disabled'].includes(key))) throw new TypeError('Invalid selection option');
        const value = string(item.value), label = string(item.label), disabled = flag(item.disabled);
        if (seen.has(value)) throw new TypeError('Duplicate selection value'); seen.add(value);
        return { value, label, disabled };
    });
}
export function selectionProps(kind, props = {}) {
    if (!selectionKinds.includes(kind) || !props || typeof props !== 'object' || Array.isArray(props) || Object.keys(props).some(key => !['id', 'name', 'form', 'label', 'help', 'value', 'options', 'required', 'disabled', 'multiple', 'attrs'].includes(key))) throw new TypeError('Invalid LQ selection props');
    const id = string(props.id); if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id) || id.includes('--lq-')) throw new TypeError('Invalid LQ selection id');
    const label = string(props.label).trim(); if (!label) throw new TypeError('Selection needs a visible label');
    const multiple = flag(props.multiple); if (kind === 'combobox' && multiple) throw new TypeError('Editable combobox is single-value');
    const options = selectionOptions(props.options === undefined ? [] : props.options), value = props.value === undefined ? (multiple ? [] : options[0]?.value ?? '') : props.value;
    const values = multiple ? value : [value]; if (!Array.isArray(values) || values.some(v => typeof v !== 'string') || new Set(values).size !== values.length || values.some(v => !options.some(o => o.value === v)) && !(options.length === 0 && !multiple && value === '')) throw new TypeError('Selection value must match its options');
    const attrs = normalizeAttributes(props.attrs);
    for (const key of Object.keys(attrs)) if (key.startsWith('data-lq-') || ['id', 'name', 'form', 'target', 'rel', 'aria-label', 'aria-labelledby', 'aria-describedby', 'aria-invalid', 'aria-required', 'aria-disabled'].includes(key)) delete attrs[key];
    Object.assign(attrs, { id, class: 'lq-select', 'data-lq-selection': kind });
    for (const key of ['name', 'form']) if (props[key] !== undefined) attrs[key] = string(props[key]);
    if (flag(props.required)) attrs.required = ''; if (flag(props.disabled)) attrs.disabled = ''; if (multiple) attrs.multiple = '';
    if (kind === 'listbox') attrs.size = '6';
    const help = props.help === undefined ? '' : string(props.help); if (help) attrs['aria-describedby'] = `${id}--lq-help`;
    return { id, kind, label, help, attrs, options: options.map(item => ({ ...item, attrs: { value: item.value, ...(item.disabled ? { disabled: '' } : {}), ...(values.includes(item.value) ? { selected: '' } : {}) } })) };
}
export function selectionMarkup(kind, props) {
    const p = selectionProps(kind, props);
    return `<div class="lq-field"><label class="lq-field__label" for="${escapeHtml(p.id)}">${escapeHtml(p.label)}</label><select${attributesMarkup(p.attrs)}>${p.options.map(item => `<option${attributesMarkup(item.attrs)}>${escapeHtml(item.label)}</option>`).join('')}</select>${p.help ? `<p class="lq-field__help" id="${escapeHtml(p.id)}--lq-help">${escapeHtml(p.help)}</p>` : ''}</div>`;
}
export function createSelection(kind, props, doc = document) {
    const p = selectionProps(kind, props); // Validate every option before creating or moving DOM.
    const root = doc.createElement('div'); root.className = 'lq-field';
    const label = doc.createElement('label'); label.className = 'lq-field__label'; label.htmlFor = p.id; label.textContent = p.label;
    const select = doc.createElement('select'); for (const [key, value] of Object.entries(p.attrs)) select.setAttribute(key, value);
    for (const item of p.options) { const option = doc.createElement('option'); for (const [key, value] of Object.entries(item.attrs)) option.setAttribute(key, value); option.textContent = item.label; select.append(option); }
    root.append(label, select);
    if (p.help) { const help = doc.createElement('p'); help.className = 'lq-field__help'; help.id = `${p.id}--lq-help`; help.textContent = p.help; root.append(help); }
    return root;
}

/** Explicit native-select enhancement. The select is the sole form/value owner. */
export function bindSelection(select, options = {}) {
    const doc = select?.ownerDocument, view = doc?.defaultView;
    if (!doc || select.tagName !== 'SELECT' || !select.isConnected) throw new TypeError('Selection requires a connected native select');
    const mode = options.mode || select.dataset.lqSelection || 'combobox';
    if (!selectionKinds.includes(mode) || (mode === 'combobox' && select.multiple)) throw new TypeError('Invalid selection mode');
    for (const key of ['onQuery', 'onError']) if (options[key] !== undefined && typeof options[key] !== 'function') throw new TypeError(`Invalid selection ${key}`);
    const bindings = doc[BINDINGS] ||= new WeakMap(); if (bindings.has(select)) return bindings.get(select);
    const system = getLayerSystem(doc), listeners = [], ownedOptions = new Set(), optionIds = new WeakMap();
    let id; do { id = `lq-selection-${doc[IDS] = (doc[IDS] || 0) + 1}`; } while (doc.getElementById(id));
    const original = new Map(['hidden', 'tabindex', 'aria-hidden'].map(name => [name, select.getAttribute(name)]));
    const hadClass = select.classList.contains('lq-selection-native');
    const initiallyEmpty = select.options.length === 0;
    const labels = [...select.labels].map(label => ({ label, for: label.getAttribute('for') }));
    const name = select.getAttribute('aria-label') || labels.map(row => row.label.textContent.trim()).join(' ') || options.label;
    if (typeof name !== 'string' || !name.trim()) throw new TypeError('Selection needs an accessible name');
    const wrapper = doc.createElement('div'); wrapper.className = `lq-selection lq-selection--${mode}`;
    const listbox = doc.createElement('div'); listbox.className = 'lq-selection__list'; listbox.id = `${id}-list`; listbox.setAttribute('role', 'listbox'); listbox.setAttribute('aria-label', name);
    if (select.multiple) listbox.setAttribute('aria-multiselectable', 'true');
    const status = doc.createElement('p'); status.className = 'lq-selection__status'; status.id = `${id}-status`; status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const popup = doc.createElement('div'); popup.className = 'lq-selection__popup lq-glass'; popup.setAttribute('role', 'presentation'); popup.hidden = true; popup.append(listbox, status);
    const input = mode === 'combobox' ? doc.createElement('input') : null;
    let toggle = null;
    const control = input || listbox; control.id = input ? id : listbox.id;
    if (input) {
        input.type = 'text'; input.className = 'lq-input lq-selection__input'; input.autocomplete = 'off'; input.setAttribute('role', 'combobox'); input.setAttribute('aria-autocomplete', 'list'); input.setAttribute('aria-haspopup', 'listbox'); input.setAttribute('aria-controls', listbox.id); input.setAttribute('aria-expanded', 'false'); input.setAttribute('aria-label', name);
        toggle = createComponent('button', { icon: 'chevron-down', variant: 'ghost', attrs: { 'aria-label': `展开${name}` } }, doc); toggle.tabIndex = -1; toggle.classList.add('lq-selection__toggle'); wrapper.append(input, toggle, status);
    } else { listbox.tabIndex = 0; wrapper.append(listbox, status); }
    const description = select.getAttribute('aria-describedby'); control.setAttribute('aria-describedby', [description, status.id].filter(Boolean).join(' '));
    let handle = null, disposed = false, composing = false, generation = 0, opening = 0, queryText = '', requestedText = null, activeOption = null, resultOptions = null, state = 'ready', errorText = '', previousDisabled = select.matches(':disabled'), buffer = '', searchTimer = null, resetTimer = null, rendering = false;
    const disabled = option => option.disabled || option.closest('optgroup')?.disabled;
    const listen = (node, event, callback, opts) => { node.addEventListener(event, callback, opts); listeners.push(() => node.removeEventListener(event, callback, opts)); };
    const clearSearch = () => { clearTimeout(searchTimer); searchTimer = null; buffer = ''; };
    const selectedText = () => select.selectedOptions[0]?.label || '';
    const alive = () => !disposed && select.isConnected && !select.matches(':disabled');
    const optionId = option => { if (!optionIds.has(option)) optionIds.set(option, `${id}-option-${doc[IDS] = (doc[IDS] || 0) + 1}`); return optionIds.get(option); };
    const visibleOptions = () => [...select.options].filter(option => !option.hidden && (resultOptions ? resultOptions.has(option) : !queryText || option.label.toLocaleLowerCase().includes(queryText.toLocaleLowerCase())));
    const enabledOptions = () => visibleOptions().filter(option => !disabled(option));
    function paintActive() {
        if (activeOption && visibleOptions().includes(activeOption) && !disabled(activeOption)) control.setAttribute('aria-activedescendant', optionId(activeOption)); else { activeOption = null; control.removeAttribute('aria-activedescendant'); }
        for (const row of listbox.children) { const active = row.id === control.getAttribute('aria-activedescendant'); row.toggleAttribute('data-active', active); if (input) row.setAttribute('aria-selected', String(active)); }
        const current = doc.getElementById(control.getAttribute('aria-activedescendant')); current?.scrollIntoView?.({ block: 'nearest' });
    }
    function render() {
        if (disposed || rendering) return; rendering = true;
        const disabledNow = select.matches(':disabled');
        if (disabledNow !== previousDisabled) { previousDisabled = disabledNow; generation++; requestedText = null; if (disabledNow) { state = 'ready'; void close('disabled'); } }
        if (mode === 'combobox' && select.multiple) { rendering = false; destroy(); return; }
        control.setAttribute('aria-required', String(select.required)); control.setAttribute('aria-disabled', String(disabledNow));
        if (input) { input.disabled = disabledNow; toggle.disabled = disabledNow; } else { listbox.tabIndex = disabledNow ? -1 : 0; listbox.setAttribute('aria-multiselectable', String(select.multiple)); }
        if (select.validity.valid) control.removeAttribute('aria-invalid');
        listbox.replaceChildren();
        for (const option of visibleOptions()) {
            const row = doc.createElement('div'); row.className = 'lq-selection__option'; row.id = optionId(option); row.setAttribute('role', 'option'); row.setAttribute('aria-selected', String(option.selected)); if (disabled(option)) row.setAttribute('aria-disabled', 'true'); row.textContent = option.label; listbox.append(row);
        }
        control.setAttribute('aria-busy', String(state === 'loading'));
        status.textContent = state === 'error' ? (errorText || '查找失败，请重试。') : state === 'loading' ? '正在查找…' : listbox.children.length ? '' : '没有匹配选项';
        if (state === 'invalid') status.textContent = select.validationMessage;
        paintActive(); rendering = false;
    }
    function refresh() {
        if (disposed) return; render();
        if (input && !composing && (!handle || ['closed', 'destroyed'].includes(handle.state))) input.value = selectedText();
    }
    function invalidate() { generation++; requestedText = null; state = 'ready'; errorText = ''; clearSearch(); }
    function afterClose() { if (disposed) return; input?.setAttribute('aria-expanded', 'false'); activeOption = null; queryText = ''; resultOptions = null; if (input) input.value = selectedText(); render(); control.removeAttribute('aria-activedescendant'); }
    function open() {
        if (!alive() || !input) return null;
        opening++;
        const host = system.getPortalHost({ trigger: input, parentLayer: options.parentLayer }); if (popup.parentNode !== host) host.append(popup);
        input.setAttribute('aria-expanded', 'true'); render();
        handle = system.open(popup, { type: 'popover', modality: 'non-modal', trigger: input, owner: select, anchor: wrapper, ...(options.parentLayer ? { parentLayer: options.parentLayer } : {}),
            initialFocus: false, returnFocus: false, onCloseRequested: invalidate, onClose: () => { popup.remove(); afterClose(); }, onDestroy: () => destroy() });
        return handle;
    }
    function close(reason = 'programmatic') { invalidate(); if (!handle || ['closed', 'destroyed'].includes(handle.state)) { afterClose(); return Promise.resolve(true); } return system.close(handle, reason); }
    function query(value) {
        if (!alive()) return null;
        queryText = string(value); requestedText = queryText; generation++; resultOptions = null; activeOption = null; state = options.onQuery ? 'loading' : 'ready'; errorText = ''; if (input) input.value = queryText; render();
        const ticket = { query: queryText, generation };
        if (options.onQuery) {
            const fail = error => { if (!disposed && ticket.generation === generation) { state = 'error'; render(); try { options.onError?.(error); } catch { /* Consumer callbacks cannot retain resources. */ } } };
            try { Promise.resolve(options.onQuery(ticket)).catch(fail); } catch (error) { fail(error); }
        }
        return ticket;
    }
    function setResults(result) {
        if (!alive() || !result || result.query !== queryText || result.generation !== generation) return false;
        if (Object.keys(result).some(key => !['query', 'generation', 'options', 'status', 'message'].includes(key))) throw new TypeError('Invalid selection result');
        const resultState = result.status === undefined ? 'ready' : result.status;
        if (!['ready', 'loading', 'error'].includes(resultState)) throw new TypeError('Invalid selection result state');
        const values = result.options === undefined ? null : selectionOptions(result.options); // Full validation precedes native changes.
        if (result.message !== undefined) string(result.message);
        if (values) {
            const selected = new Set(select.selectedOptions);
            const byValue = new Map([...select.options].map(option => [option.value, option]));
            const next = new Set();
            for (const item of values) {
                let option = byValue.get(item.value);
                if (!option) { option = doc.createElement('option'); option.value = item.value; ownedOptions.add(option); select.append(option); }
                option.textContent = item.label; option.disabled = item.disabled; next.add(option);
            }
            for (const option of [...ownedOptions]) if (!next.has(option) && !option.selected && !option.defaultSelected) { option.remove(); ownedOptions.delete(option); }
            for (const option of select.options) option.selected = selected.has(option);
            if (!select.multiple && !selected.size) select.selectedIndex = -1;
            resultOptions = next;
        }
        state = resultState; errorText = result.message || ''; activeOption = null; render(); return true;
    }
    function commit(option, toggleValue = false) {
        if (!alive() || !option || disabled(option)) return;
        const priorOpening = opening, priorGeneration = generation;
        const before = [...select.options].map(item => item.selected).join(',');
        if (select.multiple && toggleValue) option.selected = !option.selected; else { for (const item of select.options) item.selected = item === option; }
        activeOption = option; state = 'ready'; render(); if (input) input.value = selectedText();
        if (before !== [...select.options].map(item => item.selected).join(',')) { select.dispatchEvent(new view.Event('input', { bubbles: true })); if (!disposed) select.dispatchEvent(new view.Event('change', { bubbles: true })); }
        if (input && !disposed && priorOpening === opening && priorGeneration === generation) void close('selection');
    }
    function move(edge) {
        const list = enabledOptions(); if (!list.length) return;
        const index = list.indexOf(activeOption);
        activeOption = edge === 'first' ? list[0] : edge === 'last' ? list.at(-1) : list[Math.max(0, Math.min(list.length - 1, index < 0 ? edge > 0 ? 0 : list.length - 1 : index + edge))];
        if (!input && !select.multiple) commit(activeOption); else paintActive();
    }
    function destroy() {
        if (disposed) return; disposed = true; invalidate(); clearTimeout(resetTimer); observer.disconnect(); for (const remove of listeners) remove(); handle?.destroy(); popup.remove(); wrapper.remove();
        for (const [name, value] of original) value === null ? select.removeAttribute(name) : select.setAttribute(name, value);
        if (!hadClass) select.classList.remove('lq-selection-native');
        for (const item of labels) if (item.label.getAttribute('for') === control.id) item.for === null ? item.label.removeAttribute('for') : item.label.setAttribute('for', item.for);
        bindings.delete(select);
    }
    listen(control, 'keydown', event => {
        if (event.defaultPrevented || composing || event.isComposing || event.keyCode === 229 || !alive()) return;
        if (['ArrowDown', 'ArrowUp'].includes(event.key)) { event.preventDefault(); const wasOpen = handle && !['closed', 'destroyed'].includes(handle.state); if (input && !wasOpen) { open(); move(event.key === 'ArrowDown' ? 'first' : 'last'); } else move(event.key === 'ArrowDown' ? 1 : -1); }
        else if (['Home', 'End'].includes(event.key) && (!input || (!event.ctrlKey && !event.metaKey && handle && ['opening', 'open'].includes(handle.state)))) { event.preventDefault(); move(event.key === 'Home' ? 'first' : 'last'); }
        else if ((event.key === 'Enter' || (!input && event.key === ' ')) && activeOption) { event.preventDefault(); commit(activeOption, select.multiple); }
        else if (event.key === 'Tab' && input) { void close('tab'); }
        else if (!input && select.multiple && event.key.toLowerCase() === 'a' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); const changed = enabledOptions().some(option => !option.selected); for (const option of enabledOptions()) option.selected = true; render(); if (changed) { select.dispatchEvent(new view.Event('input', { bubbles: true })); select.dispatchEvent(new view.Event('change', { bubbles: true })); } }
        else if (!input && event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) { event.preventDefault(); clearTimeout(searchTimer); buffer += event.key.toLocaleLowerCase(); const list = enabledOptions(), index = list.indexOf(activeOption); activeOption = [...list.slice(index + 1), ...list.slice(0, index + 1)].find(option => option.label.toLocaleLowerCase().startsWith(buffer)) || activeOption; if (!select.multiple && activeOption) commit(activeOption); else paintActive(); searchTimer = setTimeout(clearSearch, 500); }
    });
    listen(listbox, 'pointerdown', event => { if (input && event.button === 0) event.preventDefault(); });
    listen(listbox, 'click', event => { const row = event.target.closest('[role="option"]'); const option = [...select.options].find(item => optionId(item) === row?.id); if (option) commit(option, select.multiple); });
    if (input) {
        listen(input, 'compositionstart', () => { composing = true; });
        listen(input, 'compositionend', () => { composing = false; query(input.value); open(); });
        listen(input, 'input', () => { if (!composing && input.value !== requestedText) { query(input.value); open(); } });
        listen(input, 'blur', event => { if (!popup.contains(event.relatedTarget) && event.relatedTarget !== toggle) void close('blur'); });
        listen(toggle, 'pointerdown', event => { event.preventDefault(); });
        listen(toggle, 'click', () => { if (!alive()) return; input.focus(); if (handle && ['opening', 'open'].includes(handle.state)) void close('button'); else { queryText = ''; resultOptions = null; open(); } });
    } else listen(listbox, 'focus', () => { if (!activeOption) activeOption = [...select.selectedOptions].find(option => !disabled(option)) || enabledOptions()[0]; paintActive(); });
    listen(select, 'change', refresh); listen(select, 'input', refresh); listen(select, 'focus', () => control.focus());
    listen(select, 'invalid', event => { event.preventDefault(); control.setAttribute('aria-invalid', 'true'); state = 'invalid'; render(); control.focus({ preventScroll: true }); control.scrollIntoView({ block: 'nearest' }); });
    if (select.form) listen(select.form, 'reset', event => {
        // Click-triggered reset can run its native default action after a microtask
        // checkpoint. Refresh in the next task, and never synthesize change.
        invalidate(); clearTimeout(resetTimer); resetTimer = setTimeout(() => { resetTimer = null; if (disposed || event.defaultPrevented) return; queryText = ''; resultOptions = null; activeOption = null; if (initiallyEmpty && ![...select.options].some(option => option.defaultSelected)) select.selectedIndex = -1; void close('reset'); refresh(); if (input) input.value = selectedText(); }, 0);
    });
    const observer = new view.MutationObserver(records => { if (!select.isConnected || !wrapper.isConnected) { destroy(); return; } if (records.some(record => record.target === select || select.contains(record.target) || (record.type === 'attributes' && record.target.contains(select)))) refresh(); });
    for (const item of labels) item.label.htmlFor = control.id;
    select.classList.add('lq-selection-native'); select.hidden = false; select.tabIndex = -1; select.setAttribute('aria-hidden', 'true'); select.after(wrapper);
    observer.observe(doc.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'required', 'multiple', 'selected', 'label', 'value'] });
    const binding = { select, input, listbox, control, open, close, refresh, query, setResults, destroy, get handle() { return handle; } }; bindings.set(select, binding); refresh(); return binding;
}
