/** Observe one native value owner. No prototype patches, polling or document
 * subtree observer: options and the owner's direct ancestry are sufficient. */
export function observeNativeSelect(select, { onRefresh, onReset, onDetach }) {
    const view = select.ownerDocument.defaultView;
    const hooks = new Map();
    let disposed = false, pending = false, resetTimer, form = null, ancestors = [];
    const schedule = () => {
        if (pending || disposed) return;
        pending = true;
        view.queueMicrotask(() => { pending = false; if (!disposed) sync(); });
    };
    function hook(node, property) {
        let properties = hooks.get(node);
        if (properties?.has(property)) return;
        const own = Object.getOwnPropertyDescriptor(node, property);
        if (own && !own.configurable) return;
        let prototype = node, descriptor;
        while (prototype && !descriptor) { descriptor = Object.getOwnPropertyDescriptor(prototype, property); prototype = Object.getPrototypeOf(prototype); }
        if (!descriptor?.get || !descriptor?.set) return;
        const get = function () { return descriptor.get.call(this); };
        const set = function (value) { descriptor.set.call(this, value); schedule(); };
        try {
            Object.defineProperty(node, property, { configurable: true, enumerable: descriptor.enumerable, get, set });
            if (!properties) { properties = new Map(); hooks.set(node, properties); }
            properties.set(property, { own, get, set });
        } catch { /* A host-owned descriptor remains native; refresh is explicit. */ }
    }
    function restore(node) {
        for (const [property, saved] of hooks.get(node) || []) {
            const current = Object.getOwnPropertyDescriptor(node, property);
            if (current?.get !== saved.get || current?.set !== saved.set) continue;
            if (saved.own) Object.defineProperty(node, property, saved.own); else delete node[property];
        }
        hooks.delete(node);
    }
    const reset = event => {
        clearTimeout(resetTimer);
        resetTimer = setTimeout(() => { resetTimer = null; if (!disposed && !event.defaultPrevented) { onReset?.(); sync(); } }, 0);
    };
    function topology() {
        const next = [];
        for (let node = select.parentElement; node; node = node.parentElement) next.push(node);
        if (next.length !== ancestors.length || next.some((node, index) => node !== ancestors[index])) {
            ancestors = next; observer.disconnect();
            observer.observe(select, { childList: true, subtree: true, characterData: true, attributes: true });
            for (const node of ancestors) observer.observe(node, { childList: true, ...(node.tagName === 'FIELDSET' ? { attributes: true, attributeFilter: ['disabled'] } : {}) });
        }
        if (form !== select.form) { form?.removeEventListener('reset', reset); form = select.form; form?.addEventListener('reset', reset); }
    }
    function sync() {
        if (disposed) return;
        if (!select.isConnected) { onDetach?.(); return; }
        topology();
        const options = new Set(select.options);
        for (const node of hooks.keys()) if (node.tagName === 'OPTION' && !options.has(node)) restore(node);
        for (const option of options) hook(option, 'selected');
        onRefresh?.();
    }
    const observer = new view.MutationObserver(records => {
        if (!select.isConnected) { onDetach?.(); return; }
        if (records.some(record => record.target === select || select.contains(record.target) || record.type === 'attributes'
            || [...record.removedNodes, ...record.addedNodes].some(node => node === select || node.contains?.(select)))) schedule();
    });
    hook(select, 'value'); hook(select, 'selectedIndex'); hook(select.options, 'selectedIndex');
    topology();
    for (const option of select.options) hook(option, 'selected');
    return { refresh: sync, destroy() {
        if (disposed) return; disposed = true; observer.disconnect(); clearTimeout(resetTimer); form?.removeEventListener('reset', reset);
        for (const node of [...hooks.keys()]) restore(node);
    } };
}
