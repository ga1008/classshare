/** Per-owner references to authored message controls. A menu may portal its
 * original nodes; neither the native nor React owner may rediscover them only
 * through their former DOM ancestors. No listeners or global scans live here. */
export function createMessageBellTargets() {
    const cache = new WeakMap();
    return function getTargets(shell) {
        const cached = cache.get(shell);
        if (cached?.bell?.isConnected && [cached.count, cached.caption].every(node => !node || node.isConnected)) return cached;
        const trigger = shell.querySelector('[data-lq-nav-trigger][aria-controls]');
        const panel = trigger && shell.ownerDocument.getElementById(trigger.getAttribute('aria-controls'));
        const find = selector => shell.querySelector(selector) || panel?.querySelector(selector) || null;
        const nodes = { bell: find('[data-message-center-bell]'), count: find('[data-message-center-bell-count]'), caption: find('[data-message-center-bell-caption]') };
        cache.set(shell, nodes);
        return nodes;
    };
}
