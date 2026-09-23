import { getLayerSystem } from './lq/layer.js';

// Native details remains the no-script disclosure. Enhancement only lends its
// existing panel to the shared layer owner while open, then returns the same
// nodes (and their preference listeners/state) to their original position.
export function enhancePreferencesPanels(scope) {
    const doc = scope.ownerDocument || scope;
    const bindings = [...scope.querySelectorAll('[data-ui-preferences-details]')].map(details => {
        const trigger = details.querySelector('[data-ui-preferences-toggle]');
        const panel = details.querySelector('[data-ui-preferences-panel]');
        if (!trigger || !panel) return () => {};
        const system = getLayerSystem(doc), next = panel.nextSibling;
        const mobile = doc.defaultView.matchMedia('(max-width: 640px)');
        const originalExpanded = trigger.getAttribute('aria-expanded');
        const originalPopup = trigger.getAttribute('aria-haspopup');
        const originalPosition = ['left', 'top'].map(name => [name, panel.style.getPropertyValue(name), panel.style.getPropertyPriority(name)]);
        let handle = null, disposed = false;
        const restore = () => {
            handle = null;
            details.insertBefore(panel, next?.parentNode === details ? next : null);
            panel.hidden = false;
            delete panel.dataset.uiPreferencesPortal;
            for (const [name, value, priority] of originalPosition) panel.style.setProperty(name, value, priority);
        };
        const syncPosition = () => {
            if (!handle) return;
            handle.update({ anchor: mobile.matches ? null : trigger });
            if (mobile.matches) {
                panel.style.removeProperty('left');
                panel.style.removeProperty('top');
            }
        };
        const collapsed = () => {
            details.open = false;
            trigger.setAttribute('aria-expanded', 'false');
        };
        const sync = () => {
            if (disposed) return;
            trigger.setAttribute('aria-expanded', String(details.open));
            if (!details.open) { if (handle) void system.close(handle, 'disclosure'); return; }
            panel.dataset.uiPreferencesPortal = '';
            system.getPortalHost({ trigger }).append(panel);
            handle = system.open(panel, {
                type: 'popover', modality: 'non-modal', trigger, owner: details,
                anchor: mobile.matches ? null : trigger, placement: 'bottom-end',
                onCloseRequested: collapsed,
                onClose: restore,
                onDestroy: () => { collapsed(); restore(); },
            });
            syncPosition();
        };
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.setAttribute('aria-expanded', String(details.open));
        details.addEventListener('toggle', sync);
        mobile.addEventListener?.('change', syncPosition);
        if (details.open) sync();
        return () => {
            if (disposed) return;
            disposed = true;
            details.removeEventListener('toggle', sync);
            mobile.removeEventListener?.('change', syncPosition);
            handle?.destroy();
            restore();
            for (const [name, value] of [['aria-expanded', originalExpanded], ['aria-haspopup', originalPopup]])
                value === null ? trigger.removeAttribute(name) : trigger.setAttribute(name, value);
        };
    });
    return () => bindings.forEach(dispose => dispose());
}
