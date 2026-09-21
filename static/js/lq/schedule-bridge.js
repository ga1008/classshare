import { getLayerSystem } from './layer.js';

const connections = new WeakMap();

/** The deck keeps its own focus, keys and animation. Only its explicit overlay
 * state is registered; no DOM scan, synthesized event or second lock is used. */
export function connectScheduleLayer(deck) {
    if (!deck?.overlay) return null;
    if (connections.has(deck)) return connections.get(deck);
    const overlay = deck.overlay;
    const root = overlay.getRoot();
    const layers = getLayerSystem(root.ownerDocument);
    let disposed = false;
    let placement = null;
    let nativeHost = null;
    let registration;
    let disconnect;
    const restore = () => {
        nativeHost?.removeEventListener('close', parentClosed);
        nativeHost = null;
        if (placement) {
            const { parent, next } = placement;
            placement = null;
            parent.insertBefore(root, next?.parentNode === parent ? next : null);
        }
    };
    const parentClosed = () => overlay.dismissTop('parent-destroyed');
    const beforeOpen = () => {
        if (disposed) return;
        const host = layers.getPortalHost({ trigger: overlay.getTrigger() });
        placement ||= { parent: root.parentNode, next: root.nextSibling };
        nativeHost?.removeEventListener('close', parentClosed);
        host.append(root);
        nativeHost = host.closest('dialog[open]');
        nativeHost?.addEventListener('close', parentClosed);
    };
    const onStateChange = () => {
        if (disposed) return;
        registration?.refresh();
        if (!overlay.isPresent()) restore();
    };
    const connection = { destroy() {
        if (disposed) return;
        disposed = true;
        registration?.destroy();
        disconnect?.();
        restore();
        connections.delete(deck);
    } };
    disconnect = overlay.connect({ beforeOpen, onStateChange,
        isTop: () => !disposed && Boolean(registration?.isTop()), onDestroy: connection.destroy });
    registration = layers.registerExternal({
        mode: 'ordered', owner: overlay.getOwner, root: overlay.getRoot, trigger: overlay.getTrigger,
        isOpen: overlay.isExpanded, isPresent: overlay.isPresent, isActive: overlay.isPresent,
        dismissTop: overlay.dismissTop,
    });
    connections.set(deck, connection);
    if (overlay.isExpanded()) { beforeOpen(); onStateChange(); }
    return connection;
}
