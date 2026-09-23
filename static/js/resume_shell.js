import { getLayerSystem } from './lq/layer.js';

// The resume console has its own layout; focus, Escape, backdrop dismissal and
// scroll locking belong to the same coordinator as the rest of the product.
const root = document.getElementById('rzSidebarLayer');
const sidebar = document.getElementById('rzSidebar');
const trigger = document.getElementById('rzSidebarToggle');
const mobile = matchMedia('(max-width: 768px)');
const layers = getLayerSystem();
let handle;
const closed = () => {
    handle = null;
    root.classList.remove('is-open');
    root.hidden = mobile.matches;
    trigger.setAttribute('aria-expanded', 'false');
};
function sync() {
    if (handle) handle.destroy();
    closed();
}
trigger.addEventListener('click', () => {
    if (!mobile.matches) return;
    if (handle) { void layers.close(handle, 'toggle'); return; }
    root.classList.add('is-open');
    trigger.setAttribute('aria-expanded', 'true');
    handle = layers.open(root, { type: 'drawer', surface: sidebar, trigger,
        onClose: closed, onDestroy: closed });
});
mobile.addEventListener('change', sync);
addEventListener('pagehide', () => { if (handle) handle.destroy(); });
sync();
