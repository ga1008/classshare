import { applyTheme, detectCapabilities } from './theme.js';
import LQ from './index.js';

const form = document.querySelector('[data-lq-preview-controls]');
if (form) {
    const root = document.documentElement;
    const status = form.querySelector('[data-lq-preview-status]');
    const update = () => {
        const preferences = Object.fromEntries(new FormData(form));
        applyTheme({ preferences, capabilities: detectCapabilities(window) }, root);
        status.textContent = `当前显示：${root.dataset.appearance === 'dark' ? '深色' : '浅色'} · ${root.dataset.lqGlass === 'off' ? '玻璃已关闭' : '玻璃已开启'}。仅本地预览，未保存到账户。`;
    };
    form.addEventListener('submit', (event) => event.preventDefault());
    form.addEventListener('change', update);
    for (const specimen of document.querySelectorAll('[data-probe-tone]')) {
        const tone = specimen.dataset.probeTone;
        for (const key of ['fg', 'soft', 'base', 'on-base', 'solid', 'on-solid']) {
            specimen.style.setProperty(`--preview-tone-${key}`, `var(--ls-tone-${tone}-${key})`);
        }
    }
    update();
}

LQ.ready(api => {
    const preview = document.querySelector('[data-lq-preview]');
    if (!preview || preview.dataset.lqComponentsReady === 'true') return;
    preview.dataset.lqComponentsReady = 'true';
    const status = preview.querySelector('[data-lq-demo-status]');
    preview.addEventListener('click', event => {
        const action = event.target.closest('[data-lq-demo-action]');
        if (action) status.textContent = `已操作“${action.dataset.lqDemoAction}”。这是本地预览。`;
        const filter = event.target.closest('[data-lq-demo-filter]');
        if (filter) {
            const selected = filter.getAttribute('aria-pressed') !== 'true';
            filter.setAttribute('aria-pressed', String(selected));
            filter.classList.toggle('is-selected', selected);
            status.textContent = `${filter.textContent.trim()}：${selected ? '已选中' : '已取消'}。`;
        }
        const remove = event.target.closest('[data-lq-chip-remove]');
        const tag = remove?.closest('[data-lq-demo-tag]');
        if (tag) {
            status.textContent = '已移除预览标签。刷新可恢复。';
            tag.remove();
            preview.querySelector('[data-lq-demo-filter]')?.focus();
        }
    });
    const host = preview.querySelector('[data-lq-demo-factories]');
    if (host) {
        host.append(api.button({ label: '动态按钮', icon: 'plus', attrs: { 'data-lq-demo-action': '动态按钮' } }));
        const specimen = document.createElement('div');
        specimen.innerHTML = api.html.chip({ label: '动态状态', tone: 'success' });
        host.append(...specimen.childNodes);
    }
});

