import { LQ } from './index.js';
import { applyTheme, detectCapabilities } from './theme.js';

const root = document.querySelector('[data-lq-shell-preview]');
if (root) LQ.ready(async api => {
    const [shells, forms, tables] = await Promise.all([api.load('shells'), api.load('forms'), api.load('tables')]);
    forms.enhanceForms(root);
    root.querySelectorAll('[data-lq-shell="topbar"],[data-lq-shell="sidebar"],[data-lq-editor]').forEach(node => shells.enhanceShell(node));
    root.querySelectorAll('[data-lq-table]').forEach(node => tables.enhanceTable(node));
    const dock = document.getElementById('shell-dock');
    if (dock) shells.enhanceDock(dock, { contentRoot: document.getElementById('shell-preview-main'), fallbacks: { 'preview-action': document.getElementById('shell-primary-fallback') } });
    const form = root.querySelector('[data-lq-shell-theme]');
    const updateTheme = () => applyTheme({ preferences: { ...Object.fromEntries(new FormData(form)), glass: 'tinted' }, capabilities: detectCapabilities(window) }, document.documentElement);
    form.addEventListener('submit', event => event.preventDefault());
    form.addEventListener('change', updateTheme);
    updateTheme();
    root.addEventListener('click', event => {
        const action = event.target.closest('[data-lq-command],[data-lq-page]');
        if (action) root.querySelector('[data-lq-shell-feedback]').textContent = '已操作本地示例；输入保留，未发送保存请求。';
    });
    root.dataset.lqShellReady = 'true';
});
