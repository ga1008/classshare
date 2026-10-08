import { getLayerSystem } from './layer.js';
import { enhanceDropdowns } from './dropdown.js';

/** Explicit adapter for an existing domain root; LQ alone owns its lifecycle. */
export function createDomainModal(root, options = {}) {
    let current = null, dropdowns = [];
    const parent = root?.parentNode;
    const next = root?.nextSibling;
    const restore = () => {
        if (parent && root) parent.insertBefore(root, next?.parentNode === parent ? next : null);
    };
    return {
        get handle() { return current; },
        open(overrides = {}) {
            if (!root) return null;
            const settings = { ...options, ...overrides };
            const layers = getLayerSystem(root.ownerDocument);
            const trigger = settings.trigger || current?.trigger || root.ownerDocument.activeElement;
            root.classList.add('lq-domain-region');
            root.dataset.lqComponent ||= 'layer';
            root.dataset.lqPresence = 'domain';
            root.dataset.lqPresenceVariant = settings.type || 'modal';
            if (!current || ['closed', 'destroyed'].includes(current.state)) root.hidden = true;
            root.setAttribute('aria-hidden', 'false');
            layers.getPortalHost({ trigger, parentLayer: settings.parentLayer }).appendChild(root);
            const finish = (destroyed, ...args) => {
                const handle = args[args.length - 1];
                if (current === handle) current = null;
                root.setAttribute('aria-hidden', 'true');
                if (destroyed) {
                    dropdowns.forEach(binding => binding.destroy());
                    dropdowns = [];
                    restore();
                }
                (destroyed ? settings.onDestroy || settings.onClose : settings.onClose)?.(...args);
            };
            current = layers.open(root, {
                ...settings, type: settings.type || 'modal', trigger,
                onClose: (...args) => finish(false, ...args),
                onDestroy: (...args) => finish(true, ...args),
            });
            dropdowns = enhanceDropdowns(root);
            return current;
        },
        close(reason = 'programmatic') {
            return current ? getLayerSystem(root.ownerDocument).close(current, reason) : Promise.resolve(false);
        },
        destroy() {
            dropdowns.forEach(binding => binding.destroy());
            dropdowns = [];
            current?.destroy();
            current = null;
            restore();
        },
    };
}