LQ.ready(async api => {
    const preview = document.querySelector('[data-lq-preview]');
    if (!preview || ['loading', 'true'].includes(preview.dataset.lqInteractionsReady)) return;
    preview.dataset.lqInteractionsReady = 'loading';
    const [forms, navigation, folding, menus, tooltips, statuses, tables, selection, business, upload, workspace, content, composer, chipRows, codePreview] = await Promise.all([
        api.load('forms'), api.load('navigation'), api.load('collapsible'), api.load('menus'), api.load('tooltips'), api.load('status'), api.load('tables'), api.load('selection'), api.load('business'), api.load('upload'), api.load('workspace'), api.load('content'), api.load('composer'), api.load('chipRow'), import('../file_preview.js'), import('../ls_image_lightbox.js'),
    ]);
    forms.enhanceForms(preview);
    preview.querySelectorAll('.lq-chip-row').forEach(root => chipRows.bindChipRow(root));
    preview.querySelectorAll('.lq-prose').forEach(root => codePreview.decoratePreviewCodeBlocks(root));
    content.enhanceRow(document.getElementById('preview-swipe-row'), { onAction: () => {
        preview.querySelector('[data-lq-demo-row-status]').textContent = '已收到移除示例的操作意图；记录保留，未执行删除。';
    } });
    const composerRoot = document.getElementById('preview-composer'), composerForm = composerRoot.closest('form');
    const messageInput = composerRoot.querySelector('textarea'), fileInput = composerForm.querySelector('[data-lq-demo-composer-file]');
    const inputComposer = composer.enhanceComposer(composerRoot);
    document.getElementById('preview-composer-state').addEventListener('change', event => {
        inputComposer.set({ disabled: event.target.value === 'disabled', busy: event.target.value === 'busy' });
    });
    composerRoot.querySelector('[data-lq-composer-attachment]').addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
        inputComposer.set({ hasContent: fileInput.files.length > 0 });
        composerForm.querySelector('[data-lq-demo-composer-files]').textContent = fileInput.files.length ? [...fileInput.files].map(file => file.name).join('、') : '尚未选择附件。';
    });
    composerRoot.querySelector('[data-lq-composer-emoji]').addEventListener('click', () => {
        messageInput.setRangeText('😄', messageInput.selectionStart, messageInput.selectionEnd, 'end');
        messageInput.dispatchEvent(new Event('input', { bubbles: true })); messageInput.focus();
    });
    composerForm.addEventListener('submit', event => {
        event.preventDefault();
        composerForm.querySelector('[data-lq-demo-composer-status]').textContent = `已检查 ${new FormData(composerForm).get('content').length} 个字符、${fileInput.files.length} 个附件。草稿保留，未发送。`;
    });
    preview.querySelectorAll('[data-lq-split]').forEach(root => workspace.enhanceSplit(root));
    preview.querySelectorAll('[data-lq-tabs]').forEach(root => navigation.tabs(root));
    preview.querySelectorAll('[data-lq-collapsible]').forEach(root => folding.enhanceCollapsible(root));
    menus.bindMenu(document.getElementById('preview-menu-trigger'), document.getElementById('preview-menu'), {
        onAction: (_id, item) => { preview.querySelector('[data-lq-demo-menu-status]').textContent = `已操作：${item.textContent.trim()}。仅本地预览。`; },
    });
    tooltips.bindTooltip(document.getElementById('preview-tooltip-trigger'), document.getElementById('preview-tooltip'));
    preview.querySelectorAll('[data-lq-table]').forEach(root => tables.enhanceTable(root, { selection: 'native' }));
    preview.querySelectorAll('select[data-lq-selection]').forEach(select => selection.bindSelection(select));
    preview.addEventListener('change', event => {
        if (event.target.matches('[data-lq-select-row],[data-lq-select-all]')) {
            const checked = preview.querySelectorAll('#preview-records input[data-lq-select-row]:checked');
            preview.querySelector('[data-lq-demo-table-status]').textContent = `当前选中 ${checked.length} 项（含已锁定的示例记录）。`;
            preview.querySelector('[data-lq-demo-bulk-host]').replaceChildren(tables.createTable('bulk_bar', {
                selectedCount: checked.length, actions: [{ label: '查看选中项', variant: 'soft', attrs: { 'data-lq-demo-action': '查看选中项' } }],
            }));
        }
    });
    preview.addEventListener('click', event => {
        const control = event.target.closest('[data-lq-sort],[data-lq-page]');
        if (control) preview.querySelector('[data-lq-demo-table-status]').textContent = `已操作：${control.getAttribute('aria-label')}。列表数据保持不变，仅演示操作入口。`;
    });
    const selectionForm = preview.querySelector('[data-lq-demo-selection-form]');
    selectionForm?.addEventListener('submit', event => {
        event.preventDefault();
        const data = new FormData(selectionForm);
        preview.querySelector('[data-lq-demo-selection-status]').textContent = `已选择课程 ${data.get('course')}，资料 ${data.getAll('resources').length} 项。仅本地预览。`;
    });
    selectionForm?.addEventListener('reset', () => { preview.querySelector('[data-lq-demo-selection-status]').textContent = '已恢复初始选择。'; });
    const jobs = business.jobStatus(document.getElementById('preview-job'), { onAction: ({ generation }) => {
        preview.querySelector('[data-lq-demo-job-status]').textContent = `正在查看第 ${generation} 版示例快照，未执行任务操作。`;
    } });
    let jobGeneration = 0;
    document.getElementById('preview-job-state').addEventListener('change', event => {
        const state = event.target.value;
        jobs.set({ identity: 'preview:task', generation: ++jobGeneration, state, label: event.target.selectedOptions[0].label,
            message: state === 'superseded' ? '此示例任务已被新任务替代，旧结果不会自动应用。' : '这是状态示例，尚未执行或应用任何任务。',
            elapsed: '12 秒', progress: { value: state === 'result_ready' ? 100 : 42, label: '示例处理进度' }, actions: [{ key: 'inspect', label: '查看示例状态' }] });
    });
    const agentJob = business.jobStatus(document.getElementById('preview-agent'), { onAction: ({ generation }) => {
        preview.querySelector('[data-lq-demo-agent-status]').textContent = `已查看第 ${generation} 版助手示例快照，未执行任务。`;
    } });
    let agentGeneration = 0;
    document.getElementById('preview-agent-state').addEventListener('change', event => {
        agentJob.set({ identity: 'preview:agent', generation: ++agentGeneration, family: 'agent', state: event.target.value,
            label: event.target.selectedOptions[0].label, message: '这是状态示例，不执行确认、应用或任务请求。',
            actions: [{ key: 'inspect', label: '查看助手状态示例' }] });
    });
    const questionRoot = document.getElementById('preview-questions');
    const questionItems = [{ id: 'q1', index: 1, current: true, answered: true }, { id: 'q2', index: 2, flagged: true }, { id: 'q3', index: 3, error: true }, { id: 'q4', index: 4, pendingUpload: true }, { id: 'q5', index: 5 }, { id: 'q6', index: 6, disabled: true }];
    const questions = business.questionNavigator(questionRoot, { onSelect: ({ id, index }) => {
        for (const item of questionItems) item.current = item.id === id;
        questions.set({ label: '示例答题卡', groups: [{ id: 'preview-group', label: '第一部分', items: questionItems }] });
        preview.querySelector('[data-lq-demo-question-status]').textContent = `当前是第 ${index} 题。未切换任何真实作业。`;
    } });
    let uploadSnapshot = { generation: 0, items: [] }, fileIdentity = 0;
    const uploads = upload.bindUpload(document.getElementById('preview-upload'), { snapshot: uploadSnapshot,
        onFiles: (_files, context) => {
            if (!context.isCurrent()) return;
            const additions = context.files.map(file => ({ id: `local-${++fileIdentity}`, generation: 1, name: file.name, sizeLabel: `${file.size} 字节`, state: 'selected' }));
            uploadSnapshot = { generation: uploadSnapshot.generation + 1, items: [...uploadSnapshot.items, ...additions] };
            uploads.update(uploadSnapshot);
        },
        onAction: context => {
            if (context.action !== 'remove' || !context.isCurrent()) return false;
            uploadSnapshot = { generation: uploadSnapshot.generation + 1, items: uploadSnapshot.items.filter(item => item.id !== context.id) };
            uploads.update(uploadSnapshot);
        },
    });
    const saveDemo = statuses.saveStatus(document.getElementById('preview-save-status'));
    document.getElementById('preview-save-state').addEventListener('change', event => {
        const state = event.target.value;
        saveDemo.set(state, { ...(['error', 'conflict'].includes(state) ? { action: { label: '重新核对', attrs: { 'data-demo-status-check': state } } } : {}) });
    });
    preview.addEventListener('click', event => {
        if (event.target.closest('[data-demo-status-check]')) preview.querySelector('[data-lq-demo-save-feedback]').textContent = '已演示重新核对入口；本地输入保持不变，未发送写入请求。';
    });
    const form = preview.querySelector('[data-lq-demo-form]');
    const formStatus = preview.querySelector('[data-lq-demo-form-status]');
    form?.addEventListener('submit', event => {
        event.preventDefault();
        formStatus.textContent = `填写检查通过：${new FormData(form).get('name')}。这是本地预览，未保存到服务器。`;
    });
    form?.addEventListener('reset', () => { formStatus.textContent = '已恢复示例内容。'; });
    const status = preview.querySelector('[data-lq-demo-dialog-status]');
    preview.addEventListener('click', event => {
        const notice = event.target.closest('[data-lq-demo-toast]');
        if (!notice) return;
        const kind = notice.dataset.lqDemoToast;
        const message = kind === 'danger' ? '这是需要处理的通知示例，关闭后不会修改任何数据。' : kind === 'action' ? '已准备一份本地示例，可以选择查看。' : '预览操作已完成。';
        void api.toast(message, { tone: kind === 'action' ? 'info' : kind,
            ...(kind === 'action' ? { action: { label: '查看示例', onClick: () => { status.textContent = '已查看通知中的示例。'; } } } : {})
        }).catch(() => { status.textContent = '通知暂时无法显示，请稍后重试。'; });
    });
    preview.addEventListener('click', async event => {
        const button = event.target.closest('[data-lq-demo-dialog],[data-lq-demo-confirm],[data-lq-demo-choose],[data-lq-demo-dirty]');
        if (!button || button.dataset.lqDisabled === 'true') return;
        button.dataset.lqDisabled = 'true';
        button.setAttribute('aria-disabled', 'true');
        button.setAttribute('aria-busy', 'true');
        try {
            if (button.hasAttribute('data-lq-demo-confirm')) {
                const confirmed = await api.confirm({ title: '确认本次预览操作', message: '此操作只演示确认流程，不会修改业务数据。', confirmLabel: '确认预览' }, { returnFocus: button });
                status.textContent = confirmed ? '已确认预览操作。' : '已取消预览操作。';
            } else if (button.hasAttribute('data-lq-demo-choose')) {
                const result = await api.choose({ title: '选择一种后续操作', message: '返回或 Esc 会明确取消选择。', choices: [{ value: 'continue', label: '继续编辑' }, { value: 'copy', label: '保留副本' }, { value: 'discard', label: '放弃示例', danger: true }] }, { returnFocus: button });
                const names = { continue: '继续编辑', copy: '保留副本', discard: '放弃示例' };
                status.textContent = result.status === 'chosen' ? `已选择：${names[result.value]}。` : '已返回，未执行选项。';
            } else if (button.hasAttribute('data-lq-demo-dirty')) {
                const [dialogs, guards] = await Promise.all([api.load('dialogs'), api.load('dirtyGuard')]);
                const initial = '修改这里的内容，再尝试关闭。';
                const body = forms.textarea({ id: 'preview-guarded-input', label: '受保护的示例草稿', value: initial });
                const input = body.querySelector('textarea');
                const root = dialogs.createDialog({ title: '离开保护示例', body });
                const guard = guards.bindDirtyGuard(root, { isDirty: () => input.value !== initial });
                await new Promise(resolve => {
                    const finish = () => { guard.destroy(); status.textContent = '已关闭离开保护示例。'; resolve(); };
                    dialogs.openDialog(root, { trigger: button, returnFocus: button, beforeClose: guard.beforeClose, onClose: finish, onDestroy: finish });
                    guard.refresh();
                });
            } else {
                const dialogs = await api.load('dialogs');
                const type = button.dataset.lqDemoDialog;
                const body = document.createElement('div');
                body.append(forms.textarea({ id: `preview-${type}-draft`, label: '弹层内输入', value: '可以输入内容，随后打开子确认。', autoGrow: true }));
                const nested = api.button({ label: '打开子确认', variant: 'soft' });
                body.append(nested);
                nested.addEventListener('click', async () => {
                    const confirmed = await api.confirm({ title: '子确认', message: 'Esc 先关闭当前确认，再返回上一层。' }, { returnFocus: nested });
                    status.textContent = confirmed ? '已确认子操作。' : '子操作已取消。';
                });
                await new Promise(resolve => {
                    dialogs.openDialog({ title: '弹层示例', type, body }, { anchor: type === 'popover' ? button : undefined, returnFocus: button,
                        onClose: () => { status.textContent = '弹层已关闭。'; resolve(); }, onDestroy: resolve });
                });
            }
        } catch (error) { status.textContent = `预览暂不可用：${error.message}`; }
        finally {
            delete button.dataset.lqDisabled;
            button.removeAttribute('aria-disabled'); button.removeAttribute('aria-busy');
            if (document.activeElement === document.body) button.focus();
        }
    });
    preview.dataset.lqInteractionsReady = 'true';
}).catch(error => {
    const preview = document.querySelector('[data-lq-preview]');
    if (preview) preview.dataset.lqInteractionsReady = 'error';
    const status = preview?.querySelector('[data-lq-demo-dialog-status]');
    if (status) status.textContent = `部分预览暂不可用：${error.message}`;
});
