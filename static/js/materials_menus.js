import { bindMenu, createMenu } from './lq/menus.js';

/** Domain actions and permissions stay with the library; LQ owns the menu. */
export function createMaterialMenu(trigger, { label, items, onAction, onError }) {
    const root = createMenu({ label, items: items.map(({ action, ...item }) => ({ id: action, ...item })) });
    return bindMenu(trigger, root, { placement: 'bottom-end', onAction, onError });
}

/** Keep authored IDs and native file inputs. File selection needs the original
 * user gesture, before the shared menu's asynchronous exit/replay. */
export function bindMaterialHeaderMenu(trigger, root, { fileButton, fileInput } = {}) {
    if (!trigger || !root) return null;
    const pickFile = event => {
        if (!root.hidden && fileButton && !fileButton.disabled && fileButton.contains(event.target)) fileInput?.click();
    };
    root.addEventListener('click', pickFile, true);
    const binding = bindMenu(trigger, root, { nativeActions: true, placement: 'bottom-end' });
    return { ...binding, destroy() { root.removeEventListener('click', pickFile, true); binding.destroy(); } };
}
